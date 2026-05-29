"""Shared scaffold and typography helpers for Qt settings panels."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class PanelBase(QWidget):
    """Give every settings panel the same readable content rhythm."""

    _INTRO_MAX_WIDTH = 760

    def __init__(self, config, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.setAttribute(Qt.WA_StyledBackground, True)

        self.layout: QVBoxLayout = QVBoxLayout(self)
        self.layout.setContentsMargins(28, 24, 28, 24)
        self.layout.setSpacing(8)

    def h1(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setObjectName("H1")
        self.layout.addWidget(label)
        return label

    def intro(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setObjectName("PanelIntro")
        label.setWordWrap(True)
        label.setMaximumWidth(self._INTRO_MAX_WIDTH)
        self.layout.addWidget(label)
        return label

    def section_intro(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setObjectName("SectionIntro")
        label.setWordWrap(True)
        label.setMaximumWidth(self._INTRO_MAX_WIDTH)
        self.layout.addWidget(label)
        return label

    def h2(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setObjectName("H2")
        self.layout.addWidget(label)
        return label

    def end(self) -> None:
        """Keep short panels packed against the top edge."""
        self.layout.addStretch(1)

    def add_widget(self, widget: QWidget) -> QWidget:
        """Append widget to the panel layout and return it."""
        self.layout.addWidget(widget)
        return widget
