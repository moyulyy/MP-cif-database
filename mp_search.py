"""Read-only search layer over the local ``mp.db`` index.

Supports the search categories:
    * MP-ID            ``mp-149``
    * formula          ``TiO2`` / ``NiOOH`` (Hill-normalised exact match, LIKE fallback)
    * element combo    ``Ti O`` / ``Ti,O`` / ``Ti，O``  (contains all listed elements)
    * chemical system  ``Ti-O`` / ``Li-Fe-P``
    * space group      ``225`` / ``Fm-3m``

plus server-side filters (space group, crystal system, lattice, band gap,
stability / hull energy, formation energy, sites, element include/exclude,
metal / magnetic) and a handful of sort orders.
"""
from __future__ import annotations

import re
import sqlite3
import urllib.parse
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from mp_formula import element_symbol, formula_key

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "mp_open_data" / "mp.db"

MP_ID_RE = re.compile(r"^mp-\d+$", re.I)
FORMULA_RE = re.compile(r"^[A-Za-z0-9().\[\]\s]+$")
TOKEN_SPLIT_RE = re.compile(r"[,，、;；\s\-–—]+")
CHEMSYS_SPLIT_RE = re.compile(r"[-–—\s]+")

_SG_CACHE: dict[str, set[str]] = {}

SORT_ORDERS = {
    "default": "(m.energy_above_hull IS NULL), m.is_stable DESC, m.energy_above_hull ASC, m.nsites ASC, m.material_id ASC",
    "hull": "(m.energy_above_hull IS NULL), m.energy_above_hull ASC, m.material_id ASC",
    "bandgap_asc": "(m.band_gap IS NULL), m.band_gap ASC, m.material_id ASC",
    "bandgap_desc": "(m.band_gap IS NULL), m.band_gap DESC, m.material_id ASC",
    "eform": "(m.formation_energy_per_atom IS NULL), m.formation_energy_per_atom ASC, m.material_id ASC",
    "density": "(m.density IS NULL), m.density DESC, m.material_id ASC",
    "nsites": "m.nsites ASC, m.material_id ASC",
    "volume": "(m.volume IS NULL), m.volume ASC, m.material_id ASC",
}

RESULT_COLUMNS = (
    "material_id", "formula_pretty", "formula_anonymous", "chemsys", "nelements", "nsites",
    "volume", "density", "crystal_system", "point_group", "sg_symbol", "sg_number",
    "lat_a", "lat_b", "lat_c", "alpha", "beta", "gamma",
    "band_gap", "is_metal", "is_gap_direct", "magnetic_ordering",
    "is_stable", "energy_above_hull", "formation_energy_per_atom",
)


# --------------------------------------------------------------------------- #
# Query parsing
# --------------------------------------------------------------------------- #
@dataclass
class QuerySpec:
    kind: str            # mpid | formula | elements | chemsys | spacegroup | empty | text
    value: object        # str | list[str]
    raw: str
    note: str = ""


def parse_query(text: str, mode: str = "auto") -> QuerySpec:
    raw = (text or "").strip()
    if not raw:
        return QuerySpec("empty", "", raw)
    if mode in ("mpid", "mp-id"):
        return QuerySpec("mpid", raw.lower(), raw)
    if mode == "elements":
        symbols = _parse_element_tokens(raw)
        if symbols:
            return QuerySpec("elements", symbols, raw)
        return QuerySpec("text", raw, raw, "未能识别元素符号，按化学式处理")
    if mode == "chemsys":
        symbols = _parse_element_tokens(raw, split=CHEMSYS_SPLIT_RE)
        if symbols:
            return QuerySpec("chemsys", "-".join(sorted(symbols)), raw)
        return QuerySpec("text", raw, raw, "未能识别元素符号，按化学式处理")
    if mode == "spacegroup":
        return QuerySpec("spacegroup", raw, raw)
    if mode == "formula":
        return QuerySpec("formula", raw, raw)

    # auto
    if MP_ID_RE.match(raw):
        return QuerySpec("mpid", raw.lower(), raw)
    if "-" in raw:
        symbols = _parse_element_tokens(raw, split=CHEMSYS_SPLIT_RE)
        if symbols and len(symbols) >= 1 and _looks_like_chemsys(raw):
            return QuerySpec("chemsys", "-".join(sorted(symbols)), raw)
    symbols = _parse_element_tokens(raw)
    if symbols and len(symbols) >= 2:
        return QuerySpec("elements", symbols, raw)
    if raw.isdigit():
        return QuerySpec("spacegroup", raw, raw)
    return QuerySpec("formula", raw, raw)


