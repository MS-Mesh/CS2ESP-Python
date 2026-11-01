"""
Icon-only parameter reset with an explicit Yes/No confirmation and one saved configuration transaction.
"""

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QToolButton, QVBoxLayout

from cs2esp.settings_reset import reset_parameters
from cs2esp.ui.qt import theme as qtheme


def _restore_icon(palette: dict[str, str]) -> QIcon:
	'''Centre the painted path itself rather than a font glyph's advance box.'''
	path = QPainterPath(QPointF(4.0, 7.0))
	path.cubicTo(5.0, 2.0, 12.0, 2.0, 13.0, 7.0)
	path.cubicTo(14.0, 11.0, 10.0, 15.0, 6.0, 12.0)
	path.moveTo(4.0, 3.0)
	path.lineTo(4.0, 7.0)
	path.lineTo(8.0, 7.0)
	centre = path.boundingRect().center()
	result = QIcon()
	for mode, colour in ((QIcon.Normal, palette["text_dim"]), (QIcon.Active, palette["text"]), (QIcon.Disabled, palette["text_mute"])):
		pixmap = QPixmap(32, 32)
		pixmap.setDevicePixelRatio(2.0)
		pixmap.fill(Qt.transparent)
		painter = QPainter(pixmap)
		painter.setRenderHint(QPainter.Antialiasing)
		painter.setPen(QPen(QColor(colour), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
		painter.translate(QPointF(8.0, 8.0) - centre)
		painter.drawPath(path)
		painter.end()
		result.addPixmap(pixmap, mode)
	return result


class _ResetConfirmation(QDialog):
	"""Theme-owned modal retaining the existing Yes/No result contract."""
	def __init__(self, title: str, theme_name: str, parent) -> None:
		super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
		self.setObjectName("ResetConfirmation")
		self.setWindowTitle(f"Reset {title}?")
		self.setWindowModality(Qt.WindowModal)
		self.setAttribute(Qt.WA_TranslucentBackground)
		self.setMinimumWidth(440)
		self.setMaximumWidth(560)
		outer = QVBoxLayout(self)
		outer.setContentsMargins(0, 0, 0, 0)
		surface = QFrame(self)
		surface.setObjectName("ResetSurface")
		outer.addWidget(surface)
		body = QVBoxLayout(surface)
		body.setContentsMargins(24, 22, 24, 22)
		body.setSpacing(18)
		header = QHBoxLayout()
		header.setSpacing(12)
		self._mark = QLabel(surface)
		self._mark.setObjectName("ResetMark")
		self._mark.setFixedSize(36, 36)
		self._mark.setAlignment(Qt.AlignCenter)
		header.addWidget(self._mark)
		caption = QLabel(f"Reset {title}?", surface)
		caption.setObjectName("ResetTitle")
		caption.setWordWrap(True)
		header.addWidget(caption, 1)
		close = QToolButton(surface)
		close.setObjectName("ResetClose")
		close.setText("×")
		close.setFixedSize(26, 26)
		close.setAccessibleName("Cancel reset")
		close.setCursor(Qt.PointingHandCursor)
		close.clicked.connect(self.reject)
		header.addWidget(close, 0, Qt.AlignTop)
		body.addLayout(header)
		message = QLabel("Restore this component’s saved default parameters, colours and sizes?", surface)
		message.setObjectName("ResetBody")
		message.setWordWrap(True)
		body.addWidget(message)
		note = QLabel("Switches and keybinds stay unchanged.", surface)
		note.setObjectName("ResetNote")
		note.setWordWrap(True)
		body.addWidget(note)
		actions = QHBoxLayout()
		actions.setSpacing(10)
		actions.addStretch(1)
		self._no = QPushButton("No", surface)
		self._no.setObjectName("ResetCancel")
		self._no.setDefault(True)
		self._yes = QPushButton("Yes, reset", surface)
		self._yes.setObjectName("ResetAccept")
		for button in (self._no, self._yes):
			button.setMinimumSize(100, 36)
			button.setCursor(Qt.PointingHandCursor)
			actions.addWidget(button)
		self._no.clicked.connect(lambda: self.done(QMessageBox.No))
		self._yes.clicked.connect(lambda: self.done(QMessageBox.Yes))
		body.addLayout(actions)
		self.apply_theme(theme_name)

	def button(self, standard_button):
		return self._yes if standard_button == QMessageBox.Yes else self._no

	def defaultButton(self):
		return self._no

	def apply_theme(self, theme_name: str) -> None:
		palette = qtheme.PALETTES.get(theme_name, qtheme.PALETTES["slate"])
		self._mark.setPixmap(_restore_icon(palette).pixmap(20, 20))

	def showEvent(self, event) -> None:
		super().showEvent(event)
		self.adjustSize()
		parent = self.parentWidget()
		if parent is not None:
			centre = parent.mapToGlobal(parent.rect().center())
			position = centre - self.rect().center()
			available = self.screen().availableGeometry()
			self.move(max(available.left(), min(position.x(), available.right() - self.width() + 1)),
				max(available.top(), min(position.y(), available.bottom() - self.height() + 1)))
		self._no.setFocus(Qt.OtherFocusReason)


class ResetButton(QToolButton):
	"""Restore only the selected component's parameters after confirmation."""
	applied = Signal(object)

	def __init__(self, title: str, config, scopes: tuple[str, ...], parent=None) -> None:
		super().__init__(parent)
		self.setObjectName("SettingsReset")
		self._title = title
		self._config = config
		self._scopes = tuple(scopes)
		self._dialog = None
		self.setCursor(Qt.PointingHandCursor)
		self.setFocusPolicy(Qt.StrongFocus)
		self.setAccessibleName(f"Reset {title} parameters")
		self.setToolTip("Restore saved default parameters, colours and sizes. Switches and keybinds stay unchanged.")
		self.clicked.connect(self._confirm_reset)
		self.apply_theme("slate")

	def apply_theme(self, theme_name: str) -> None:
		self._theme_name = theme_name
		palette = qtheme.PALETTES.get(theme_name, qtheme.PALETTES["slate"])
		self.setIconSize(QSize(16, 16))
		self.setText("")
		self.setIcon(_restore_icon(palette))

	def _confirm_reset(self) -> None:
		if self._dialog is not None:
			self._dialog.raise_()
			return
		dialog = _ResetConfirmation(self._title, self._theme_name, self.window())
		dialog.finished.connect(self._finish_reset)
		self._dialog = dialog
		dialog.open()

	def _finish_reset(self, result: int) -> None:
		dialog, self._dialog = self._dialog, None
		if dialog is not None:
			dialog.deleteLater()
		if result != QMessageBox.Yes:
			return
		try:
			changed = reset_parameters(self._config, self._scopes)
		except (OSError, ValueError, TypeError) as error:
			QMessageBox.warning(self.window(), "Reset not applied", str(error))
			return
		self.applied.emit(changed)
