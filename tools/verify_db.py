"""Quick post-build verification of the local MP index.

Run after ``build_index.py`` finishes::

    D:/miniconda3/envs/chem_env/python.exe tools/verify_db.py
"""
from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

import cif_tools  # noqa: E402
import mp_search  # noqa: E402


def main() -> int:
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else mp_search.DEFAULT_DB
    if not db.is_file():
        print(f"[x] 未找到 {db}")
        return 1
    stats = mp_search.database_stats(db)
    print("数据库统计：")
    for key, value in stats.items():
        print(f"  {key:16} {value}")

    checks = [
        ("MP-ID", "mp-149", "auto"),
        ("化学式归一化", "NiOOH", "auto"),
        ("元素组合", "Si O", "auto"),
        ("化学体系", "Li-Fe-P", "auto"),
        ("空间群符号", "Fm-3m", "auto"),
        ("空间群编号", "225", "auto"),
        ("批量：TiO2 精确", "TiO2", "formula"),
    ]
    print("\n检索类别：")
    for name, query, mode in checks:
        start = time.perf_counter()
        result = mp_search.search(db, query, mode=mode, size=3)
        elapsed = (time.perf_counter() - start) * 1000
        sample = ", ".join(row["material_id"] for row in result["results"][:3])
        print(f"  {name:14} {query:10} -> kind={result['spec']['kind']:10} "
              f"total={result['total_count']:>7} [{sample}] {elapsed:.0f} ms")

    print("\n筛选：")
    filters = [
        ("仅稳定相", {"stable_only": True}),
        ("带隙 1.0-2.0 eV", {"band_gap_min": 1.0, "band_gap_max": 2.0}),
        ("晶格 4-6 Å", {"lat_min": 4.0, "lat_max": 6.0}),
        ("含 Li 且不含 S", {"el_inc": "Li", "el_exc": "S"}),
        ("立方晶系+金属", {"crystal_system": "Cubic", "metal_only": True}),
        ("空间群 Pnma", {"sg": "Pnma"}),
    ]
    for name, flt in filters:
        start = time.perf_counter()
        result = mp_search.search(db, "", filters=flt, size=1)
        elapsed = (time.perf_counter() - start) * 1000
        print(f"  {name:16} total={result['total_count']:>7}  {elapsed:.0f} ms")

    if stats.get("structures"):
        row = mp_search.search(db, "mp-149", size=1)["results"]
        material_id = row[0]["material_id"] if row else "mp-149"
        raw = mp_search.get_structure_bytes(db, material_id)
        start = time.perf_counter()
        bundle = cif_tools.cif_bundle(raw, supercell=1, pbc=True, with_xrd=True)
        elapsed = (time.perf_counter() - start) * 1000
        print(f"\nCIF 生成 {material_id}: atoms={bundle['info']['atoms']} "
              f"pbc_added={bundle['pbc_added']} {elapsed:.0f} ms")
        print("  首行:", bundle["cif"].splitlines()[1] if bundle["cif"] else "(空)")
        pattern = bundle.get("xrd")
        if pattern:
            strongest = max(pattern["peaks"], key=lambda peak: peak["intensity"])
            print(f"XRD {material_id}: {pattern['formula']} · {pattern['wavelength_label']} · "
                  f"{len(pattern['peaks'])} peaks · 最强 {strongest['two_theta']:.2f}° "
                  f"I={strongest['intensity']:.1f} {strongest['hkl']}")
        else:
            print(f"[!] {material_id} 未能生成 XRD 图谱（已跳过）")

    print("\n[ok] 验证完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