def _looks_like_chemsys(raw: str) -> bool:
    # "Ti-O" style: only element letters and separators, no digits or parentheses.
    return bool(re.fullmatch(r"[A-Za-z\-–—\s]+", raw)) and "-" in raw


def _parse_element_tokens(text: str, split=TOKEN_SPLIT_RE) -> list[str]:
    tokens = [t for t in split.split(text) if t]
    symbols: list[str] = []
    for token in tokens:
        symbol = element_symbol(token)
        if symbol is None:
            return []
        if symbol not in symbols:
            symbols.append(symbol)
    return symbols


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #
@dataclass
class SearchFilters:
    sg: list[str] = field(default_factory=list)
    crystal_system: str | None = None
    el_inc: list[str] = field(default_factory=list)
    el_exc: list[str] = field(default_factory=list)
    lat_min: float | None = None
    lat_max: float | None = None
    band_gap_min: float | None = None
    band_gap_max: float | None = None
    energy_max: float | None = None
    eform_min: float | None = None
    eform_max: float | None = None
    nsites_min: int | None = None
    nsites_max: int | None = None
    nelements_min: int | None = None
    nelements_max: int | None = None
    stable_only: bool = False
    metal_only: bool = False

    @property
    def active(self) -> bool:
        return any([
            self.sg, self.crystal_system, self.el_inc, self.el_exc,
            self.lat_min is not None, self.lat_max is not None,
            self.band_gap_min is not None, self.band_gap_max is not None,
            self.energy_max is not None, self.eform_min is not None, self.eform_max is not None,
            self.nsites_min is not None, self.nsites_max is not None,
            self.nelements_min is not None, self.nelements_max is not None,
            self.stable_only, self.metal_only,
        ])

    def to_query(self) -> dict:
        out: dict[str, object] = {}
        for key in ("sg", "el_inc", "el_exc"):
            value = getattr(self, key)
            if value:
                out[key] = ",".join(value)
        for key in ("crystal_system", "lat_min", "lat_max", "band_gap_min", "band_gap_max",
                    "energy_max", "eform_min", "eform_max", "nsites_min", "nsites_max",
                    "nelements_min", "nelements_max"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.stable_only:
            out["stable_only"] = "1"
        if self.metal_only:
            out["metal_only"] = "1"
        return out


def _to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int | None:
    number = _to_float(value)
    return None if number is None else int(number)


def normalize_filters(params: dict | None) -> SearchFilters:
    params = params or {}
    filters = SearchFilters()
    sg_value = params.get("sg")
    if sg_value:
        if isinstance(sg_value, (list, tuple)):
            items = sg_value
        else:
            items = re.split(r"[,，;；\s]+", str(sg_value))
        filters.sg = [item.strip() for item in items if item.strip()]
    filters.crystal_system = (params.get("crystal_system") or None) or None
    for key in ("el_inc", "el_exc"):
        value = params.get(key)
        if not value:
            continue
        items = value if isinstance(value, (list, tuple)) else re.split(r"[,，;；\s]+", str(value))
        symbols = []
        for item in items:
            symbol = element_symbol(item)
            if symbol is None:
                raise ValueError(f"非法元素符号：{item}")
            if symbol not in symbols:
                symbols.append(symbol)
        setattr(filters, key, symbols)
    filters.lat_min = _to_float(params.get("lat_min"))
    filters.lat_max = _to_float(params.get("lat_max"))
    filters.band_gap_min = _to_float(params.get("band_gap_min"))
    filters.band_gap_max = _to_float(params.get("band_gap_max"))
    filters.energy_max = _to_float(params.get("energy_max"))
    filters.eform_min = _to_float(params.get("eform_min"))
    filters.eform_max = _to_float(params.get("eform_max"))
    filters.nsites_min = _to_int(params.get("nsites_min"))
    filters.nsites_max = _to_int(params.get("nsites_max"))
    filters.nelements_min = _to_int(params.get("nelements_min"))
    filters.nelements_max = _to_int(params.get("nelements_max"))
    filters.stable_only = str(params.get("stable_only", "")).lower() in ("1", "true", "yes", "on")
    filters.metal_only = str(params.get("metal_only", "")).lower() in ("1", "true", "yes", "on")
    if filters.lat_min is not None and filters.lat_min <= 0:
        raise ValueError("晶格下限必须大于 0")
    if filters.lat_max is not None and filters.lat_max <= 0:
        raise ValueError("晶格上限必须大于 0")
    if filters.lat_min is not None and filters.lat_max is not None and filters.lat_min > filters.lat_max:
        raise ValueError("晶格区间下限不能大于上限")
    return filters


# --------------------------------------------------------------------------- #
# SQL construction
# --------------------------------------------------------------------------- #
def _sg_symbol_set(conn: sqlite3.Connection, db_key: str) -> set[str]:
    cached = _SG_CACHE.get(db_key)
    if cached is not None:
        return cached
    try:
        rows = conn.execute(
            "SELECT DISTINCT sg_symbol FROM materials WHERE sg_symbol IS NOT NULL").fetchall()
        symbols = {row[0].strip().lower() for row in rows if row[0]}
    except sqlite3.Error:
        symbols = set()
    _SG_CACHE[db_key] = symbols
    return symbols


def _where_for_spec(spec: QuerySpec) -> tuple[list[str], list]:
    clauses: list[str] = []
    params: list = []
    if spec.kind == "mpid":
        clauses.append("m.material_id = ?")
        params.append(spec.value)
    elif spec.kind == "chemsys":
        clauses.append("m.chemsys = ?")
        params.append(spec.value)
    elif spec.kind == "elements":
        symbols = spec.value
        placeholders = ",".join(["?"] * len(symbols))
        clauses.append(
            "m.material_id IN (SELECT material_id FROM material_elements "
            f"WHERE element IN ({placeholders}) GROUP BY material_id HAVING COUNT(DISTINCT element) = ?)"
        )
        params.extend(symbols)
        params.append(len(symbols))
    elif spec.kind == "spacegroup":
        value = str(spec.value).strip()
        if value.isdigit():
            clauses.append("m.sg_number = ?")
            params.append(int(value))
        else:
            clauses.append("m.sg_symbol = ? COLLATE NOCASE")
            params.append(value)
    elif spec.kind == "text":
        clauses.append("m.formula_pretty LIKE ?")
        params.append(f"%{spec.value}%")
    return clauses, params


def _where_for_filters(filters: SearchFilters) -> tuple[list[str], list]:
    clauses: list[str] = []
    params: list = []
    if filters.sg:
        parts = []
        for item in filters.sg:
            if item.isdigit():
                parts.append("m.sg_number = ?")
                params.append(int(item))
            else:
                parts.append("m.sg_symbol = ? COLLATE NOCASE")
                params.append(item)
        clauses.append("(" + " OR ".join(parts) + ")")
    if filters.crystal_system:
        clauses.append("m.crystal_system = ? COLLATE NOCASE")
        params.append(filters.crystal_system)
    if filters.el_inc:
        placeholders = ",".join(["?"] * len(filters.el_inc))
        clauses.append(
            "m.material_id IN (SELECT material_id FROM material_elements "
            f"WHERE element IN ({placeholders}) GROUP BY material_id HAVING COUNT(DISTINCT element) = ?)"
        )
        params.extend(filters.el_inc)
        params.append(len(filters.el_inc))
    for symbol in filters.el_exc:
        clauses.append(
            "m.material_id NOT IN (SELECT material_id FROM material_elements WHERE element = ?)")
        params.append(symbol)
    if filters.lat_min is not None:
        clauses.append("m.lat_a >= ? AND m.lat_b >= ? AND m.lat_c >= ?")
        params.extend([filters.lat_min] * 3)
    if filters.lat_max is not None:
        clauses.append("m.lat_a <= ? AND m.lat_b <= ? AND m.lat_c <= ?")
        params.extend([filters.lat_max] * 3)
    if filters.band_gap_min is not None:
        clauses.append("m.band_gap >= ?")
        params.append(filters.band_gap_min)
    if filters.band_gap_max is not None:
        clauses.append("m.band_gap <= ?")
        params.append(filters.band_gap_max)
    if filters.energy_max is not None:
        clauses.append("m.energy_above_hull <= ?")
        params.append(filters.energy_max)
    if filters.eform_min is not None:
        clauses.append("m.formation_energy_per_atom >= ?")
        params.append(filters.eform_min)
    if filters.eform_max is not None:
        clauses.append("m.formation_energy_per_atom <= ?")
        params.append(filters.eform_max)
    if filters.nsites_min is not None:
        clauses.append("m.nsites >= ?")
        params.append(filters.nsites_min)
    if filters.nsites_max is not None:
        clauses.append("m.nsites <= ?")
        params.append(filters.nsites_max)
    if filters.nelements_min is not None:
        clauses.append("m.nelements >= ?")
        params.append(filters.nelements_min)
    if filters.nelements_max is not None:
        clauses.append("m.nelements <= ?")
        params.append(filters.nelements_max)
    if filters.stable_only:
        clauses.append("m.is_stable = 1")
    if filters.metal_only:
        clauses.append("m.is_metal = 1")
    return clauses, params


def _formula_clause(value: str) -> tuple[str, list]:
    key = formula_key(value)
    if key:
        return ("(m.formula_pretty = ? OR m.material_id IN "
                "(SELECT material_id FROM formula_keys WHERE formula_key = ?))",
                [value, key])
    return ("m.formula_pretty LIKE ?", [f"%{value}%"])


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
def _open(db_path: Path) -> sqlite3.Connection:
    uri = "file:{}?mode=ro".format(urllib.parse.quote(str(Path(db_path).resolve()), safe="/:\\"))
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_result(row: sqlite3.Row) -> dict:
    return {
        "material_id": row["material_id"],
        "formula": row["formula_pretty"],
        "formula_anonymous": row["formula_anonymous"],
        "chemsys": row["chemsys"],
        "nelements": row["nelements"],
        "nsites": row["nsites"],
        "volume": row["volume"],
        "density": row["density"],
        "crystal_system": row["crystal_system"],
        "spacegroup": row["sg_symbol"],
        "spacegroup_number": row["sg_number"],
        "point_group": row["point_group"],
        "lattice": [row["lat_a"], row["lat_b"], row["lat_c"]],
        "angles": [row["alpha"], row["beta"], row["gamma"]],
        "band_gap": row["band_gap"],
        "is_metal": None if row["is_metal"] is None else bool(row["is_metal"]),
        "is_gap_direct": None if row["is_gap_direct"] is None else bool(row["is_gap_direct"]),
        "magnetic_ordering": row["magnetic_ordering"],
        "is_stable": None if row["is_stable"] is None else bool(row["is_stable"]),
        "energy_above_hull": row["energy_above_hull"],
        "formation_energy_per_atom": row["formation_energy_per_atom"],
    }


def search(db_path: Path | str = DEFAULT_DB, q: str = "", mode: str = "auto",
           filters: dict | SearchFilters | None = None, page: int = 1, size: int = 24,
           sort: str = "default") -> dict:
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"未找到本地索引库：{db_path}")
    if isinstance(filters, SearchFilters):
        active = filters
    else:
        active = normalize_filters(filters)
    spec = parse_query(q, mode)

    page = max(1, int(page))
    size = max(1, min(200, int(size)))
    order = SORT_ORDERS.get(sort, SORT_ORDERS["default"])

    fclauses, fparams = _where_for_filters(active)
    base = ["m.deprecated = 0"]

    conn = _open(db_path)
    try:
        # In auto mode a bare token like "Pnma" or "Fm-3m" that is a real space-group
        # symbol in this database is treated as a space-group query rather than a formula.
        if mode == "auto" and spec.kind == "formula":
            token = str(spec.value).strip()
            if token.lower() in _sg_symbol_set(conn, str(db_path.resolve())):
                spec = QuerySpec("spacegroup", token, spec.raw)
        clauses, params = _where_for_spec(spec)
        clauses.extend(fclauses)
        params.extend(fparams)
        where = " AND ".join(base + clauses)
        exact_used = True
        fallback = False
        if spec.kind == "formula":
            clause, fparams2 = _formula_clause(str(spec.value))
            query_where = " AND ".join(base + [clause] + fclauses)
            query_params = list(fparams2) + list(fparams)
            total = conn.execute(
                f"SELECT COUNT(*) FROM materials m WHERE {query_where}", query_params).fetchone()[0]
            if total == 0 and str(spec.value).strip():
                # Only when the exact/normalised match finds nothing do we fall back to LIKE.
                query_where = " AND ".join(base + ["m.formula_pretty LIKE ?"] + fclauses)
                query_params = [f"%{spec.value}%"] + list(fparams)
                total = conn.execute(
                    f"SELECT COUNT(*) FROM materials m WHERE {query_where}", query_params).fetchone()[0]
                fallback = total > 0
                exact_used = not fallback
            where, params = query_where, query_params
        else:
            total = conn.execute(
                f"SELECT COUNT(*) FROM materials m WHERE {where}", params).fetchone()[0]

        offset = (page - 1) * size
        rows = conn.execute(
            f"SELECT m.{', m.'.join(RESULT_COLUMNS)} FROM materials m "
            f"WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [size, offset]).fetchall()
        results = [row_to_result(row) for row in rows]
    finally:
        conn.close()

    total_pages = max(1, (total + size - 1) // size)
    return {
        "source": "local",
        "query": q,
        "mode": mode,
        "spec": {"kind": spec.kind, "value": spec.value, "note": spec.note},
        "page": page,
        "size": size,
        "sort": sort,
        "total_count": total,
        "total_pages": total_pages,
        "count": len(results),
        "results": results,
        "filters": active.to_query(),
        "formula_fallback_like": (spec.kind == "formula" and not exact_used),
    }


def get_material(db_path: Path | str, material_id: str) -> dict | None:
    db_path = Path(db_path)
    conn = _open(db_path)
    try:
        row = conn.execute(
            f"SELECT m.{', m.'.join(RESULT_COLUMNS)}, m.deprecated FROM materials m WHERE m.material_id = ?",
            (material_id,)).fetchone()
        if row is None:
            return None
        result = row_to_result(row)
        result["deprecated"] = bool(row["deprecated"])
        elements = conn.execute(
            "SELECT element FROM material_elements WHERE material_id = ? ORDER BY element",
            (material_id,)).fetchall()
        result["elements"] = [item["element"] for item in elements]
        return result
    finally:
        conn.close()


def get_structure_dict(db_path: Path | str, material_id: str) -> dict | None:
    raw = get_structure_bytes(db_path, material_id)
    if raw is None:
        return None
    import json
    return json.loads(raw.decode("utf-8"))


def get_structure_bytes(db_path: Path | str, material_id: str) -> bytes | None:
    db_path = Path(db_path)
    conn = _open(db_path)
    try:
        row = conn.execute("SELECT z FROM structures WHERE material_id = ?", (material_id,)).fetchone()
        if row is None:
            return None
        return zlib.decompress(row["z"])
    finally:
        conn.close()


def database_stats(db_path: Path | str = DEFAULT_DB) -> dict:
    db_path = Path(db_path)
    if not db_path.is_file():
        return {"exists": False, "path": str(db_path)}
    conn = _open(db_path)
    try:
        def scalar(sql: str):
            try:
                return conn.execute(sql).fetchone()[0]
            except sqlite3.Error:
                return None

        return {
            "exists": True,
            "path": str(db_path),
            "bytes": db_path.stat().st_size,
            "materials": scalar("SELECT COUNT(*) FROM materials"),
            "active": scalar("SELECT COUNT(*) FROM materials WHERE deprecated=0"),
            "structures": scalar("SELECT COUNT(*) FROM structures"),
            "stable": scalar("SELECT COUNT(*) FROM materials WHERE is_stable=1 AND deprecated=0"),
            "with_bandgap": scalar("SELECT COUNT(*) FROM materials WHERE band_gap IS NOT NULL AND deprecated=0"),
            "elements": scalar("SELECT COUNT(DISTINCT element) FROM material_elements"),
            "spacegroups": scalar("SELECT COUNT(DISTINCT sg_number) FROM materials WHERE deprecated=0"),
            "chemsys": scalar("SELECT COUNT(DISTINCT chemsys) FROM materials WHERE deprecated=0"),
        }
    finally:
        conn.close()


def distinct_values(db_path: Path | str, column: str, limit: int = 400) -> list[str]:
    allowed = {"crystal_system", "sg_symbol", "magnetic_ordering"}
    if column not in allowed:
        raise ValueError("不允许的列")
    db_path = Path(db_path)
    if not db_path.is_file():
        return []
    conn = _open(db_path)
    try:
        rows = conn.execute(
            f"SELECT DISTINCT {column} FROM materials WHERE {column} IS NOT NULL AND deprecated=0 "
            f"ORDER BY {column} LIMIT ?", (limit,)).fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()
