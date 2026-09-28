"""Self-contained tests for formula normalisation, query parsing and search.

Builds a tiny synthetic SQLite index so it does not depend on the ~1 GB download.

Run::

    D:/miniconda3/envs/chem_env/python.exe -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
import warnings
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cif_tools  # noqa: E402
import mp_search  # noqa: E402
import xrd_tools  # noqa: E402
from build_index import SCHEMA  # noqa: E402
from mp_formula import formula_key  # noqa: E402

warnings.filterwarnings("ignore")


def _structure_dict(symbols, lattice=5.0):
    from pymatgen.core import Lattice, Structure
    coords = [[(i * 0.13) % 1.0, (i * 0.29) % 1.0, (i * 0.47) % 1.0]
              for i in range(len(symbols))]
    structure = Structure(Lattice.cubic(lattice), symbols, coords)
    return structure.as_dict()


ROWS = [
    # material_id, formula, anon, chemsys, n_el, n_sites, sg, sg_no, crystal, a, b, c,
    # band_gap, metal, stable, hull, eform, dep, elements, symbols
    ("mp-149", "Si", "A", "Si", 1, 2, "Fd-3m", 227, "Cubic", 5.43, 5.43, 5.43,
     0.61, 0, 1, 0.0, -0.0, 0, ["Si"], ["Si", "Si"]),
    ("mp-2657", "TiO2", "AB2", "O-Ti", 2, 6, "P4_2/mnm", 136, "Tetragonal", 4.59, 4.59, 2.96,
     2.10, 0, 1, 0.0, -3.5, 0, ["Ti", "O"], ["Ti", "O", "O", "Ti", "O", "O"]),
    ("mp-1067482", "NiHO2", "ABC2", "H-Ni-O", 3, 4, "P-1", 2, "Triclinic", 4.1, 4.2, 5.0,
     1.50, 0, 0, 0.12, -2.0, 0, ["Ni", "H", "O"], ["Ni", "H", "O", "O"]),
    ("mp-19009", "LiFePO4", "ABCD4", "Fe-Li-O-P", 4, 28, "Pnma", 62, "Orthorhombic",
     10.3, 6.0, 4.7, 3.60, 0, 1, 0.0, -2.5, 0, ["Li", "Fe", "P", "O"], ["Li", "Fe", "P", "O"] * 7),
    ("mp-999999", "Cu", "A", "Cu", 1, 1, "Fm-3m", 225, "Cubic", 3.6, 3.6, 3.6,
     0.0, 1, 0, 0.05, 0.1, 1, ["Cu"], ["Cu"]),  # deprecated
]


def build_test_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    for (mid, formula, anon, chemsys, n_el, n_sites, sg, sg_no, system, a, b, c,
         gap, metal, stable, hull, eform, dep, elements, symbols) in ROWS:
        conn.execute(
            "INSERT INTO materials VALUES (" + ",".join(["?"] * 27) + ")",
            (mid, formula, anon, chemsys, n_el, n_sites, n_el * 10.0, 5.0, 1.0, system, "mmm",
             sg, sg_no, a, b, c, 90.0, 90.0, 90.0, gap, metal, 0, "NM", stable, hull, eform, dep))
        blob = zlib.compress(json.dumps(_structure_dict(symbols, lattice=a)).encode("utf-8"), 6)
        conn.execute("INSERT INTO structures VALUES (?,?)", (mid, blob))
        for element in elements:
            conn.execute("INSERT INTO material_elements VALUES (?,?)", (mid, element))
        if not dep:
            conn.execute("INSERT INTO formula_keys VALUES (?,?)", (mid, formula_key(formula)))
    conn.commit()
    conn.close()


class FormulaKeyTests(unittest.TestCase):
    def test_normalisation(self):
        self.assertEqual(formula_key("NiOOH"), "HNiO2")
        self.assertEqual(formula_key("NiHO2"), "HNiO2")
        self.assertEqual(formula_key("HNiO2"), "HNiO2")
        self.assertEqual(formula_key("TiO2"), formula_key("Ti2O4"))

    def test_rejects_non_formula(self):
        for token in ("", "mp-149", "Zz9Qq", "!!!", "hello world"):
            self.assertEqual(formula_key(token), "")


class QueryParsingTests(unittest.TestCase):
    def test_categories(self):
        self.assertEqual(mp_search.parse_query("mp-149").kind, "mpid")
        self.assertEqual(mp_search.parse_query("TiO2").kind, "formula")
        self.assertEqual(mp_search.parse_query("Ti O").kind, "elements")
        self.assertEqual(mp_search.parse_query("Ti,O").kind, "elements")
        self.assertEqual(mp_search.parse_query("Ti，O").kind, "elements")
        self.assertEqual(mp_search.parse_query("Ti-O").kind, "chemsys")
        self.assertEqual(mp_search.parse_query("Li-Fe-P").kind, "chemsys")
        self.assertEqual(mp_search.parse_query("225").kind, "spacegroup")

    def test_forced_modes(self):
        self.assertEqual(mp_search.parse_query("Ti-O", "elements").kind, "elements")
        self.assertEqual(mp_search.parse_query("Fm-3m", "spacegroup").kind, "spacegroup")


class SearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "test.db"
        build_test_db(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_deprecated_excluded(self):
        result = mp_search.search(self.db, "", size=50)
        ids = {row["material_id"] for row in result["results"]}
        self.assertNotIn("mp-999999", ids)
        self.assertEqual(result["total_count"], 4)

    def test_mp_id(self):
        result = mp_search.search(self.db, "mp-149")
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["results"][0]["formula"], "Si")

    def test_formula_and_normalisation(self):
        for query in ("NiOOH", "NiHO2", "HNiO2"):
            result = mp_search.search(self.db, query)
            self.assertEqual(result["total_count"], 1, query)
            self.assertEqual(result["results"][0]["material_id"], "mp-1067482", query)
        self.assertEqual(mp_search.search(self.db, "Ti2O4")["results"][0]["material_id"], "mp-2657")

    def test_element_combo(self):
        result = mp_search.search(self.db, "Ti O")
        self.assertEqual(result["total_count"], 1)
        result = mp_search.search(self.db, "Fe Li P O")
        self.assertEqual(result["results"][0]["material_id"], "mp-19009")

    def test_chemsys(self):
        result = mp_search.search(self.db, "O-Ti")
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["results"][0]["material_id"], "mp-2657")

    def test_spacegroup(self):
        self.assertEqual(mp_search.search(self.db, "225")["total_count"], 0)  # Cu deprecated
        self.assertEqual(mp_search.search(self.db, "Fm-3m")["total_count"], 0)
        self.assertEqual(mp_search.search(self.db, "Pnma")["total_count"], 1)
        self.assertEqual(mp_search.search(self.db, "136")["total_count"], 1)

    def test_filters(self):
        self.assertEqual(mp_search.search(self.db, "", filters={"stable_only": True})["total_count"], 3)
        self.assertEqual(mp_search.search(self.db, "", filters={"el_exc": "O"})["total_count"], 1)
        self.assertEqual(mp_search.search(self.db, "", filters={"el_inc": "O,Li"})["total_count"], 1)
        self.assertEqual(mp_search.search(self.db, "", filters={"band_gap_min": 2.0})["total_count"], 2)
        self.assertEqual(mp_search.search(self.db, "", filters={"crystal_system": "Cubic"})["total_count"], 1)
        self.assertEqual(mp_search.search(self.db, "", filters={"lat_min": 4.0, "lat_max": 5.5})["total_count"], 2)
        self.assertEqual(mp_search.search(self.db, "", filters={"metal_only": True})["total_count"], 0)
        self.assertEqual(mp_search.search(self.db, "", filters={"nsites_max": 4})["total_count"], 2)

    def test_sorting(self):
        result = mp_search.search(self.db, "", sort="bandgap_desc", size=10)
        gaps = [row["band_gap"] for row in result["results"]]
        self.assertEqual(gaps, sorted(gaps, reverse=True))

    def test_pagination(self):
        result = mp_search.search(self.db, "", page=1, size=2)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["total_pages"], 2)
        second = mp_search.search(self.db, "", page=2, size=2)
        first_ids = {row["material_id"] for row in result["results"]}
        second_ids = {row["material_id"] for row in second["results"]}
        self.assertFalse(first_ids & second_ids)

    def test_like_fallback(self):
        result = mp_search.search(self.db, "LiFe")  # not a formula, triggers LIKE
        self.assertEqual(result["total_count"], 1)
        self.assertTrue(result["formula_fallback_like"])


class CifToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "test.db"
        build_test_db(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_cif_bundle(self):
        raw = mp_search.get_structure_bytes(self.db, "mp-149")
        bundle = cif_tools.cif_bundle(raw, supercell=1, pbc=True)
        self.assertIn("_cell_length_a", bundle["cif"])
        self.assertEqual(bundle["info"]["atoms"], 2)
        self.assertTrue(bundle["poscar"].strip())
        self.assertIn("Si", bundle["poscar"].splitlines()[0])

    def test_xrd(self):
        raw = mp_search.get_structure_bytes(self.db, "mp-149")
        bundle = cif_tools.cif_bundle(raw, with_xrd=True)
        self.assertIsNotNone(bundle["xrd_structure"])
        pattern = bundle["xrd"]
        self.assertEqual(pattern["formula"], "Si")
        self.assertTrue(pattern["peaks"])
        self.assertEqual(pattern["wavelength_key"], xrd_tools.DEFAULT_WAVELENGTH)
        low, high = pattern["two_theta_range"]
        for peak in pattern["peaks"]:
            self.assertGreaterEqual(peak["two_theta"], low)
            self.assertLessEqual(peak["two_theta"], high)
            self.assertGreater(peak["d"], 0.0)
            self.assertTrue(peak["hkl"])
        self.assertAlmostEqual(max(p["intensity"] for p in pattern["peaks"]), 100.0, delta=0.1)
        # a different radiation must shift the pattern but keep it valid
        other = xrd_tools.pattern(bundle["xrd_structure"], "MoKa", (5.0, 90.0))
        self.assertTrue(other["peaks"])
        self.assertNotAlmostEqual(other["peaks"][0]["two_theta"], pattern["peaks"][0]["two_theta"], places=2)
        # default bundle stays lightweight (no XRD unless requested)
        self.assertIsNone(cif_tools.cif_bundle(raw)["xrd"])

    def test_supercell(self):
        raw = mp_search.get_structure_bytes(self.db, "mp-149")
        one = cif_tools.cif_bundle(raw, supercell=1)
        two = cif_tools.cif_bundle(raw, supercell=2)
        self.assertEqual(two["info"]["atoms"], 8 * one["info"]["atoms"])

    def test_info(self):
        raw = mp_search.get_material(self.db, "mp-2657")
        self.assertEqual(raw["formula"], "TiO2")
        self.assertEqual(raw["elements"], ["O", "Ti"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
