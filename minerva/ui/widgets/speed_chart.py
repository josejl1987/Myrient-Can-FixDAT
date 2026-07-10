"""Rolling transfer activity chart used by the Downloads workspace."""

from __future__ import annotations

import time
from collections import deque

import pyqtgraph as pg
from PyQt6 import QtWidgets

from minerva.ui.theme import ThemeTokens

_WINDOW_SECS = 60
_MAX_POINTS = 180


class SpeedChart(QtWidgets.QFrame):
    """Compact real-time download/upload chart with a clear legend."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("transferActivityPanel")
        self._tokens = ThemeTokens()
        self._timestamps: deque[float] = deque(maxlen=_MAX_POINTS)
        self._download: deque[float] = deque(maxlen=_MAX_POINTS)
        self._upload: deque[float] = deque(maxlen=_MAX_POINTS)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 10)
        root.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(12)
        title = QtWidgets.QLabel("Transfer activity")
        title.setObjectName("panelTitle")
        header.addWidget(title)

        self._download_legend = QtWidgets.QLabel("●  Download  0 B/s")
        self._download_legend.setObjectName("chartDownloadLegend")
        header.addWidget(self._download_legend)
        self._upload_legend = QtWidgets.QLabel("●  Upload  0 B/s")
        self._upload_legend.setObjectName("chartUploadLegend")
        header.addWidget(self._upload_legend)
        header.addStretch(1)
        self._range_label = QtWidgets.QLabel("Real-time  ▾")
        self._range_label.setObjectName("chartRange")
        header.addWidget(self._range_label)
        root.addLayout(header)

        # Backward-compatible aliases used by earlier code and ad-hoc tests.
        self._current = self._download_legend
        self._peak = QtWidgets.QLabel("")
        self._peak.hide()

        self._plot = pg.PlotWidget(background=self._tokens.surface)
        self._plot.setObjectName("transferActivityPlot")
        self._plot.setMinimumHeight(118)
        self._plot.setMaximumHeight(165)
        self._plot.setLabel("left", "KB/s")
        self._plot.setMouseEnabled(x=False, y=False)
        self._plot.showGrid(x=True, y=True, alpha=0.12)
        self._plot.setXRange(-_WINDOW_SECS, 0)
        self._plot.enableAutoRange(axis="x", enable=False)
        self._plot.enableAutoRange(axis="y")
        self._plot.getAxis("bottom").setTicks(
            [[(-60, "60s"), (-50, "50s"), (-40, "40s"), (-30, "30s"), (-20, "20s"), (-10, "10s"), (0, "Now")]]
        )
        self._download_curve = self._plot.plot(
            pen=pg.mkPen(self._tokens.info_fg, width=2)
        )
        self._upload_curve = self._plot.plot(
            pen=pg.mkPen(self._tokens.success, width=1.5)
        )
        root.addWidget(self._plot)

    @staticmethod
    def _format_speed(value: float) -> str:
        units = ["B/s", "KB/s", "MB/s", "GB/s"]
        value = max(0.0, float(value))
        index = 0
        while value >= 1024 and index < len(units) - 1:
            value /= 1024
            index += 1
        return f"{value:.1f} {units[index]}" if index else f"{int(value)} {units[index]}"

    def add_data_point(self, download_speed: float, upload_speed: float = 0.0) -> None:
        self._timestamps.append(time.monotonic())
        self._download.append(download_speed / 1024)
        self._upload.append(upload_speed / 1024)
        self._download_legend.setText(
            f"●  Download  {self._format_speed(download_speed)}"
        )
        self._upload_legend.setText(
            f"●  Upload  {self._format_speed(upload_speed)}"
        )
        self._redraw()

    def clear(self) -> None:
        self._timestamps.clear()
        self._download.clear()
        self._upload.clear()
        self._download_curve.clear()
        self._upload_curve.clear()
        self._download_legend.setText("●  Download  0 B/s")
        self._upload_legend.setText("●  Upload  0 B/s")

    def _redraw(self) -> None:
        if not self._timestamps:
            return
        now = time.monotonic()
        x = [stamp - now for stamp in self._timestamps]
        self._download_curve.setData(x, list(self._download))
        self._upload_curve.setData(x, list(self._upload))

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self._plot.setBackground(tokens.surface)
        self._download_curve.setPen(pg.mkPen(tokens.info_fg, width=2))
        self._upload_curve.setPen(pg.mkPen(tokens.success, width=1.5))
