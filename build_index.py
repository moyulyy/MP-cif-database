"""Build the local SQLite search index (``mp.db``) from the downloaded MP shards.

Streaming ETL, safe to re-run (DROP + recreate).  Mirrors the design described in
``cif-database-描述.md``: flatten every searchable field into ``materials``, store
the pymatgen structure dict as a zlib blob in ``structures``, keep a slim
``material_elements`` table for element include/exclude queries, and a
``formula_keys`` table with Hill-normalised composition keys.

Usage::

    python build_index.py                       # src=data/mp_open_data, out=data/mp_open_data/mp.db
    python build_index.py --src data/mp_open_data --out data/mp_open_data/mp.db
    python build_index.py --limit 2000          # quick smoke build
"""
from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import sys
import time
import zlib
from pathlib import Path

from mp_formula import formula_key

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent
DEFAULT_SRC = ROOT / "data" / "mp_open_data"
DEFAULT_OUT = ROOT / "data" / "mp_open_data" / "mp.db"

SCHEMA = """
CREATE TABLE materials (
    material_id               TEXT PRIMARY KEY,
    formula_pretty            TEXT NOT NULL,
    formula_anonymous         TEXT,
    chemsys                   TEXT,
    nelements                 INTEGER,
    nsites                    INTEGER,
    volume                    REAL,
    density                   REAL,
    density_atomic            REAL,
    crystal_system            TEXT,
    point_group               TEXT,
    sg_symbol                 TEXT,
    sg_number                 INTEGER,
    lat_a                     REAL,
    lat_b                     REAL,
    lat_c                     REAL,
    alpha                     REAL,
    beta                      REAL,
    gamma                     REAL,
    band_gap                  REAL,
    is_metal                  INTEGER,
    is_gap_direct             INTEGER,
    magnetic_ordering         TEXT,
    is_stable                 INTEGER,
    energy_above_hull         REAL,
    formation_energy_per_atom REAL,
    deprecated                INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE structures (
    material_id TEXT PRIMARY KEY,
    z           BLOB NOT NULL
);
CREATE TABLE material_elements (
    material_id TEXT NOT NULL,
    element     TEXT NOT NULL
);
CREATE TABLE formula_keys (
    material_id TEXT PRIMARY KEY,
    formula_key TEXT NOT NULL
);
"""

INDEXES = """
CREATE INDEX idx_mat_formula  ON materials(formula_pretty);
CREATE INDEX idx_mat_chemsys  ON materials(chemsys);
CREATE INDEX idx_mat_sg       ON materials(sg_symbol COLLATE NOCASE, sg_number);
CREATE INDEX idx_mat_sgno     ON materials(sg_number);
CREATE INDEX idx_mat_lat_a    ON materials(lat_a);
CREATE INDEX idx_mat_system   ON materials(crystal_system COLLATE NOCASE);
CREATE INDEX idx_mat_order    ON materials(deprecated, is_stable DESC, energy_above_hull, nsites);
CREATE INDEX idx_mat_bandgap  ON materials(band_gap);
CREATE INDEX idx_el_element   ON material_elements(element, material_id);
CREATE INDEX idx_el_mat       ON material_elements(material_id);
CREATE INDEX idx_fkey_formula ON formula_keys(formula_key);
"""

PRAGMAS = """
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;
PRAGMA cache_size  = -64000;
PRAGMA temp_store  = FILE;
"""


def _shards(root: Path, subdir: str) -> list[Path]:
    base = root / "collections"
    if not base.is_dir():
        base = root
    return sorted(p for p in base.rglob("*.jsonl.gz") if subdir in p.as_posix())


def _thermo_shards(root: Path) -> list[Path]:
    base = root / "collections"
    if not base.is_dir():
        base = root
    # Exact directory component: exclude other thermo types such as GGA_GGA+U_R2SCAN.
    found = [p for p in base.rglob("*.jsonl.gz") if "/thermo_type=GGA_GGA+U/" in p.as_posix()]
    return sorted(found)


