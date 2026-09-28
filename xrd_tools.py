"""X-ray diffraction (XRD) pattern calculation for the previewed structure.

Uses pymatgen's :class:`~pymatgen.analysis.diffraction.xrd.XRDCalculator`, so the
app stays fully offline.  The result is a plain dict that the Qt painter, the
peak table and the CSV exporter all consume.
"""
from __future__ import annotations

from pymatgen.analysis.diffraction.xrd import XRDCalculator
from pymatgen.core import Structure

# (display label, pymatgen wavelength key)
WAVELENGTHS: list[tuple[str, str]] = [
    ("Cu Kα (1.5406 Å)", "CuKa"),
    ("Mo Kα (0.7107 Å)", "MoKa"),
    ("Co Kα (1.7890 Å)", "CoKa"),
    ("Fe Kα (1.9373 Å)", "FeKa"),
    ("Cr Kα (2.2897 Å)", "CrKa"),
    ("Ag Kα (0.5594 Å)", "AgKa"),
]
DEFAULT_WAVELENGTH = "CuKa"
DEFAULT_TWO_THETA = (5.0, 90.0)


def wavelength_label(key: str) -> str:
    for label, value in WAVELENGTHS:
        if value == key:
            return label
    return key


def _hkl_label(hkl) -> str:
    return "(" + " ".join(str(int(index)) for index in hkl) + ")"


def pattern(structure: Structure, wavelength: str = DEFAULT_WAVELENGTH,
            two_theta_range: tuple[float, float] = DEFAULT_TWO_THETA) -> dict:
    """Compute a normalised (I_max = 100) powder XRD pattern for ``structure``.

    Returns a dict with ``peaks`` (2θ / intensity / d-spacing / hkl), the
    wavelength, the 2θ window and the reduced formula.
    """
    low, high = float(two_theta_range[0]), float(two_theta_range[1])
    if high <= low:
        low, high = DEFAULT_TWO_THETA
    calculator = XRDCalculator(wavelength=wavelength)
    result = calculator.get_pattern(structure, scaled=True, two_theta_range=(low, high))
    peaks = []
    for angle, intensity, d_spacing, hkls in zip(result.x, result.y, result.d_hkls, result.hkls):
        labels = [_hkl_label(entry["hkl"]) for entry in hkls]
        peaks.append({
            "two_theta": round(float(angle), 4),
            "intensity": round(float(intensity), 4),
            "d": round(float(d_spacing), 4),
            "hkl": labels[0] if labels else "",
            "hkls": labels,
        })
    return {
        "wavelength": float(calculator.wavelength),
        "wavelength_key": wavelength,
        "wavelength_label": wavelength_label(wavelength),
        "two_theta_range": [low, high],
        "peaks": peaks,
        "formula": structure.composition.reduced_formula,
    }
