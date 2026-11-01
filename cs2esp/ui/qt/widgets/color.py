"""
QColorDialog-backed colour picker for config colour tuples.
"""

from typing import Any, Callable, Sequence

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QColorDialog, QPushButton, QWidget

from cs2esp.ui.qt import theme as qtheme


ColorTuple = tuple[float, float, float] | tuple[float, float, float, float]


def _tuple_to_qcolor(values: Sequence[float]) -> QColor:
	if not values:
		return QColor(255, 255, 255)
	red = max(0, min(255, int(round(float(values[0]) * 255))))
	green = max(0, min(255, int(round(float(values[1]) * 255))))
	blue = max(0, min(255, int(round(float(values[2]) * 255))))
	alpha = 255
	if len(values) >= 4:
		alpha = max(0, min(255, int(round(float(values[3]) * 255))))
	return QColor(red, green, blue, alpha)


class ColorButton(QPushButton):
	"""Click-to-open colour picker for a config tuple."""
	changed = Signal(tuple)

	SWATCH_SIZE = 18

	def __init__(
		self,
		label: str,
		section: Any,
		attr: str,
		*,
		with_alpha: bool = False,
		auto_color: Callable[[], Sequence[float] | None] | None = None,
		parent: QWidget | None = None,
	) -> None:
		super().__init__(label, parent)
		self.setObjectName("ColorButton")
		self._section = section
		self._attr = attr
		self._with_alpha = with_alpha
		self._auto_color = auto_color
		self._label = label
		self._theme_name = "midnight"
		self._swatch_signature = None

		self.setProperty("withAlpha", with_alpha)
		self.setAccessibleName(label)
		self.setCursor(Qt.PointingHandCursor)
		self.setFocusPolicy(Qt.StrongFocus)
		self.setMinimumHeight(30)
		self.setIconSize(QSize(self.SWATCH_SIZE, self.SWATCH_SIZE))
		self.clicked.connect(self._open_dialog)
		self._refresh_swatch()

	def apply_theme(self, theme_name: str) -> None:
		'''Repaint the swatch border and alpha grid for the active theme.'''
		self._theme_name = theme_name or self._theme_name
		self.setProperty("themeName", self._theme_name)
		self._swatch_signature = None
		self._refresh_swatch()
		self.update()

	def paintEvent(self, event) -> None:  # noqa: N802
		self._refresh_swatch()
		super().paintEvent(event)

	def refresh(self) -> None:
		'''Force a swatch repaint after resetting to the theme colour.'''
		self._swatch_signature = None
		self._refresh_swatch()
		self.update()

	def _refresh_swatch(self) -> None:
		values = self._read_tuple()
		signature = (tuple(round(component, 6) for component in values), self._theme_name, round(self.devicePixelRatioF(), 2))
		if signature == self._swatch_signature:
			return
		self._swatch_signature = signature
		self.setIcon(QIcon(self._swatch_pixmap(_tuple_to_qcolor(values))))

	def _swatch_pixmap(self, color: QColor) -> QPixmap:
		pixel_ratio = max(1.0, self.devicePixelRatioF())
		pixel_size = max(1, int(round(self.SWATCH_SIZE * pixel_ratio)))
		pixmap = QPixmap(pixel_size, pixel_size)
		pixmap.setDevicePixelRatio(pixel_ratio)
		pixmap.fill(Qt.transparent)

		palette = qtheme.PALETTES.get(self._theme_name, next(iter(qtheme.PALETTES.values())))
		base = QColor(palette["surface_1"])
		alternate = QColor(palette.get("surface_hi", palette["surface_2"]))
		border = QColor(palette["border"])

		painter = QPainter(pixmap)
		painter.setRenderHint(QPainter.Antialiasing, True)
		rect = QRectF(0.75, 0.75, self.SWATCH_SIZE - 1.5, self.SWATCH_SIZE - 1.5)
		path = QPainterPath()
		path.addRoundedRect(rect, 4.0, 4.0)
		painter.setClipPath(path)
		painter.fillRect(rect, base)
		if color.alpha() < 255:
			cell = self.SWATCH_SIZE / 4.0
			for row in range(4):
				for column in range(4):
					if (row + column) % 2:
						painter.fillRect(QRectF(column * cell, row * cell, cell, cell), alternate)
		painter.fillRect(rect, color)
		painter.setClipping(False)
		painter.setPen(QPen(border, 1.0))
		painter.setBrush(Qt.NoBrush)
		painter.drawRoundedRect(rect, 4.0, 4.0)
		painter.end()
		return pixmap

	def _read_tuple(self) -> ColorTuple:
		try:
			current_value = getattr(self._section, self._attr, (1.0, 1.0, 1.0))
		except Exception:
			current_value = (1.0, 1.0, 1.0)
		if isinstance(current_value, (list, tuple)) and not current_value:
			if self._auto_color is not None:
				try:
					auto = self._auto_color()
					if auto:
						return tuple(float(component) for component in auto)  # type: ignore[return-value]
				except Exception:
					pass
		if isinstance(current_value, dict):
			current_value = tuple(current_value.get(key, 1.0) for key in ("r", "g", "b", "a")[:4])
		if not isinstance(current_value, (list, tuple)):
			return (1.0, 1.0, 1.0)
		return tuple(float(component) for component in current_value)  # type: ignore[return-value]

	def _open_dialog(self) -> None:
		current_color = _tuple_to_qcolor(self._read_tuple())
		options = QColorDialog.ColorDialogOptions()
		if self._with_alpha:
			options |= QColorDialog.ShowAlphaChannel
		new_color = QColorDialog.getColor(current_color, self, f"Pick {self._label.lower()}", options=options)
		if not new_color.isValid():
			return

		output: ColorTuple = (
			(new_color.red() / 255.0, new_color.green() / 255.0, new_color.blue() / 255.0, new_color.alpha() / 255.0)
			if self._with_alpha
			else (new_color.red() / 255.0, new_color.green() / 255.0, new_color.blue() / 255.0)
		)
		try:
			setattr(self._section, self._attr, output)
		except Exception as e:
			print(f"[color] set {self._attr}={output!r} failed: {e!r}")
			return
		self._swatch_signature = None
		self._refresh_swatch()
		self.changed.emit(output)


__all__ = ["ColorButton"]
