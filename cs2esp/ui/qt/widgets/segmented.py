"""
Compact segmented selector bound to a config string.
"""

from typing import Any

from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QSizePolicy, QWidget

from cs2esp.ui.qt.widgets.choice import _ConfigValueMixin
from cs2esp.ui.qt.widgets.help import _repolish


class SegmentedSelector(_ConfigValueMixin, QWidget):
	"""Two-or-more keyboard-accessible buttons for one config value."""
	_LOG_PREFIX = "segmented"
	changed = Signal(str)

	def __init__(
		self,
		section: Any,
		attr: str,
		options: list[tuple[str, str]] | tuple[tuple[str, str], ...],
		*,
		default: str,
		equal_width: bool = False,
		parent: QWidget | None = None,
	) -> None:
		super().__init__(parent)
		self._section = section
		self._attr = attr
		self._options = list(options)
		if not self._options:
			raise ValueError("SegmentedSelector requires at least one option")
		self._default = default
		self._equal_width = equal_width
		self.setObjectName("SegmentedSelector")
		self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)

		layout = QHBoxLayout(self)
		layout.setContentsMargins(0, 2, 0, 2)
		layout.setSpacing(4)

		self._group = QButtonGroup(self)
		self._group.setExclusive(True)
		self._buttons: list[QPushButton] = []
		current_value = self._get()
		option_count = len(self._options)
		for index, (value, display) in enumerate(self._options):
			button = QPushButton(display, self)
			button.setObjectName("SegmentedButton")
			button.setCheckable(True)
			button.setAutoDefault(False)
			button.setCursor(Qt.PointingHandCursor)
			button.setFocusPolicy(Qt.StrongFocus)
			button.setProperty("seg_value", value)
			button.setProperty("segmentPosition", self._position_name(index, option_count))
			button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
			button.setChecked(value == current_value)
			self._group.addButton(button)
			self._buttons.append(button)
			layout.addWidget(button, 0)
		layout.addStretch(1)

		self._apply_selected_style(current_value)
		for button in self._buttons:
			button.toggled.connect(self._on_toggled)

	@staticmethod
	def _position_name(index: int, count: int) -> str:
		if count == 1:
			return "only"
		if index == 0:
			return "first"
		if index == count - 1:
			return "last"
		return "middle"

	def showEvent(self, event) -> None:  # noqa: N802
		super().showEvent(event)
		self._equalize_button_widths()

	def changeEvent(self, event) -> None:  # noqa: N802
		super().changeEvent(event)
		if getattr(self, "_equal_width", False) and event.type() in {QEvent.FontChange, QEvent.StyleChange, QEvent.ApplicationFontChange}:
			QTimer.singleShot(0, self._equalize_button_widths)

	def setValue(self, value: str) -> None:  # noqa: N802
		for button in self._buttons:
			if button.property("seg_value") == value:
				button.setChecked(True)
				return

	def apply_theme(self, theme_name: str) -> None:
		'''Refresh selector and button QSS after a live theme change.'''
		self.setProperty("themeName", theme_name)
		_repolish(self)
		for button in self._buttons:
			button.setProperty("themeName", theme_name)
			_repolish(button)
		self._equalize_button_widths()

	def _equalize_button_widths(self) -> None:
		if not self._equal_width or not self._buttons:
			return
		for button in self._buttons:
			button.setMinimumWidth(0)
		widest = max(button.sizeHint().width() for button in self._buttons)
		for button in self._buttons:
			button.setMinimumWidth(widest)

	def _apply_selected_style(self, value: str) -> None:
		for button in self._buttons:
			selected = button.property("seg_value") == value
			button.setProperty("selected", selected)
			button.setProperty("role", "primary" if selected else "")
			_repolish(button)

	def _on_toggled(self, checked: bool) -> None:
		if not checked:
			return
		sender = self.sender()
		if sender is None:
			return
		value = sender.property("seg_value")
		if value is None:
			return
		value = str(value)
		self._apply_selected_style(value)
		self._set(value)
		self.changed.emit(value)


__all__ = ["SegmentedSelector"]
