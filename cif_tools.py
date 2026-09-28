"""Structure helpers: zlib structure dict -> pymatgen -> CIF / POSCAR / info / PBC images."""
from __future__ import annotations

import json
from collections import Counter

import numpy as np
from pymatgen.core import Structure
from pymatgen.io.cif import CifWriter
from pymatgen.io.vasp import Poscar

import xrd_tools

# Periodic-boundary display guard rails (mirrors the design doc).
PBC_CUTOFF = 3.0
PBC_MAX_SITES = 120
PBC_MAX_PADDED = 240


def structure_from_bytes(raw: bytes) -> Structure:
    data = json.loads(raw.decode("utf-8"))
    structure = Structure.from_dict(data)
    for site in structure:
        if len(site.species) > 1:
            raise ValueError("结构含无序占据，暂不支持生成 CIF")
        occupancy = list(site.species.values())[0]
        if abs(occupancy - 1.0) > 1e-6:
            raise ValueError("结构含部分占据，暂不支持生成 CIF")
    return structure


def _conventional(structure: Structure) -> Structure:
    try:
        return structure.to_conventional()
    except Exception:  # noqa: BLE001 - fall back to the primitive cell
        return structure


def build_structure(structure: Structure, supercell: int = 1, conventional: bool = True) -> Structure:
    supercell = max(1, min(4, int(supercell)))
    result = _conventional(structure) if conventional else structure
    if supercell > 1:
        result = result.make_supercell([supercell, supercell, supercell])
    return result


def build_cif(structure: Structure, supercell: int = 1, conventional: bool = True) -> str:
    return str(CifWriter(build_structure(structure, supercell, conventional), symprec=None))


def build_poscar(structure: Structure, supercell: int = 1, conventional: bool = True) -> str:
    return _poscar_text(build_structure(structure, supercell, conventional))


def structure_info(structure: Structure) -> dict:
    lattice = structure.lattice
    counts = Counter(site.species_string.split()[0] for site in structure)
    return {
        "formula": structure.composition.reduced_formula,
        "formula_full": structure.composition.formula,
        "atoms": len(structure),
        "elements": [{"element": symbol, "count": count} for symbol, count in sorted(counts.items())],
        "lattice": [round(float(v), 4) for v in lattice.abc],
        "angles": [round(float(v), 4) for v in lattice.angles],
        "volume": round(float(lattice.volume), 4),
        "density": round(float(structure.density), 4),
    }


def pad_pbc(structure: Structure, cutoff: float = PBC_CUTOFF,
            max_sites: int = PBC_MAX_SITES, max_padded: int = PBC_MAX_PADDED) -> tuple[Structure | None, int]:
    """Add periodic images within ``cutoff`` of an original atom for 3D display.

    Returns (padded_structure, added_count).  ``(None, 0)`` when the guards trip.
    """
    if len(structure) > max_sites:
        return None, 0
    frac = np.asarray(structure.frac_coords, dtype=float)
    cell = np.asarray(structure.lattice.matrix, dtype=float)
    species = [site.species for site in structure]

    shifts = np.array([[i, j, k] for i in (-1, 0, 1) for j in (-1, 0, 1) for k in (-1, 0, 1)
                       if not (i == 0 and j == 0 and k == 0)], dtype=float)

    extra_frac: list[np.ndarray] = []
    extra_species: list = []
    for shift in shifts:
        images = frac + shift  # (N, 3)
        delta = frac[None, :, :] - images[:, None, :]  # (N_image, N_atom, 3)
        cart = delta @ cell
        distance = np.sqrt((cart ** 2).sum(axis=2))
        keep = distance.min(axis=1) <= cutoff
        if keep.any():
            extra_frac.append(images[keep])
            extra_species.extend([species[i] for i in np.flatnonzero(keep)])

    if not extra_frac:
        return structure, 0
    all_frac = np.vstack([frac] + extra_frac)
    total = len(all_frac)
    if total > max_padded:
        return None, 0
    new_species = species + extra_species
    padded = Structure(structure.lattice, new_species, all_frac, coords_are_cartesian=False)
    return padded, total - len(structure)


def cif_bundle(raw: bytes, supercell: int = 1, pbc: bool = False,
               conventional: bool = True, with_xrd: bool = False) -> dict:
    """Produce the CIF payload used by both the viewer and the download button.

    When ``with_xrd`` is set the payload also carries a powder XRD pattern and
    the cleaned :class:`~pymatgen.core.Structure`, so the UI can recompute the
    pattern for other radiation / 2θ windows without re-reading the database.
    """
    structure = structure_from_bytes(raw)
    clean = build_structure(structure, supercell, conventional)
    info = structure_info(clean)

    xrd = None
    xrd_structure = None
    if with_xrd:
        # XRD is computed on the conventional cell (ignoring the display
        # supercell): peak positions and normalised intensities are identical
        # while staying cheap to recompute for other radiation.
        xrd_structure = _conventional(structure)
        try:
            xrd = xrd_tools.pattern(xrd_structure)
        except Exception:  # noqa: BLE001 - XRD is best-effort, never fatal
            xrd = None
            xrd_structure = None

    cif_pbc: str | None = None
    added = 0
    if pbc:
        padded, added = pad_pbc(clean)
        if padded is not None and added:
            try:
                cif_pbc = str(CifWriter(padded, symprec=None))
            except Exception:  # noqa: BLE001 - padding is display-only, never fatal
                cif_pbc = None
                added = 0
    return {
        "cif": str(CifWriter(clean, symprec=None)),
        "cif_pbc": cif_pbc,
        "pbc_added": added,
        "info": info,
        "poscar": _poscar_text(clean),
        "xrd": xrd,
        "xrd_structure": xrd_structure,
    }


def _poscar_text(structure: Structure) -> str:
    poscar = Poscar(structure)
    getter = getattr(poscar, "get_str", None) or getattr(poscar, "get_string")
    return getter()
