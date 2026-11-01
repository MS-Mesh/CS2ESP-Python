"""
Config-bound radio and combo controls for fixed string choices.
"""

from typing import Any

from PySide6.QtCore import QEvent, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QButtonGroup, QComboBox, QGridLayout, QHBoxLayout, QLabel, QRadioButton, QSizePolicy, QStyle, QStyleOptionComboBox, QWidget

from cs2esp.ui.qt import theme as qtheme
from cs2esp.ui.qt.widgets.help import _repolish


class _ConfigValueMixin:
	"""Read and write one validated string field on a bound configuration section."""
	_LOG_PREFIX = "choice"

	def value(self) -> str:
		return self._get()

	def _get(self) -> str:
		try:
			current_value = str(getattr(self._section, self._attr, self._default))
		except Exception:
			current_value = self._default
		valid = {value for value, _ in self._options}
		return current_value if current_value in valid else self._default

	def _set(self, value: str) -> None:
		try:
			setattr(self._section, self._attr, value)
		except Exception as error:
			print(f"[{self._LOG_PREFIX}] set {self._attr}={value!r} failed: {error!r}")


class ChoiceRow(_ConfigValueMixin, QWidget):
	"""Radio row bound directly to a config string field."""
	_LOG_PREFIX = "choice"
	changed = Signal(str)

	def __init__(
		self,
		label: str,
		section: Any,
		attr: str,
		options: list[tuple[str, str]] | tuple[tuple[str, str], ...],
		*,
		default: str | None = None,
		parent: QWidget | None = None,
	) -> None:
		super().__init__(parent)
		self._section = section
		self._attr = attr
		self._options = list(options)
		if not self._options:
			raise ValueError("ChoiceRow requires at least one option")
		self._default = default or self._options[0][0]
		self.setObjectName("ChoiceRow")
		self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

		self._layout = QGridLayout(self)
		self._layout.setContentsMargins(0, 3, 0, 3)
		self._layout.setHorizontalSpacing(12)
		self._layout.setVerticalSpacing(6)

		self._label_widget: QLabel | None = None
		if label:
			label_widget = QLabel(label, self)
			label_widget.setObjectName("ChoiceLabel")
			label_widget.setWordWrap(True)
			label_widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
			self._label_widget = label_widget

		self._group = QButtonGroup(self)
		self._group.setExclusive(True)
		self._buttons: list[QRadioButton] = []
		current_value = self._get()
		for value, display in self._options:
			radio_button = QRadioButton(display, self)
			radio_button.setObjectName("ChoiceOption")
			radio_button.setProperty("choice_value", value)
			radio_button.setCursor(Qt.PointingHandCursor)
			radio_button.setFocusPolicy(Qt.StrongFocus)
			radio_button.setChecked(value == current_value)
			radio_button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
			radio_button.toggled.connect(self._on_toggled)
			self._group.addButton(radio_button)
			self._buttons.append(radio_button)

		self._compact: bool | None = None
		self._set_compact(False)

	def setValue(self, value: str) -> None:  # noqa: N802
		for button in self._group.buttons():
			if button.property("choice_value") == value:
				button.setChecked(True)
				return

	def apply_theme(self, theme_name: str) -> None:
		'''Refresh semantic QSS properties after a live theme change.'''
		self.setProperty("themeName", theme_name)
		_repolish(self)
		for button in self._group.buttons():
			_repolish(button)
		self._update_layout_mode()

	def resizeEvent(self, event) -> None:  # noqa: N802
		super().resizeEvent(event)
		self._update_layout_mode(event.size().width())

	def changeEvent(self, event) -> None:  # noqa: N802
		super().changeEvent(event)
		if event.type() in (QEvent.FontChange, QEvent.StyleChange):
			self._update_layout_mode()

	def _update_layout_mode(self, width: int | None = None) -> None:
		width = self.width() if width is None else width
		label_width = self._label_widget.sizeHint().width() if self._label_widget is not None else 0
		options_width = sum(button.sizeHint().width() for button in self._buttons)
		gaps = 12 * max(0, len(self._buttons))
		required_width = label_width + options_width + gaps
		compact = self.fontMetrics().height() >= 28 or (width > 0 and width < required_width)
		self._set_compact(compact)

	def _set_compact(self, compact: bool) -> None:
		if compact == self._compact:
			return
		widgets = [*self._buttons]
		if self._label_widget is not None:
			widgets.insert(0, self._label_widget)
		for widget in widgets:
			self._layout.removeWidget(widget)
		for column in range(len(self._buttons) + 2):
			self._layout.setColumnStretch(column, 0)

		if compact:
			row = 0
			if self._label_widget is not None:
				self._layout.addWidget(self._label_widget, row, 0, 1, 2)
				row += 1
			for button in self._buttons:
				self._layout.addWidget(button, row, 0)
				row += 1
			self._layout.setColumnStretch(0, 0)
			self._layout.setColumnStretch(1, 1)
		else:
			column = 0
			if self._label_widget is not None:
				self._layout.addWidget(self._label_widget, 0, column)
				column += 1
			for button in self._buttons:
				self._layout.addWidget(button, 0, column)
				column += 1
			self._layout.setColumnStretch(column, 1)

		self._compact = compact
		self.setProperty("layoutMode", "compact" if compact else "wide")
		self.updateGeometry()

	def _on_toggled(self, checked: bool) -> None:
		if not checked:
			return
		sender = self.sender()
		if sender is None:
			return
		value = sender.property("choice_value")
		if value is None:
			return
		value = str(value)
		self._set(value)
		self.changed.emit(value)


