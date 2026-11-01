"""
Shared scaffold and typography helpers for Qt settings panels.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QLayout, QSizePolicy, QVBoxLayout, QWidget

from cs2esp.ui.qt.widgets.card import FeatureCard


class PanelBase(QWidget):
	"""Give every settings panel the same readable content rhythm."""
	settingsReset = Signal()
	RESET_SCOPES: dict[str, tuple[str, ...]] = {}

	def __init__(self, config, parent=None) -> None:
		super().__init__(parent)
		self.config = config
		self.setAttribute(Qt.WA_StyledBackground, True)
		# Let the scroll area use height-for-width instead of retaining width-blind wrapped-row minima
		self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)

		self.layout: QVBoxLayout = QVBoxLayout(self)
		self.layout.setContentsMargins(28, 24, 28, 24)
		self.layout.setSpacing(8)
		self.layout.setAlignment(Qt.AlignTop)
		self.layout.setSizeConstraint(QLayout.SetNoConstraint)

	def h1(self, text: str) -> QLabel:
		'''Add a page heading label.'''
		label = QLabel(text, self)
		label.setObjectName("H1")
		self.layout.addWidget(label)
		return label

	def intro(self, text: str) -> QLabel:
		'''Add a wrapped page introduction label.'''
		label = QLabel(text, self)
		label.setObjectName("PanelIntro")
		label.setWordWrap(True)
		self.layout.addWidget(label)
		return label

	def section_intro(self, text: str) -> QLabel:
		'''Add a wrapped section introduction label.'''
		label = QLabel(text, self)
		label.setObjectName("SectionIntro")
		label.setWordWrap(True)
		self.layout.addWidget(label)
		return label

	def h2(self, text: str) -> QLabel:
		'''Add a section heading label.'''
		label = QLabel(text, self)
		label.setObjectName("H2")
		self.layout.addWidget(label)
		return label

	def end(self) -> None:
		'''Commit the content extent; viewport slack belongs outside the page layout.'''
		for card in self.findChildren(FeatureCard):
			scopes = self.RESET_SCOPES.get(card.accessibleName().removesuffix(" settings"))
			if scopes and card._reset_button is None:
				card.setResetTarget(self.config, scopes)
				card.settingsReset.connect(self.settingsReset)
		self.layout.activate()

	def add_widget(self, widget: QWidget) -> QWidget:
		'''Append widget to the panel layout and return it.'''
		self.layout.addWidget(widget)
		return widget
