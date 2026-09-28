"""MP-CIF Search Studio — local offline Materials Project CIF explorer.

Layout follows the FixAtoms Studio reference (sidebar + 3D stage), extended with a
result column and a full search / filter panel.  All queries run against the local
SQLite index built by ``build_index.py`` from the MP AWS Open Data snapshot.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve, QObject, QPointF, QPropertyAnimation, QRectF, QRunnable, QSize,
    QThreadPool, Qt, QUrl, Property, Signal, Slot,
)
from PySide6.QtGui import (
    QColor, QFont, QKeySequence, QPainter, QPen, QPixmap, QPolygonF, QShortcut,
)
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QApplication, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QLayout, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QStatusBar,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

import cif_tools
import mp_search
import xrd_tools
from build_index import build as build_index_db

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = mp_search.DEFAULT_DB

STYLESHEET = """
* { outline: none; }
QMainWindow, QWidget#root { background: #F2F2F7; }
QWidget { font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Microsoft YaHei UI'; font-size: 13px; color: #1C1C1E; }
QLabel { background: transparent; }
QLabel#brand { font-size: 26px; font-weight: 700; color: #000000; letter-spacing: -0.4px; }
QLabel#subtitle { color: #8E8E93; font-size: 13px; }
QLabel#muted { color: #8E8E93; font-size: 12px; }
QLabel#section { color: #8E8E93; font-size: 12px; font-weight: 600; }
QLabel#filename { font-size: 16px; font-weight: 600; color: #000000; }
QLabel#number { font-size: 21px; font-weight: 700; color: #007AFF; }
QLabel#logo { background: #007AFF; color: #FFFFFF; font-size: 15px; font-weight: 700; border-radius: 10px; }

/* surfaces */
QFrame#card { background: #FFFFFF; border: none; border-radius: 14px; }
QFrame#panel { background: transparent; border: none; }
QFrame#stat { background: #F2F2F7; border: none; border-radius: 12px; }
QFrame#stage { background: #FFFFFF; border: none; border-radius: 16px; }
QFrame#resultCard { background: #FFFFFF; border: 1px solid #EFEFF4; border-radius: 12px; }
QFrame#resultCard:hover { background: #F7F7FB; }
QFrame#resultCard[selected="true"] { background: #E5F1FF; border-color: #B7DBFF; }
QFrame#hline { background: #E5E5EA; border: none; max-height: 1px; min-height: 1px; }

/* tags & chips */
QLabel#tag { background: rgba(52, 199, 89, 0.16); color: #248A3D; border-radius: 9px; padding: 2px 9px; font-size: 11px; font-weight: 600; }
QLabel#tagWarn { background: rgba(255, 149, 0, 0.16); color: #C24C00; border-radius: 9px; padding: 2px 9px; font-size: 11px; font-weight: 600; }
QLabel#chip { background: #F2F2F7; color: #3C3C43; border-radius: 9px; padding: 2px 9px; font-size: 11px; }

/* buttons */
QPushButton { background: #E9E9EB; color: #007AFF; border: none; border-radius: 10px; padding: 9px 14px; font-weight: 600; }
QPushButton:hover { background: #DEDEE3; }
QPushButton:pressed { background: #D2D2D7; }
QPushButton:disabled { color: #C7C7CC; background: #F2F2F7; }
QPushButton#primary { background: #007AFF; color: #FFFFFF; border-radius: 11px; }
QPushButton#primary:hover { background: #0071E3; }
QPushButton#primary:pressed { background: #0063C8; }
QPushButton#primary:disabled { background: #B7DBFF; color: #FFFFFF; }
QPushButton#plain { background: transparent; color: #007AFF; padding: 6px 10px; }
QPushButton#plain:hover { background: rgba(0, 122, 255, 0.10); }
QPushButton#small { font-size: 12px; padding: 6px 10px; border-radius: 9px; }
QPushButton#chipButton { background: #F2F2F7; color: #007AFF; border-radius: 999px; padding: 6px 12px; font-weight: 500; }
QPushButton#chipButton:hover { background: #E5E5EA; }
QPushButton#segmentButton { background: transparent; color: #3C3C43; border-radius: 7px; padding: 6px 12px; font-weight: 500; }
QPushButton#segmentButton:checked { background: #FFFFFF; color: #000000; font-weight: 600; }

/* inputs */
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {
    background: #F2F2F7; border: 1px solid transparent; border-radius: 10px;
    padding: 8px 10px; selection-background-color: #B7DBFF; selection-color: #000000;
}
QLineEdit:hover, QComboBox:hover, QDoubleSpinBox:hover, QSpinBox:hover { background: #EBEBF0; }
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus { background: #FFFFFF; border-color: #007AFF; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView {
    background: #FFFFFF; border: 1px solid #E5E5EA; border-radius: 12px; padding: 5px;
    selection-background-color: #E5F1FF; selection-color: #000000; outline: none;
}
QComboBox QAbstractItemView::item { padding: 7px 9px; border-radius: 8px; min-height: 20px; }
QDoubleSpinBox::up-button, QSpinBox::up-button, QDoubleSpinBox::down-button, QSpinBox::down-button {
    width: 17px; border: none; background: transparent;
}
QDoubleSpinBox::up-arrow, QSpinBox::up-arrow { width: 8px; height: 8px; }
QDoubleSpinBox::down-arrow, QSpinBox::down-arrow { width: 8px; height: 8px; }

/* segmented tabs */
QTabWidget::pane { border: none; background: transparent; }
QTabBar { background: #E9E9EB; border-radius: 9px; }
QTabBar::tab { background: transparent; color: #3C3C43; padding: 6px 20px; margin: 2px; border-radius: 7px; font-weight: 500; }
QTabBar::tab:hover { color: #000000; }
QTabBar::tab:selected { background: #FFFFFF; color: #000000; font-weight: 600; }

/* table (XRD peaks) */
QTableWidget {
    background: #FFFFFF; border: none; border-radius: 14px;
    gridline-color: transparent; alternate-background-color: #FAFAFC;
    selection-background-color: #E5F1FF; selection-color: #000000; outline: none;
}
QTableWidget::item { border-bottom: 1px solid #F2F2F7; padding: 6px 10px; }
QHeaderView::section {
    background: #F2F2F7; color: #8E8E93; border: none; border-bottom: 1px solid #E5E5EA;
    padding: 8px 10px; font-weight: 600; font-size: 11px;
}
QTableCornerButton::section { background: #F2F2F7; border: none; }

/* menus (text-edit context menu) */
QMenu { background: #FFFFFF; border: 1px solid #E5E5EA; border-radius: 12px; padding: 6px; }
QMenu::item { padding: 7px 24px 7px 14px; border-radius: 8px; color: #1C1C1E; }
QMenu::item:selected { background: #E5F1FF; color: #000000; }
QMenu::separator { height: 1px; background: #E5E5EA; margin: 5px 8px; }

/* misc */
QDialog { background: #F2F2F7; }
QStatusBar { background: transparent; color: #8E8E93; font-size: 12px; padding: 2px 10px; }
QStatusBar::item { border: none; }
QToolTip { background: #2C2C2E; color: #FFFFFF; border: none; padding: 6px 9px; border-radius: 8px; }
QProgressBar { background: #E9E9EB; border: none; border-radius: 6px; height: 12px; text-align: center; color: #3C3C43; font-size: 11px; }
QProgressBar::chunk { background: #007AFF; border-radius: 6px; }
QScrollArea#scroll, QWidget#scrollContent { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 9px; margin: 2px; }
QScrollBar::handle:vertical { background: #C7C7CC; border-radius: 4px; min-height: 40px; }
QScrollBar::handle:vertical:hover { background: #AEAEB2; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 9px; margin: 2px; }
QScrollBar::handle:horizontal { background: #C7C7CC; border-radius: 4px; min-width: 40px; }
QScrollBar::handle:horizontal:hover { background: #AEAEB2; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
QPlainTextEdit {
    background: #1C1C1E; color: #E5E5EA; border: none; border-radius: 12px;
    font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; padding: 10px;
    selection-background-color: #007AFF; selection-color: #FFFFFF;
}
"""

STYLES = [("球棍模型", "ball"), ("空间填充", "space"), ("线框模型", "line"), ("棍状模型", "stick")]
CATEGORIES = [
    ("自动识别", "auto"),
    ("化学式", "formula"),
    ("元素组合", "elements"),
    ("化学体系", "chemsys"),
    ("MP-ID", "mpid"),
    ("空间群", "spacegroup"),
]
EXAMPLES = ["TiO2", "NiOOH", "Si O", "mp-149", "Li-Fe-P", "Fm-3m", "Fe2O3", "LiFePO4"]
CRYSTAL_SYSTEMS = ["", "Cubic", "Tetragonal", "Orthorhombic", "Hexagonal",
                   "Trigonal", "Monoclinic", "Triclinic"]
SORTS = [("稳定性优先", "default"), ("凸包能升序", "hull"), ("带隙升序", "bandgap_asc"),
         ("带隙降序", "bandgap_desc"), ("形成能升序", "eform"), ("密度降序", "density"),
         ("原子数升序", "nsites"), ("体积升序", "volume")]
PAGE_SIZE = 15


class IOSSwitch(QAbstractButton):
    """A small iOS-style toggle switch used in place of QCheckBox."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(44, 26)
        self._offset = 0.0
        self._animation = QPropertyAnimation(self, b"offset", self)
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.toggled.connect(self._animate)

    def sizeHint(self):  # noqa: N802
        return QSize(44, 26)

    def get_offset(self) -> float:
        return self._offset

    def set_offset(self, value: float):
        self._offset = float(value)
        self.update()

    offset = Property(float, get_offset, set_offset)

    def _animate(self, checked: bool):
        self._animation.stop()
        self._animation.setStartValue(self._offset)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        turned = self._offset
        off, on = QColor("#E9E9EB"), QColor("#34C759")
        blended = QColor(
            int(off.red() + (on.red() - off.red()) * turned),
            int(off.green() + (on.green() - off.green()) * turned),
            int(off.blue() + (on.blue() - off.blue()) * turned),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(blended)
        radius = rect.height() / 2.0
        painter.drawRoundedRect(rect, radius, radius)
        knob = rect.height() - 4.0
        x = 2.0 + turned * (rect.width() - knob - 4.0)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(QRectF(x, 2.0, knob, knob))
        painter.end()


def ios_toggle(text: str, checked: bool = False):
    """Return ``(container_widget, switch)``: an iOS switch with a label."""
    container = QWidget()
    row = QHBoxLayout(container)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(7)
    switch = IOSSwitch()
    switch.setChecked(checked)
    row.addWidget(switch)
    row.addWidget(QLabel(text))
    return container, switch


# --------------------------------------------------------------------------- #
# Background jobs
# --------------------------------------------------------------------------- #
class DetailSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class DetailJob(QRunnable):
    def __init__(self, db_path: Path, material_id: str, supercell: int, pbc: bool, req: int = 0):
        super().__init__()
        self.db_path = db_path
        self.material_id = material_id
        self.supercell = supercell
        self.pbc = pbc
        self.req = req
        self.signals = DetailSignals()

    def run(self):
        try:
            raw = mp_search.get_structure_bytes(self.db_path, self.material_id)
            if raw is None:
                raise ValueError("本地索引缺少该结构数据")
            bundle = cif_tools.cif_bundle(raw, self.supercell, self.pbc, with_xrd=True)
            material = mp_search.get_material(self.db_path, self.material_id) or {}
            self.signals.done.emit({"material": material, "bundle": bundle,
                                    "supercell": self.supercell, "pbc": self.pbc,
                                    "req": self.req})
        except Exception as error:  # noqa: BLE001
            self.signals.failed.emit(str(error))


class BuildSignals(QObject):
    progress = Signal(str, int)
    done = Signal(object)
    failed = Signal(str)


class BuildJob(QRunnable):
    def __init__(self, src: Path, out: Path):
        super().__init__()
        self.src = src
        self.out = out
        self.signals = BuildSignals()

    def run(self):
        try:
            stats = build_index_db(
                self.src, self.out,
                progress=lambda phase, done: self.signals.progress.emit(f"{phase}：{done:,}", -1),
                log=lambda message: self.signals.progress.emit(message, -1),
            )
            self.signals.done.emit(stats)
        except Exception as error:  # noqa: BLE001
            self.signals.failed.emit(str(error))


# --------------------------------------------------------------------------- #
# Qt <-> JS bridge
# --------------------------------------------------------------------------- #
class Bridge(QObject):
    stateChanged = Signal(str)
    command = Signal(str)

    def __init__(self, window):
        super().__init__(window)
        self.window = window

    @Slot()
    def ready(self):
        self.window.viewer_ready = True
        self.window.push_viewer()

    @Slot(str)
    def reportError(self, message: str):
        self.window.statusBar().showMessage(f"3D 视图：{message}")


class LocalPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        return url.scheme() in ("file", "qrc", "about", "data")

    def javaScriptConsoleMessage(self, level, message, line, source):
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            print(f"JavaScript {source}:{line}: {message}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Result card
# --------------------------------------------------------------------------- #
class ResultCard(QFrame):
    clicked = Signal(dict)

    def __init__(self, result: dict, parent=None):
        super().__init__(parent)
        self.result = result
        self.setObjectName("resultCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("selected", "false")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)

        top = QHBoxLayout()
        top.setSpacing(6)
        formula = QLabel(result.get("formula") or "—")
        formula.setObjectName("filename")
        formula.setStyleSheet("font-size: 15px;")
        top.addWidget(formula)
        is_stable = result.get("is_stable")
        tag = QLabel("稳定" if is_stable else "亚稳")
        tag.setObjectName("tag" if is_stable else "tagWarn")
        top.addWidget(tag)
        top.addStretch()
        mid = QLabel(result.get("material_id") or "")
        mid.setObjectName("muted")
        top.addWidget(mid)
        layout.addLayout(top)

        sub = QLabel(_subtitle(result))
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        metrics = QLabel(_metrics(result))
        metrics.setObjectName("muted")
        metrics.setWordWrap(True)
        layout.addWidget(metrics)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.result)
        super().mousePressEvent(event)


def _fmt(value, digits=3, suffix=""):
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}{suffix}"
    return f"{value}{suffix}"


def _subtitle(result: dict) -> str:
    lattice = result.get("lattice") or [None, None, None]
    angles = result.get("angles") or [None, None, None]
    sg = result.get("spacegroup") or "—"
    number = result.get("spacegroup_number")
    if number:
        sg = f"{sg} (#{number})"
    cell = " × ".join(_fmt(v, 3) for v in lattice)
    ang = "/".join(_fmt(v, 1) for v in angles)
    return f"{result.get('crystal_system') or '—'} · {sg}   晶格 {cell} Å   角度 {ang}°"


def _metrics(result: dict) -> str:
    gap = result.get("band_gap")
    if result.get("is_metal"):
        gap_text = "金属"
    else:
        gap_text = _fmt(gap, 3, " eV")
    hull = result.get("energy_above_hull")
    hull_text = _fmt(hull, 4, " eV/atom") if hull is not None else "无热力学"
    return (f"带隙 {gap_text}   凸包能 {hull_text}   "
            f"原子 {result.get('nsites') or '—'}   密度 {_fmt(result.get('density'), 3, ' g/cm³')}")


# --------------------------------------------------------------------------- #
# XRD pattern (calculated by xrd_tools, painted here with QPainter)
# --------------------------------------------------------------------------- #
def _nice_ticks(low: float, high: float, target: int = 8) -> list[float]:
    span = high - low
    if span <= 0:
        return [low]
    raw = span / max(1, target)
    magnitude = 10 ** math.floor(math.log10(raw))
    step = magnitude
    for multiple in (1, 2, 2.5, 5, 10):
        step = multiple * magnitude
        if raw <= step:
            break
    value = math.ceil(low / step - 1e-9) * step
    ticks: list[float] = []
    while value <= high + step * 1e-6:
        ticks.append(round(value, 6))
        value += step
    return ticks


class XRDPlot(QWidget):
    """Renders a powder XRD pattern (sticks + optional profile) with QPainter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pattern_data: dict | None = None
        self.annotate = True
        self.show_profile = True
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_pattern(self, data: dict | None):
        self.pattern_data = data
        self.update()

    def set_annotate(self, value: bool):
        self.annotate = bool(value)
        self.update()

    def set_profile(self, value: bool):
        self.show_profile = bool(value)
        self.update()

    def to_pixmap(self, width: int = 1600, height: int = 1000) -> QPixmap:
        pixmap = QPixmap(width, height)
        pixmap.fill(QColor("#ffffff"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._draw(painter, width, height)
        painter.end()
        return pixmap

    def paintEvent(self, event):  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._draw(painter, self.width(), self.height())
        painter.end()

    def _draw(self, painter: QPainter, width: int, height: int):
        painter.fillRect(0, 0, width, height, QColor("#ffffff"))
        left, right, top, bottom = 62, 22, 40, 46
        plot_w = max(20, width - left - right)
        plot_h = max(20, height - top - bottom)
        data = self.pattern_data
        if not data or not data.get("peaks"):
            painter.setPen(QColor("#8E8E93"))
            font = painter.font()
            font.setPointSizeF(11)
            painter.setFont(font)
            painter.drawText(QRectF(0, 0, width, height), Qt.AlignmentFlag.AlignCenter,
                             "选择材料后显示其粉末 XRD 图谱")
            return

        x0, x1 = data["two_theta_range"]
        peaks = data["peaks"]
        y_peak = max(peak["intensity"] for peak in peaks) or 100.0
        y_top = y_peak * 1.12

        def px(theta: float) -> float:
            return left + (theta - x0) / (x1 - x0) * plot_w

        def py(intensity: float) -> float:
            return top + plot_h - (intensity / y_top) * plot_h

        baseline = top + plot_h

        grid_pen = QPen(QColor("#EFEFF4"))
        grid_pen.setWidth(1)
        axis_pen = QPen(QColor("#AEAEB2"))
        axis_pen.setWidthF(1.2)
        tick_font = painter.font()
        tick_font.setPointSizeF(8.5)
        tick_font.setBold(False)

        painter.setPen(grid_pen)
        for tick in _nice_ticks(x0, x1):
            x = px(tick)
            painter.drawLine(QPointF(x, top), QPointF(x, baseline))
        for level in (0, 20, 40, 60, 80, 100):
            y = py(level)
            painter.drawLine(QPointF(left, y), QPointF(left + plot_w, y))

        painter.setFont(tick_font)
        painter.setPen(QColor("#8E8E93"))
        for tick in _nice_ticks(x0, x1):
            x = px(tick)
            painter.drawLine(QPointF(x, baseline), QPointF(x, baseline + 4))
            painter.drawText(QRectF(x - 24, baseline + 5, 48, 16),
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, f"{tick:g}")
        for level in (0, 20, 40, 60, 80, 100):
            y = py(level)
            painter.drawLine(QPointF(left - 4, y), QPointF(left, y))
            painter.drawText(QRectF(left - 56, y - 9, 50, 18),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{level:d}")

        painter.setPen(axis_pen)
        painter.drawLine(QPointF(left, top - 6), QPointF(left, baseline))
        painter.drawLine(QPointF(left, baseline), QPointF(left + plot_w, baseline))

        if self.show_profile:
            sigma = max(0.035, (x1 - x0) / 1400.0)
            samples = max(2, int(plot_w))
            xs = [x0 + (x1 - x0) * index / (samples - 1) for index in range(samples)]
            raw: list[float] = []
            for value in xs:
                total = 0.0
                for peak in peaks:
                    delta = (value - peak["two_theta"]) / sigma
                    if -6.0 <= delta <= 6.0:
                        total += peak["intensity"] * math.exp(-0.5 * delta * delta)
                raw.append(total)
            peak_raw = max(raw) or 1.0
            scale = y_peak / peak_raw
            polygon = QPolygonF()
            polygon.append(QPointF(px(xs[0]), baseline))
            for value, intensity in zip(xs, raw):
                polygon.append(QPointF(px(value), py(intensity * scale)))
            polygon.append(QPointF(px(xs[-1]), baseline))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#B7DBFF"))
            painter.drawPolygon(polygon)

        stick_pen = QPen(QColor("#007AFF"))
        stick_pen.setWidthF(max(1.1, width / 1500.0))
        painter.setPen(stick_pen)
        for peak in peaks:
            x = px(peak["two_theta"])
            painter.drawLine(QPointF(x, baseline), QPointF(x, py(peak["intensity"])))

        if self.annotate:
            candidates = sorted(peaks, key=lambda peak: -peak["intensity"])
            placed: list[float] = []
            drawn = 0
            label_font = painter.font()
            label_font.setPointSizeF(8.2)
            label_font.setBold(False)
            painter.setFont(label_font)
            painter.setPen(QColor("#3C3C43"))
            for peak in candidates:
                if peak["intensity"] < 1.0:
                    continue
                x = px(peak["two_theta"])
                if any(abs(x - other) < 30.0 for other in placed):
                    continue
                placed.append(x)
                y = max(top + 4, py(peak["intensity"]) - 6 - (drawn % 2) * 12)
                painter.drawText(QRectF(x - 26, y - 14, 52, 14),
                                 Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                                 f"{peak['two_theta']:.1f}")
                drawn += 1
                if drawn >= 22:
                    break

        title_font = painter.font()
        title_font.setPointSizeF(11)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor("#000000"))
        painter.drawText(QRectF(left, 6, plot_w, 26),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         f"{data['formula']} · 粉末 XRD · {data.get('wavelength_label', '')}")

        painter.setFont(tick_font)
        painter.setPen(QColor("#8E8E93"))
        painter.drawText(QRectF(left, height - 20, plot_w, 18),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, "2θ (°)")
        painter.save()
        painter.translate(16, top + plot_h / 2)
        painter.rotate(-90)
        painter.drawText(QRectF(-plot_h / 2, -14, plot_h, 18),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, "相对强度 (a.u.)")
        painter.restore()


class XRDView(QWidget):
    """XRD tab: radiation / 2θ controls, rendered pattern and peak table."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.structure = None
        self.current_pattern: dict | None = None
        self._suspend = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 10, 6, 4)
        layout.setSpacing(8)

        controls = QHBoxLayout()
        controls.setSpacing(6)
        controls.addWidget(QLabel("靶材"))
        self.wavelength_combo = QComboBox()
        for label, key in xrd_tools.WAVELENGTHS:
            self.wavelength_combo.addItem(label, key)
        controls.addWidget(self.wavelength_combo)
        controls.addSpacing(10)
        controls.addWidget(QLabel("2θ 范围"))
        self.tt_min = self._angle_spin(xrd_tools.DEFAULT_TWO_THETA[0])
        self.tt_max = self._angle_spin(xrd_tools.DEFAULT_TWO_THETA[1])
        controls.addWidget(self.tt_min)
        controls.addWidget(QLabel("–"))
        controls.addWidget(self.tt_max)
        annotate_box, self.annotate_check = ios_toggle("标注峰位", True)
        controls.addWidget(annotate_box)
        profile_box, self.profile_check = ios_toggle("轮廓线", True)
        controls.addWidget(profile_box)
        controls.addStretch()
        self.save_button = QPushButton("保存图片…")
        self.export_button = QPushButton("导出数据…")
        controls.addWidget(self.save_button)
        controls.addWidget(self.export_button)
        layout.addLayout(controls)

        self.plot = XRDPlot()
        layout.addWidget(self.plot, 3)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["2θ (°)", "d (Å)", "强度 I (%)", "hkl"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        for column in (0, 1, 2):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 2)

        self.wavelength_combo.currentIndexChanged.connect(self.recompute)
        self.tt_min.valueChanged.connect(self.recompute)
        self.tt_max.valueChanged.connect(self.recompute)
        self.annotate_check.toggled.connect(self.plot.set_annotate)
        self.profile_check.toggled.connect(self.plot.set_profile)
        self.save_button.clicked.connect(self.save_image)
        self.export_button.clicked.connect(self.export_data)

    @staticmethod
    def _angle_spin(value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.5, 150.0)
        spin.setDecimals(1)
        spin.setSingleStep(1.0)
        spin.setValue(value)
        spin.setSuffix(" °")
        return spin

    def set_bundle(self, bundle: dict | None):
        """Load a freshly computed structure bundle (see cif_tools.cif_bundle)."""
        self._suspend = True
        try:
            bundle = bundle or {}
            self.structure = bundle.get("xrd_structure")
            self.current_pattern = bundle.get("xrd")
            if self.current_pattern:
                window = self.current_pattern.get("two_theta_range")
                if window:
                    self.tt_min.setValue(window[0])
                    self.tt_max.setValue(window[1])
                index = self.wavelength_combo.findData(self.current_pattern.get("wavelength_key"))
                if index >= 0:
                    self.wavelength_combo.setCurrentIndex(index)
            self.plot.set_pattern(self.current_pattern)
            self._fill_table(self.current_pattern)
        finally:
            self._suspend = False

    def recompute(self):
        if self._suspend or self.structure is None:
            return
        low = self.tt_min.value()
        high = self.tt_max.value()
        if high <= low:
            high = min(150.0, low + 1.0)
            self._suspend = True
            self.tt_max.setValue(high)
            self._suspend = False
        try:
            data = xrd_tools.pattern(self.structure, self.wavelength_combo.currentData(), (low, high))
        except Exception as error:  # noqa: BLE001 - keep the UI alive
            self.current_pattern = None
            self.plot.set_pattern(None)
            self.plot.setToolTip(f"XRD 计算失败：{error}")
            self._fill_table(None)
            return
        self.current_pattern = data
        self.plot.set_pattern(data)
        self._fill_table(data)

    def _fill_table(self, data: dict | None):
        self.table.setRowCount(0)
        if not data:
            return
        peaks = sorted(data["peaks"], key=lambda peak: peak["two_theta"])
        self.table.setRowCount(len(peaks))
        for row, peak in enumerate(peaks):
            values = [f"{peak['two_theta']:.3f}", f"{peak['d']:.4f}",
                      f"{peak['intensity']:.1f}", ", ".join(peak["hkls"]) or "—"]
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column < 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, column, item)

    def _notify(self, message: str):
        window = self.window()
        status_bar = getattr(window, "statusBar", None)
        if callable(status_bar):
            status_bar().showMessage(message, 6000)

    def save_image(self):
        if not self.current_pattern:
            QMessageBox.information(self, "暂无 XRD", "请先搜索并选择一个材料。")
            return
        formula = self.current_pattern.get("formula", "structure")
        key = self.current_pattern.get("wavelength_key", "CuKa")
        default = str(Path.home() / f"{formula}_XRD_{key}.png")
        path, _ = QFileDialog.getSaveFileName(self, "保存 XRD 图片", default, "PNG 图片 (*.png)")
        if not path:
            return
        if not self.plot.to_pixmap().save(path, "PNG"):
            QMessageBox.critical(self, "保存失败", f"无法写入 {path}")
            return
        self._notify(f"已保存 XRD 图片：{path}")

    def export_data(self):
        if not self.current_pattern:
            QMessageBox.information(self, "暂无 XRD", "请先搜索并选择一个材料。")
            return
        data = self.current_pattern
        formula = data.get("formula", "structure")
        key = data.get("wavelength_key", "CuKa")
        default = str(Path.home() / f"{formula}_XRD_{key}.csv")
        path, _ = QFileDialog.getSaveFileName(self, "导出 XRD 数据", default, "CSV 文件 (*.csv)")
        if not path:
            return
        peaks = sorted(data["peaks"], key=lambda peak: peak["two_theta"])
        lines = [
            f"# {formula} powder XRD · {data.get('wavelength_label', '')}",
            f"# wavelength_angstrom,{data.get('wavelength')}",
            f"# two_theta_range_deg,{data['two_theta_range'][0]},{data['two_theta_range'][1]}",
            "two_theta_deg,d_angstrom,intensity_percent,hkl",
        ]
        for peak in peaks:
            lines.append(f"{peak['two_theta']:.4f},{peak['d']:.4f},"
                         f"{peak['intensity']:.2f},{' '.join(peak['hkls'])}")
        try:
            Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as error:
            QMessageBox.critical(self, "保存失败", str(error))
            return
        self._notify(f"已导出 XRD 数据：{path}")


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self, db_path: Path | None = None):
        super().__init__()
        self.db_path = Path(db_path) if db_path else DEFAULT_DB
        self.viewer_ready = False
        self.ui_ready = False
        self.generation = 0
        self.pool = QThreadPool(self)
        self.current_result: dict | None = None
        self.current_bundle: dict | None = None
        self.detail_req = 0
        self.cards: list[ResultCard] = []
        self.last_query = ""
        self.last_mode = "auto"
        self.last_filters: dict = {}
        self.page = 1
        self.total_pages = 1
        self.total_count = 0
        self.detail_job = None
        self.build_job = None

        self.setWindowTitle("MP-CIF Search Studio")
        self.resize(1560, 940)
        self.setMinimumSize(1220, 780)
        self.build_ui()
        self._install_shortcuts()

        self.bridge = Bridge(self)
        self.channel = QWebChannel(self.web.page())
        self.channel.registerObject("bridge", self.bridge)
        self.web.page().setWebChannel(self.channel)
        self.web.loadFinished.connect(self.page_loaded)
        self.web.setUrl(QUrl.fromLocalFile(str(ROOT / "viewer.html")))

        self.refresh_db_status()
        self.ui_ready = True
        self.statusBar().showMessage("正在初始化本地 3D 视图…")

    # ---- UI construction -------------------------------------------------- #
    @staticmethod
    def label(text, name=None, wrap=False):
        item = QLabel(text)
        if name:
            item.setObjectName(name)
        item.setWordWrap(wrap)
        item.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        return item

    @staticmethod
    def button(text, callback, name=None):
        button = QPushButton(text)
        if name:
            button.setObjectName(name)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(callback)
        return button

    @staticmethod
    def soft_shadow(blur: int = 20, dy: int = 4, alpha: int = 26) -> QGraphicsDropShadowEffect:
        effect = QGraphicsDropShadowEffect()
        effect.setBlurRadius(blur)
        effect.setOffset(0, dy)
        effect.setColor(QColor(28, 48, 82, alpha))
        return effect

    def card(self, parent_layout, title):
        frame = QFrame()
        frame.setObjectName("card")
        frame.setGraphicsEffect(self.soft_shadow())
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(9)
        layout.addWidget(self.label(title, "section"))
        parent_layout.addWidget(frame)
        return layout

    def build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(22, 18, 22, 8)
        outer.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(14)
        mark = QLabel("MP")
        mark.setObjectName("logo")
        mark.setFixedSize(42, 42)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(mark)
        brand = QVBoxLayout()
        brand.setSpacing(1)
        brand.addWidget(self.label("MP-CIF Search Studio", "brand"))
        brand.addWidget(self.label("Materials Project 本地离线晶体检索 · CC BY 4.0", "subtitle"))
        header.addLayout(brand)
        header.addStretch()
        header.addWidget(self.button("数据库管理", self.open_data_dialog, "plain"))
        header.addWidget(self.button("重建索引", self.rebuild_index, "plain"))
        outer.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(self.build_sidebar())
        body.addWidget(self.build_results_panel())
        body.addWidget(self.build_stage(), 1)
        outer.addLayout(body, 1)

        self.status = QStatusBar()
        self.setStatusBar(self.status)

    def build_sidebar(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName("scroll")
        scroll.setFixedWidth(340)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("scrollContent")
        side = QVBoxLayout(content)
        side.setContentsMargins(2, 4, 10, 8)
        side.setSpacing(14)

        # 01 search
        search = self.card(side, "01  /  搜索")
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("化学式 / 元素组合 / 化学体系 / mp-ID / 空间群")
        self.search_edit.returnPressed.connect(lambda: self.do_search(1))
        search.addWidget(self.search_edit)
        self.category_combo = QComboBox()
        for text, value in CATEGORIES:
            self.category_combo.addItem(text, value)
        search.addWidget(self.category_combo)
        search.addWidget(self.button("搜索", lambda: self.do_search(1), "primary"))
        chips = QHBoxLayout()
        chips.setSpacing(5)
        for index, example in enumerate(EXAMPLES[:5]):
            if index == 3:
                chips.addStretch()
            chips.addWidget(self.button(example, lambda checked=False, e=example: self.use_example(e), "chipButton"))
        search.addLayout(chips)
        chips2 = QHBoxLayout()
        chips2.setSpacing(5)
        for example in EXAMPLES[5:]:
            chips2.addWidget(self.button(example, lambda checked=False, e=example: self.use_example(e), "chipButton"))
        chips2.addStretch()
        search.addLayout(chips2)

        # 02 filters
        flt = self.card(side, "02  /  筛选（作用于全库）")
        flt.addWidget(self.label("空间群（Fm-3m 或 225，可逗号分隔）", "muted"))
        self.sg_edit = QLineEdit()
        self.sg_edit.setPlaceholderText("例如 Fm-3m,225")
        flt.addWidget(self.sg_edit)

        flt.addWidget(self.label("晶系", "muted"))
        self.system_combo = QComboBox()
        for system in CRYSTAL_SYSTEMS:
            self.system_combo.addItem(system or "全部晶系", system)
        flt.addWidget(self.system_combo)

        flt.addWidget(self.label("晶格 a / b / c 区间（Å，同时作用三个边长）", "muted"))
        self.lat_min = self._spin(0.0, 200.0, 3)
        self.lat_max = self._spin(0.0, 200.0, 3)
        flt.addLayout(self._range_row("最小", self.lat_min, "最大", self.lat_max))

        flt.addWidget(self.label("带隙区间（eV）", "muted"))
        self.gap_min = self._spin(0.0, 20.0, 3)
        self.gap_max = self._spin(0.0, 20.0, 3)
        flt.addLayout(self._range_row("最小", self.gap_min, "最大", self.gap_max))

        flt.addWidget(self.label("凸包能上限（eV/atom）", "muted"))
        self.hull_max = self._spin(-1.0, 10.0, 4)
        self.hull_max.setSingleStep(0.05)
        flt.addWidget(self.hull_max)

        flt.addWidget(self.label("包含元素 / 不包含元素", "muted"))
        self.el_inc = QLineEdit()
        self.el_inc.setPlaceholderText("必须全部包含，例如 Li Fe O")
        self.el_exc = QLineEdit()
        self.el_exc.setPlaceholderText("一个都不能有，例如 S")
        flt.addWidget(self.el_inc)
        flt.addWidget(self.el_exc)

        flt.addWidget(self.label("原子数区间", "muted"))
        self.nsites_min = QSpinBox()
        self.nsites_min.setRange(0, 5000)
        self.nsites_max = QSpinBox()
        self.nsites_max.setRange(0, 5000)
        self.nsites_max.setSpecialValueText("不限")
        self.nsites_min.setSpecialValueText("不限")
        flt.addLayout(self._range_row("最少", self.nsites_min, "最多", self.nsites_max))

        toggles = QHBoxLayout()
        toggles.setSpacing(16)
        stable_box, self.stable_check = ios_toggle("仅稳定相")
        metal_box, self.metal_check = ios_toggle("仅金属")
        toggles.addWidget(stable_box)
        toggles.addWidget(metal_box)
        toggles.addStretch()
        flt.addLayout(toggles)

        self.sort_combo = QComboBox()
        for text, value in SORTS:
            self.sort_combo.addItem(text, value)
        self.sort_combo.currentIndexChanged.connect(lambda: self.do_search(1))
        flt.addWidget(self.sort_combo)

        row = QHBoxLayout()
        row.addWidget(self.button("应用筛选", lambda: self.do_search(1), "primary"))
        row.addWidget(self.button("重置筛选", self.reset_filters))
        flt.addLayout(row)

        # 03 database status
        status_card = self.card(side, "03  /  数据库状态")
        self.db_status_label = self.label("正在读取…", "muted", True)
        status_card.addWidget(self.db_status_label)
        stats = QHBoxLayout()
        self.db_count_label = self._stat_box(stats, "材料总数")
        self.db_stable_label = self._stat_box(stats, "稳定相")
        status_card.addLayout(stats)
        stats2 = QHBoxLayout()
        self.db_sg_label = self._stat_box(stats2, "空间群")
        self.db_el_label = self._stat_box(stats2, "元素")
        status_card.addLayout(stats2)

        side.addStretch()
        scroll.setWidget(content)
        return scroll

    @staticmethod
    def _spin(low, high, decimals):
        box = QDoubleSpinBox()
        box.setRange(low, high)
        box.setDecimals(decimals)
        box.setSpecialValueText("不限")
        box.setValue(low)
        return box

    @staticmethod
    def _range_row(label_a, widget_a, label_b, widget_b) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel(label_a))
        row.addWidget(widget_a, 1)
        row.addWidget(QLabel(label_b))
        row.addWidget(widget_b, 1)
        return row

    @staticmethod
    def _stat_box(layout, caption) -> QLabel:
        box = QFrame()
        box.setObjectName("stat")
        inner = QVBoxLayout(box)
        inner.setContentsMargins(10, 8, 10, 8)
        value = QLabel("—")
        value.setObjectName("number")
        inner.addWidget(value)
        inner.addWidget(QLabel(caption))
        layout.addWidget(box)
        return value

    def build_results_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setFixedWidth(400)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        self.result_header = self.label("输入关键字开始检索", "muted")
        layout.addWidget(self.result_header)
        self.results_scroll = QScrollArea()
        self.results_scroll.setObjectName("scroll")
        self.results_scroll.setWidgetResizable(True)
        self.results_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.results_container = QWidget()
        self.results_container.setObjectName("scrollContent")
        self.results_layout = QVBoxLayout(self.results_container)
        self.results_layout.setContentsMargins(0, 0, 4, 0)
        self.results_layout.setSpacing(8)
        self.results_layout.addStretch()
        self.results_scroll.setWidget(self.results_container)
        layout.addWidget(self.results_scroll, 1)

        pager = QHBoxLayout()
        self.prev_button = self.button("上一页", lambda: self.do_search(self.page - 1), "small")
        self.next_button = self.button("下一页", lambda: self.do_search(self.page + 1), "small")
        self.page_label = self.label("—", "muted")
        pager.addWidget(self.prev_button)
        pager.addWidget(self.page_label, 1)
        pager.addWidget(self.next_button)
        layout.addLayout(pager)
        return panel

    def build_stage(self) -> QWidget:
        stage = QFrame()
        stage.setObjectName("stage")
        layout = QVBoxLayout(stage)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.title_label = self.label("尚未选择材料", "filename")
        toolbar.addWidget(self.title_label)
        toolbar.addStretch()
        self.style_combo = QComboBox()
        for text, value in STYLES:
            self.style_combo.addItem(text, value)
        self.style_combo.currentIndexChanged.connect(self.push_viewer)
        toolbar.addWidget(self.style_combo)
        cell_box, self.cell_check = ios_toggle("晶胞框", True)
        self.cell_check.toggled.connect(self.push_viewer)
        toolbar.addWidget(cell_box)
        layout.addLayout(toolbar)

        toolbar2 = QHBoxLayout()
        toolbar2.setSpacing(6)
        toolbar2.addWidget(self.label("超胞", "muted"))
        self.supercell_combo = QComboBox()
        for n in (1, 2, 3, 4):
            self.supercell_combo.addItem(f"{n}×{n}×{n}", n)
        self.supercell_combo.currentIndexChanged.connect(lambda: self.load_detail(self.current_result))
        toolbar2.addWidget(self.supercell_combo)
        pbc_box, self.pbc_check = ios_toggle("周期性边界镜像")
        self.pbc_check.toggled.connect(lambda: self.load_detail(self.current_result))
        toolbar2.addWidget(pbc_box)
        toolbar2.addStretch()
        toolbar2.addWidget(self.button("缩小", lambda: self.bridge.command.emit("zoomOut"), "small"))
        toolbar2.addWidget(self.button("放大", lambda: self.bridge.command.emit("zoomIn"), "small"))
        toolbar2.addWidget(self.button("重置视图", lambda: self.bridge.command.emit("reset"), "small"))
        toolbar2.addWidget(self.button("下载结构…", self.download_structure, "small"))
        layout.addLayout(toolbar2)

        self.web = QWebEngineView()
        self.web.setPage(LocalPage(self.web))
        self.web.page().setBackgroundColor(QColor("#FFFFFF"))
        settings = self.web.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.web.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.xrd_view = XRDView()
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(False)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.setUsesScrollButtons(False)
        self.tabs.addTab(self.web, "3D 结构")
        self.tabs.addTab(self.xrd_view, "XRD 图谱")
        layout.addWidget(self.tabs, 1)

        info = QFrame()
        info.setObjectName("stat")
        info.setGraphicsEffect(self.soft_shadow(blur=16, dy=3, alpha=22))
        self.info_label = self.label("选择材料后显示晶体学信息", "muted", True)
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(12, 10, 12, 10)
        info_layout.addWidget(self.info_label)
        layout.addWidget(info)
        return stage

    def _install_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+F"), self, self.search_edit.setFocus)
        QShortcut(QKeySequence("Ctrl+R"), self, self.reset_filters)
        QShortcut(QKeySequence("F5"), self, self.refresh_db_status)

    # ---- viewer ----------------------------------------------------------- #
    def page_loaded(self, ok):
        if not ok:
            self.status.showMessage("3D 视图加载失败，请检查 viewer.html 与 3Dmol-min.js 是否存在。")

    def push_viewer(self):
        if not self.viewer_ready:
            return
        state = {
            "generation": self.generation,
            "style": self.style_combo.currentData(),
            "showCell": self.cell_check.isChecked(),
        }
        if self.current_bundle is not None:
            state["cif"] = self.current_bundle.get("cif_pbc") or self.current_bundle.get("cif")
            material = (self.current_result or {}).get("material_id", "")
            state["label"] = f"{material} · {(self.current_result or {}).get('formula', '')}"
        self.bridge.stateChanged.emit(json.dumps(state, ensure_ascii=False))

    # ---- search ----------------------------------------------------------- #
    def use_example(self, example: str):
        self.search_edit.setText(example)
        mode = "auto"
        if example == "Fm-3m":
            mode = "spacegroup"
        index = self.category_combo.findData(mode)
        if index >= 0:
            self.category_combo.setCurrentIndex(index)
        self.do_search(1)

    def collect_filters(self) -> dict:
        return {
            "sg": self.sg_edit.text().strip(),
            "crystal_system": self.system_combo.currentData() or None,
            "lat_min": self._spin_value(self.lat_min),
            "lat_max": self._spin_value(self.lat_max),
            "band_gap_min": self._spin_value(self.gap_min),
            "band_gap_max": self._spin_value(self.gap_max),
            "energy_max": self._spin_value(self.hull_max),
            "el_inc": self.el_inc.text().strip(),
            "el_exc": self.el_exc.text().strip(),
            "nsites_min": self.nsites_min.value() or None,
            "nsites_max": self.nsites_max.value() or None,
            "stable_only": self.stable_check.isChecked() or None,
            "metal_only": self.metal_check.isChecked() or None,
        }

    @staticmethod
    def _spin_value(spin: QDoubleSpinBox):
        value = spin.value()
        return value if value > spin.minimum() else None

    def reset_filters(self):
        for spin in (self.lat_min, self.lat_max, self.gap_min, self.gap_max, self.hull_max):
            spin.setValue(spin.minimum())
        for spin in (self.nsites_min, self.nsites_max):
            spin.setValue(0)
        self.sg_edit.clear()
        self.el_inc.clear()
        self.el_exc.clear()
        self.system_combo.setCurrentIndex(0)
        self.stable_check.setChecked(False)
        self.metal_check.setChecked(False)
        self.status.showMessage("筛选已重置", 4000)
        if self.last_query or self.last_filters:
            self.do_search(1)

    def do_search(self, page: int):
        if not self.ui_ready:
            return
        if not self.db_path.is_file():
            QMessageBox.warning(self, "缺少数据库", "尚未找到本地索引 mp.db。\n请先下载数据并重建索引。")
            self.open_data_dialog()
            return
        if page < 1:
            return
        q = self.search_edit.text().strip()
        mode = self.category_combo.currentData()
        filters = self.collect_filters()
        try:
            payload = mp_search.search(self.db_path, q, mode, filters, page, PAGE_SIZE,
                                       self.sort_combo.currentData())
        except Exception as error:  # noqa: BLE001
            QMessageBox.warning(self, "搜索失败", str(error))
            return
        self.last_query, self.last_mode, self.last_filters = q, mode, filters
        self.page = payload["page"]
        self.total_pages = payload["total_pages"]
        self.total_count = payload["total_count"]
        self.render_results(payload)
        kind_names = {"mpid": "MP-ID", "formula": "化学式", "elements": "元素组合",
                      "chemsys": "化学体系", "spacegroup": "空间群", "empty": "全部", "text": "模糊"}
        kind = kind_names.get(payload["spec"]["kind"], payload["spec"]["kind"])
        message = f"“{q or '（全部）'}” · 识别为 {kind} · 共 {self.total_count:,} 条"
        if payload.get("formula_fallback_like"):
            message += " · 已使用模糊匹配"
        self.result_header.setText(message)
        self.page_label.setText(f"第 {self.page} / {self.total_pages} 页")
        self.prev_button.setEnabled(self.page > 1)
        self.next_button.setEnabled(self.page < self.total_pages)
        self.status.showMessage(f"检索完成：{self.total_count:,} 条命中", 5000)

    def render_results(self, payload: dict):
        while self.results_layout.count() > 1:
            item = self.results_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.cards = []
        if not payload["results"]:
            empty = self.label("没有匹配的材料，试试放宽筛选条件。", "muted", True)
            empty.setContentsMargins(6, 20, 6, 6)
            self.results_layout.insertWidget(0, empty)
            return
        for index, result in enumerate(payload["results"]):
            card = ResultCard(result)
            card.clicked.connect(self.select_result)
            self.results_layout.insertWidget(index, card)
            self.cards.append(card)
        selected = (self.current_result or {}).get("material_id")
        if selected:
            for card in self.cards:
                if card.result.get("material_id") == selected:
                    self._mark_selected(card)
                    break

    def _mark_selected(self, selected_card):
        for card in self.cards:
            card.setProperty("selected", "true" if card is selected_card else "false")
            card.style().unpolish(card)
            card.style().polish(card)

    def select_result(self, result: dict):
        self.current_result = result
        for card in self.cards:
            if card.result.get("material_id") == result.get("material_id"):
                self._mark_selected(card)
                break
        self.load_detail(result)

    def load_detail(self, result: dict | None):
        if not result:
            return
        self.current_result = result
        self.title_label.setText(f"{result.get('formula', '')} · {result.get('material_id', '')}")
        self.status.showMessage("正在生成结构…")
        supercell = self.supercell_combo.currentData()
        pbc = self.pbc_check.isChecked()
        self.detail_req += 1
        job = DetailJob(self.db_path, result["material_id"], supercell, pbc, self.detail_req)
        job.signals.done.connect(self.accept_detail)
        job.signals.failed.connect(self.detail_failed)
        self.detail_job = job
        self.pool.start(job)

    @Slot(object)
    def accept_detail(self, payload: dict):
        if payload.get("req") is not None and payload["req"] != self.detail_req:
            return  # a newer request already superseded this one
        self.generation += 1
        self.current_bundle = payload["bundle"]
        self.xrd_view.set_bundle(payload["bundle"])
        material = payload["material"]
        self.current_result = material
        self.push_viewer()
        info = payload["bundle"]["info"]
        elements = "  ".join(f"{item['element']}×{item['count']}" for item in info["elements"])
        lattice = " × ".join(f"{v:.3f}" for v in info["lattice"])
        angles = "/".join(f"{v:.2f}" for v in info["angles"])
        gap = material.get("band_gap")
        gap_text = "金属" if material.get("is_metal") else (f"{gap:.3f} eV" if gap is not None else "—")
        hull = material.get("energy_above_hull")
        eform = material.get("formation_energy_per_atom")
        density = material.get("density")
        lines = [
            f"{material.get('material_id')}  ·  {info['formula']}  ·  "
            f"{material.get('crystal_system') or '—'}  ·  {material.get('spacegroup') or '—'}"
            f"（#{material.get('spacegroup_number') or '—'}）",
            f"原子数 {info['atoms']}   晶胞 {lattice} Å   角度 {angles}°   体积 {info['volume']} Å³",
            f"元素 {elements}",
            f"带隙 {gap_text}   凸包能 {'—' if hull is None else f'{hull:.4f} eV/atom'}"
            f"   形成能 {'—' if eform is None else f'{eform:.4f} eV/atom'}"
            f"   密度 {'—' if density is None else f'{density:.3f} g/cm³'}"
            f"   稳定相 {'是' if material.get('is_stable') else '否'}",
        ]
        if payload.get("pbc") and payload["bundle"].get("pbc_added"):
            lines.append(f"已补周期性镜像原子 {payload['bundle']['pbc_added']} 个（仅显示，不写入下载文件）")
        self.info_label.setText("\n".join(lines))
        self.status.showMessage(f"已生成 {info['formula']} 的结构", 4000)

    def select_result_card(self, result):
        for card in self.cards:
            if card.result.get("material_id") == result.get("material_id"):
                self._mark_selected(card)
                break

    @Slot(str)
    def detail_failed(self, error: str):
        self.status.showMessage("结构生成失败")
        QMessageBox.warning(self, "无法生成结构", error)

    # ---- download / data -------------------------------------------------- #
    def download_structure(self):
        if not self.current_bundle or not self.current_result:
            QMessageBox.information(self, "尚无结构", "请先搜索并选择一个材料。")
            return
        material_id = self.current_result.get("material_id", "structure")
        supercell = self.supercell_combo.currentData()
        default = str(Path.home() / f"{material_id}_s{supercell}.cif")
        path, selected_filter = QFileDialog.getSaveFileName(
            self, "下载结构", default,
            "CIF 文件 (*.cif);;VASP POSCAR (*.vasp *.poscar POSCAR);;所有文件 (*)")
        if not path:
            return
        path = Path(path)
        is_poscar = ("POSCAR" in selected_filter) or path.suffix.lower() in (".vasp", ".poscar") \
            or path.name.upper().startswith("POSCAR")
        try:
            text = self.current_bundle["poscar"] if is_poscar else self.current_bundle["cif"]
            path.write_text(text, encoding="utf-8")
        except OSError as error:
            QMessageBox.critical(self, "保存失败", str(error))
            return
        self.status.showMessage(f"已保存：{path}", 6000)

    def refresh_db_status(self):
        stats = mp_search.database_stats(self.db_path)
        if not stats.get("exists"):
            self.db_status_label.setText("未找到本地索引。请先下载数据并重建索引。")
            for label in (self.db_count_label, self.db_stable_label, self.db_sg_label, self.db_el_label):
                label.setText("—")
            return
        size = stats["bytes"] / 1048576
        self.db_status_label.setText(f"{self.db_path.name} · {size:.0f} MiB\n{self.db_path.parent}")
        self.db_count_label.setText(f"{stats['active']:,}" if stats.get("active") else "—")
        self.db_stable_label.setText(f"{stats['stable']:,}" if stats.get("stable") else "—")
        self.db_sg_label.setText(str(stats.get("spacegroups") or "—"))
        self.db_el_label.setText(str(stats.get("elements") or "—"))

    def open_data_dialog(self):
        DataDialog(self).exec()

    def rebuild_index(self):
        if self.build_job is not None:
            QMessageBox.information(self, "正在建库", "索引重建已在进行中。")
            return
        if not (self.db_path.parent / "collections").is_dir():
            QMessageBox.warning(self, "缺少数据", "未找到下载的原始分片目录 collections/。请先下载数据。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("重建本地索引")
        dialog.resize(560, 320)
        layout = QVBoxLayout(dialog)
        layout.addWidget(self.label("正在把 jsonl.gz 分片合并为 SQLite 索引 mp.db …", "muted", True))
        bar = QProgressBar()
        bar.setRange(0, 0)
        layout.addWidget(bar)
        log_view = QPlainTextEdit()
        log_view.setReadOnly(True)
        layout.addWidget(log_view, 1)
        close_button = self.button("后台运行", dialog.accept)
        layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignRight)

        job = BuildJob(self.db_path.parent, self.db_path)
        job.signals.progress.connect(lambda message, _: log_view.appendPlainText(message))
        job.signals.done.connect(lambda stats: self._build_done(dialog, stats))
        job.signals.failed.connect(lambda error: self._build_failed(dialog, error))
        self.build_job = job
        self.pool.start(job)
        dialog.exec()

    def _build_done(self, dialog, stats):
        self.build_job = None
        dialog.accept()
        self.refresh_db_status()
        QMessageBox.information(self, "建库完成",
                                f"材料 {stats.get('materials', 0):,} 条\n"
                                f"数据库 {stats.get('db_bytes', 0) / 1048576:.0f} MiB\n"
                                f"用时 {stats.get('elapsed_sec', 0)} 秒")

    def _build_failed(self, dialog, error):
        self.build_job = None
        dialog.accept()
        QMessageBox.critical(self, "建库失败", error)

    def download_data(self):
        DataDialog(self, start_download=True).exec()


# --------------------------------------------------------------------------- #
# Data management dialog
# --------------------------------------------------------------------------- #
class DataDialog(QDialog):
    def __init__(self, window: MainWindow, start_download: bool = False):
        super().__init__(window)
        self.window = window
        self.process = None
        self.setWindowTitle("MP-CIF 数据库管理")
        self.resize(720, 520)
        layout = QVBoxLayout(self)

        stats = mp_search.database_stats(window.db_path)
        summary = (
            f"索引路径：{window.db_path}\n"
            + (f"材料 {stats.get('active'):,} 条 · 结构 {stats.get('structures'):,} 个 · "
               f"空间群 {stats.get('spacegroups')} · 元素 {stats.get('elements')}\n"
               f"文件大小 {stats['bytes'] / 1048576:.0f} MiB"
               if stats.get("exists") else "（索引尚未构建）")
        )
        layout.addWidget(window.label(summary, "muted", True))

        layout.addWidget(window.label(
            "数据来源：Materials Project AWS Open Data（materialsproject-build 公共桶，免密钥）。\n"
            "下载约 1.0 GiB，共 3297 个 jsonl.gz 分片；建库后 mp.db 约 600 MiB。", "muted", True))

        buttons = QHBoxLayout()
        self.download_button = window.button("开始下载数据", self.start_download)
        self.build_button = window.button("重建索引", self.start_build)
        buttons.addWidget(self.download_button)
        buttons.addWidget(self.build_button)
        buttons.addStretch()
        buttons.addWidget(window.button("关闭", self.accept))
        layout.addLayout(buttons)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, 1)

        if start_download:
            self.start_download()

    def start_download(self):
        if self.process is not None:
            return
        from PySide6.QtCore import QProcess, QProcessEnvironment
        self.progress.setVisible(True)
        self.download_button.setEnabled(False)
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(ROOT))
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUTF8", "1")
        env.insert("JOBS", "24")
        self.process.setProcessEnvironment(env)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.readyReadStandardError.connect(self._read_error)
        self.process.finished.connect(self._download_finished)
        self.log_view.appendPlainText("正在下载 Materials Project Open Data（可后台等待）…\n")
        self.process.start(sys.executable, [str(ROOT / "fetch_mp_data.py"), "--jobs", "24"])

    def _read_output(self):
        text = bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
        self.log_view.appendPlainText(text.rstrip())

    def _read_error(self):
        text = bytes(self.process.readAllStandardError()).decode("utf-8", "replace")
        if text.strip():
            self.log_view.appendPlainText(text.rstrip())

    def _download_finished(self, code, status):
        self.progress.setVisible(False)
        self.download_button.setEnabled(True)
        self.log_view.appendPlainText(f"\n下载进程结束（退出码 {code}）。")
        self.window.refresh_db_status()

    def start_build(self):
        self.accept()
        self.window.rebuild_index()


def main(argv=None):
    parser = argparse.ArgumentParser(description="MP-CIF Search Studio")
    parser.add_argument("--db", help="mp.db 路径（默认 data/mp_open_data/mp.db）")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    app = QApplication(sys.argv[:1])
    app.setApplicationName("MP-CIF Search Studio")
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(STYLESHEET)
    for asset in ("viewer.html", "viewer.js", "3Dmol-min.js"):
        if not (ROOT / asset).is_file():
            QMessageBox.critical(None, "缺少资源文件", f"找不到 {ROOT / asset}")
            return 1
    window = MainWindow(Path(args.db) if args.db else None)
    window.show()
    return app.exec()


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    raise SystemExit(main())