class _ChevronCombo(QComboBox):
	"""Combo box with a reliable vector chevron on Windows."""
	def __init__(self, parent: QWidget | None = None, *, theme_name: str = "slate") -> None:
		super().__init__(parent)
		self._theme_name = theme_name or "slate"
		self.setObjectName("ChoiceCombo")
		self.setProperty("customChevron", True)
		self.setFocusPolicy(Qt.StrongFocus)
		self.setCursor(Qt.PointingHandCursor)

	def apply_theme(self, theme_name: str) -> None:
		self._theme_name = theme_name or self._theme_name
		self.setProperty("themeName", self._theme_name)
		_repolish(self)

	def paintEvent(self, event) -> None:  # noqa: N802
		super().paintEvent(event)

		palette = qtheme.PALETTES.get(self._theme_name, next(iter(qtheme.PALETTES.values())))
		option = QStyleOptionComboBox()
		self.initStyleOption(option)
		arrow_rect = self.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxArrow, self)
		if not arrow_rect.isValid():
			return

		active = self.underMouse() or self.hasFocus() or self.view().isVisible()
		surface_key = "surface_hi" if active else "surface_2"
		surface = QColor(palette[surface_key])
		arrow_key = "text_dim" if self.isEnabled() else "text_mute"
		arrow = QColor(palette[arrow_key])

		painter = QPainter(self)
		painter.setRenderHint(QPainter.Antialiasing, True)
		cover = arrow_rect.adjusted(2, 2, -2, -2)
		painter.setPen(Qt.NoPen)
		painter.setBrush(surface)
		painter.drawRect(cover)

		pen = QPen(arrow, 1.6)
		pen.setCapStyle(Qt.RoundCap)
		pen.setJoinStyle(Qt.RoundJoin)
		painter.setPen(pen)
		center = arrow_rect.center()
		half_width = min(4.0, max(2.5, arrow_rect.width() * 0.16))
		painter.drawPolyline(
			[QPointF(center.x() - half_width, center.y() - 2.0), QPointF(float(center.x()), center.y() + 2.5), QPointF(center.x() + half_width, center.y() - 2.0)]
		)


class ComboRow(_ConfigValueMixin, QWidget):
	"""Compact dropdown row bound directly to a config string field."""
	_LOG_PREFIX = "combo"
	changed = Signal(str)

	def __init__(
		self,
		label: str,
		section: Any,
		attr: str,
		options: list[tuple[str, str]] | tuple[tuple[str, str], ...],
		*,
		default: str,
		theme_name: str | None = None,
		parent: QWidget | None = None,
	) -> None:
		super().__init__(parent)
		self._section = section
		self._attr = attr
		self._options = list(options)
		if not self._options:
			raise ValueError("ComboRow requires at least one option")
		self._default = default
		self.setObjectName("ComboRow")
		self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

		layout = QHBoxLayout(self)
		layout.setContentsMargins(0, 3, 0, 3)
		layout.setSpacing(12)

		label_widget = None
		if label:
			label_widget = QLabel(label, self)
			label_widget.setObjectName("ChoiceLabel")
			label_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
			layout.addWidget(label_widget, 0)

		self._combo = _ChevronCombo(self, theme_name=theme_name or "slate")
		self._combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
		for value, display in self._options:
			self._combo.addItem(display, value)
		index = self._combo.findData(self._get())
		if index >= 0:
			self._combo.setCurrentIndex(index)
		if label_widget is not None:
			if "&" not in label:
				label_widget.setBuddy(self._combo)
			self._combo.setAccessibleName(label)
		layout.addWidget(self._combo, 0)
		layout.addStretch(1)

		self._combo.currentIndexChanged.connect(self._on_changed)

	def setValue(self, value: str) -> None:  # noqa: N802
		index = self._combo.findData(value)
		if index >= 0:
			self._combo.setCurrentIndex(index)

	def apply_theme(self, theme_name: str) -> None:
		self.setProperty("themeName", theme_name)
		self._combo.apply_theme(theme_name)
		_repolish(self)

	def _on_changed(self, index: int) -> None:
		if index < 0:
			return
		value = self._combo.itemData(index)
		if value is None:
			return
		value = str(value)
		self._set(value)
		self.changed.emit(value)


__all__ = ["ChoiceRow", "ComboRow"]