def iter_rows(path: Path):
    """Yield dict rows from a gzip jsonl file; skip malformed lines."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except (OSError, EOFError):
        return


def _as_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None  # drop NaN


def load_materials(conn: sqlite3.Connection, root: Path, limit: int | None,
                   progress) -> int:
    count = 0
    batch = 0
    for shard in _shards(root, "materials"):
        for row in iter_rows(shard):
            material_id = row.get("material_id")
            if not material_id:
                continue
            structure = row.get("structure") or {}
            lattice = structure.get("lattice") or {}
            symmetry = row.get("symmetry") or {}
            values = (
                material_id,
                row.get("formula_pretty") or "",
                row.get("formula_anonymous"),
                row.get("chemsys"),
                _as_int(row.get("nelements")),
                _as_int(row.get("nsites")),
                _as_float(row.get("volume")),
                _as_float(row.get("density")),
                _as_float(row.get("density_atomic")),
                symmetry.get("crystal_system"),
                symmetry.get("point_group"),
                symmetry.get("symbol"),
                _as_int(symmetry.get("number")),
                _as_float(lattice.get("a")),
                _as_float(lattice.get("b")),
                _as_float(lattice.get("c")),
                _as_float(lattice.get("alpha")),
                _as_float(lattice.get("beta")),
                _as_float(lattice.get("gamma")),
                _as_float(row.get("band_gap")),
                _as_int(row.get("is_metal")),
                _as_int(row.get("is_gap_direct")),
                row.get("magnetic_ordering"),
                _as_int(row.get("is_stable")),
                _as_float(row.get("energy_above_hull")),
                _as_float(row.get("formation_energy_per_atom")),
                1 if row.get("deprecated") else 0,
            )
            conn.execute(
                "INSERT OR REPLACE INTO materials VALUES (" + ",".join(["?"] * 27) + ")",
                values,
            )
            if structure:
                try:
                    blob = zlib.compress(json.dumps(structure, separators=(",", ":")).encode("utf-8"), 6)
                    conn.execute("INSERT OR REPLACE INTO structures VALUES (?,?)", (material_id, blob))
                except (TypeError, ValueError):
                    pass
            for element in row.get("elements") or []:
                conn.execute("INSERT INTO material_elements VALUES (?,?)", (material_id, element))
            count += 1
            batch += 1
            if batch >= 5000:
                conn.commit()
                batch = 0
                progress("材料", count)
            if limit and count >= limit:
                conn.commit()
                progress("材料", count)
                return count
    conn.commit()
    progress("材料", count)
    return count


def load_thermo(conn: sqlite3.Connection, root: Path, limit: int | None, progress) -> int:
    count = 0
    for shard in _thermo_shards(root):
        rows = []
        for row in iter_rows(shard):
            material_id = row.get("material_id")
            if not material_id:
                continue
            rows.append((
                _as_float(row.get("energy_above_hull")),
                _as_float(row.get("formation_energy_per_atom")),
                _as_int(row.get("is_stable")),
                material_id,
            ))
        if rows:
            conn.executemany(
                "UPDATE materials SET energy_above_hull=?, formation_energy_per_atom=?, is_stable=? "
                "WHERE material_id=?", rows)
        conn.commit()
        count += len(rows)
        progress("热力学", count)
        if limit and count >= limit:
            return count
    return count


def load_electronic(conn: sqlite3.Connection, root: Path, limit: int | None, progress) -> int:
    count = 0
    for shard in _shards(root, "electronic-structure"):
        rows = []
        for row in iter_rows(shard):
            material_id = row.get("material_id")
            if not material_id:
                continue
            rows.append((
                _as_float(row.get("band_gap")),
                _as_int(row.get("is_metal")),
                _as_int(row.get("is_gap_direct")),
                row.get("magnetic_ordering"),
                material_id,
            ))
        if rows:
            conn.executemany(
                "UPDATE materials SET band_gap=?, is_metal=?, is_gap_direct=?, magnetic_ordering=? "
                "WHERE material_id=?", rows)
        conn.commit()
        count += len(rows)
        progress("电子结构", count)
        if limit and count >= limit:
            return count
    return count


def populate_formula_keys(conn: sqlite3.Connection, limit: int | None, progress) -> int:
    cur = conn.execute("SELECT material_id, formula_pretty FROM materials WHERE deprecated=0")
    count = 0
    batch = 0
    while True:
        rows = cur.fetchmany(20000)
        if not rows:
            break
        payload = []
        for material_id, pretty in rows:
            key = formula_key(pretty)
            if key:
                payload.append((material_id, key))
        if payload:
            conn.executemany("INSERT OR REPLACE INTO formula_keys VALUES (?,?)", payload)
        conn.commit()
        count += len(rows)
        batch += len(rows)
        progress("化学式键", count)
        if limit and count >= limit:
            break
    return count


def build(src: Path, out: Path, limit: int | None = None, progress=None, log=print) -> dict:
    if progress is None:
        progress = lambda phase, done: None  # noqa: E731
    src = Path(src)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    conn = sqlite3.connect(str(out))
    start = time.time()
    try:
        conn.executescript(PRAGMAS)
        conn.executescript(SCHEMA)
        log("阶段 1/4：载入 materials …")
        materials = load_materials(conn, src, limit, progress)
        log("阶段 2/4：合并 thermo …")
        thermo = load_thermo(conn, src, limit, progress)
        log("阶段 3/4：合并 electronic-structure …")
        electronic = load_electronic(conn, src, limit, progress)
        log("阶段 4/4：生成归一化化学式键 …")
        keys = populate_formula_keys(conn, limit, progress)
        log("建立索引并 ANALYZE …")
        conn.executescript(INDEXES)
        conn.execute("ANALYZE")
        conn.commit()
        stats = {
            "materials": materials,
            "thermo_rows": thermo,
            "electronic_rows": electronic,
            "formula_keys": keys,
            "elapsed_sec": round(time.time() - start, 1),
            "db_bytes": out.stat().st_size if out.exists() else 0,
        }
    finally:
        conn.close()
    log(f"建库完成：{stats}")
    return stats


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="从 MP Open Data 分片构建本地 SQLite 索引")
    parser.add_argument("--src", default=str(DEFAULT_SRC), help="下载数据根目录")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出 mp.db 路径")
    parser.add_argument("--limit", type=int, default=None, help="每阶段最多处理多少行（调试用）")
    args = parser.parse_args(argv)
    build(Path(args.src), Path(args.out), args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
