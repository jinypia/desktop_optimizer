"""Reusable dashboard widgets: metric cards and live charts."""
from __future__ import annotations

from collections import deque

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from . import theme


class MetricCard(QFrame):
    """A stat tile: colored series chip + title, hero value, sub-line."""

    def __init__(self, title: str, accent: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 9)
        lay.setSpacing(1)

        head = QHBoxLayout()
        head.setSpacing(6)
        chip = QLabel("●")
        chip.setStyleSheet(f"color: {accent}; font-size: 10px;")
        title_lbl = QLabel(title.upper())
        title_lbl.setObjectName("cardTitle")
        head.addWidget(chip)
        head.addWidget(title_lbl)
        head.addStretch(1)

        self._value = QLabel("–")
        self._value.setObjectName("cardValue")
        self._sub = QLabel("")
        self._sub.setObjectName("cardSub")
        self._sub2 = QLabel("")
        self._sub2.setObjectName("cardSub")

        lay.addLayout(head)
        lay.addWidget(self._value)
        lay.addWidget(self._sub)
        lay.addWidget(self._sub2)

    def update_values(self, value: str, sub: str = "", sub2: str = ""):
        self._value.setText(value)
        self._sub.setText(sub)
        self._sub2.setText(sub2)


class LiveChart:
    """A titled live line chart in one GraphicsLayoutWidget cell.

    Keeps a fixed-length rolling history; x axis is "seconds ago" ending
    at 0 (now). Includes a crosshair + value readout on hover.
    """

    def __init__(self, glw, row, col, title, series, history, interval_s,
                 y_range=None, fmt=None, x_label=False):
        self._dt = interval_s
        self._fmt = fmt or (lambda v: f"{v:.0f}")
        self._series_names = [s[0] for s in series]

        self.plot = glw.addPlot(row=row, col=col)
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.setTitle(title, color=theme.TEXT_2, size="10pt")
        self.plot.showGrid(x=False, y=True, alpha=0.18)
        for axis_name in ("left", "bottom"):
            ax = self.plot.getAxis(axis_name)
            ax.setPen(pg.mkPen(theme.AXIS))
            ax.setTextPen(pg.mkPen(theme.MUTED))
        if x_label:
            self.plot.setLabel("bottom", "seconds ago")
        self.plot.setXRange(-(history - 1) * interval_s, 0, padding=0.01)
        if y_range:
            self.plot.setYRange(*y_range, padding=0)
        else:
            self.plot.enableAutoRange(axis="y")
            self.plot.setLimits(yMin=0)

        if len(series) > 1:
            self.plot.addLegend(offset=(6, 2), labelTextColor=theme.TEXT_2,
                                brush=pg.mkBrush(26, 26, 25, 200),
                                pen=pg.mkPen(None))

        self._curves = []
        self._buffers = []
        for name, color in series:
            curve = self.plot.plot(name=name,
                                   pen=pg.mkPen(color, width=2),
                                   antialias=True)
            self._curves.append(curve)
            self._buffers.append(deque(maxlen=history))

        # hover readout: dashed crosshair + pinned value label
        self._vline = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen(theme.MUTED, width=1, style=Qt.DashLine))
        self._vline.hide()
        self.plot.addItem(self._vline, ignoreBounds=True)
        self._label = pg.TextItem(color=theme.TEXT, anchor=(0, 0))
        self._label.hide()
        self.plot.addItem(self._label, ignoreBounds=True)
        self.plot.scene().sigMouseMoved.connect(self._on_mouse)

    def update(self, *values):
        for buf, v in zip(self._buffers, values):
            buf.append(float(v))
        n = len(self._buffers[0])
        xs = [-(n - 1 - i) * self._dt for i in range(n)]
        for curve, buf in zip(self._curves, self._buffers):
            curve.setData(xs, list(buf))

    # -- hover ---------------------------------------------------------------
    def _on_mouse(self, scene_pos):
        if not self._buffers[0] or not self.plot.sceneBoundingRect().contains(scene_pos):
            self._vline.hide()
            self._label.hide()
            return
        vb = self.plot.getViewBox()
        p = vb.mapSceneToView(scene_pos)
        n = len(self._buffers[0])
        idx = int(round(p.x() / self._dt)) + (n - 1)
        idx = max(0, min(n - 1, idx))
        x = -(n - 1 - idx) * self._dt
        vals = [buf[idx] for buf in self._buffers]
        if len(vals) == 1:
            text = f"{abs(x):.0f}s ago · {self._fmt(vals[0])}"
        else:
            parts = " · ".join(f"{nm} {self._fmt(v)}"
                               for nm, v in zip(self._series_names, vals))
            text = f"{abs(x):.0f}s ago · {parts}"
        self._vline.setPos(x)
        self._vline.show()
        (x0, x1), (_y0, y1) = vb.viewRange()
        self._label.setText(text)
        self._label.setPos(x0 + (x1 - x0) * 0.02, y1)
        self._label.show()
