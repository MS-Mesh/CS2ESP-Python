"""Quiet, collapsible settings-card container."""

from PySide6.QtCore import (
    QAbstractAnimation, QEasingCurve, QEvent, QPropertyAnimation,
    QSignalBlocker, Qt, Signal,
)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout,
    QWidget,
)

from cs2esp.ui.qt.widgets.help import HelpIcon


_ACCENT_BUCKET = {
    "safe": "safe",
    "medium": "medium",
    "medium_faceit_only": "medium",
    "high": "high",
    "high_faceit": "high",
    "high_both": "high",
}


def _repolish(widget: QWidget) -> None:
    """Refresh selectors that depend on a dynamic Qt property."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


class FeatureCard(QFrame):
    """Settings section with a compact header and optional live toggle."""

    collapsedChanged = Signal(bool)

    def __init__(self, title: str, *,
                 help_text: str | None = None,
                 risk: str = "safe",
                 toggle_target: tuple | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("FeatureCard")
        self.setFrameShape(QFrame.NoFrame)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.setAccessibleName(f"{title} settings")

        self._collapsed = False
        self._collapse_target = False
        self.setProperty("collapsed", False)
        self.setProperty("state", "active")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget(self)
        header.setObjectName("FeatureCardHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 11, 14, 11)
        header_layout.setSpacing(10)

        # Kept for QSS compatibility, but rendered as a restrained risk marker.
        accent = QFrame(header)
        accent.setObjectName("FeatureCardAccent")
        accent.setFixedSize(4, 4)
        header_layout.addWidget(accent, 0, Qt.AlignVCenter)

        title_label = QLabel(title, header)
        title_label.setObjectName("FeatureCardTitle")
        title_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        header_layout.addWidget(title_label, 0, Qt.AlignVCenter)
        self._title_insert_idx = header_layout.count()

        self._risk_badge = QLabel(header)
        self._risk_badge.setObjectName("RiskBadge")
        self._risk_badge.setSizePolicy(
            QSizePolicy.Maximum, QSizePolicy.Preferred
        )
        header_layout.addWidget(self._risk_badge, 0, Qt.AlignVCenter)
        header_layout.addStretch(1)

        self._header_toggle = None
        if toggle_target is not None:
            from cs2esp.ui.qt.widgets.toggle import ToggleSwitch
            section, attr = toggle_target
            initial = bool(getattr(section, attr, False))
            switch = ToggleSwitch(checked=initial, parent=header)
            switch.setAccessibleName(f"Enable {title}")
            switch._live_section = section  # type: ignore[attr-defined]
            switch._live_attr = attr  # type: ignore[attr-defined]

            def _on_toggled(checked: bool,
                            bound_section=section, bound_attr=attr) -> None:
                try:
                    setattr(bound_section, bound_attr, bool(checked))
                except Exception as e:
                    print(f"[card] header-toggle {bound_attr}={checked!r} "
                          f"failed: {e!r}")
                root = getattr(bound_section, "_root", None)
                if root is not None:
                    try:
                        root.save()
                    except Exception as e:
                        print("[card] explicit save after header toggle "
                              f"failed: {e!r}")

            switch.toggled.connect(_on_toggled)
            header_layout.addWidget(switch, 0, Qt.AlignVCenter)
            self._header_toggle = switch

        self._collapse_button = QToolButton(header)
        self._collapse_button.setObjectName("FeatureCardCollapse")
        self._collapse_button.setCheckable(True)
        self._collapse_button.setArrowType(Qt.DownArrow)
        self._collapse_button.setCursor(Qt.PointingHandCursor)
        self._collapse_button.setFocusPolicy(Qt.StrongFocus)
        self._collapse_button.setAccessibleName(f"Collapse {title}")
        self._collapse_button.setToolTip("Collapse section")
        self._collapse_button.toggled.connect(self.setCollapsed)
        header_layout.addWidget(self._collapse_button, 0, Qt.AlignVCenter)

        self._help = HelpIcon(help_text or "", risk=risk, parent=header)
        self._help.setAccessibleName(f"Help for {title}")
        header_layout.addWidget(self._help, 0, Qt.AlignVCenter)

        self._hdr_layout = header_layout
        outer.addWidget(header)

        body = QWidget(self)
        body.setObjectName("FeatureCardBody")
        body.setMinimumHeight(0)
        body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._body_layout = QVBoxLayout(body)
        self._body_layout.setContentsMargins(16, 8, 16, 16)
        self._body_layout.setSpacing(9)
        outer.addWidget(body)

        self._body = body
        self._accent = accent
        self._collapse_animation = QPropertyAnimation(
            body, b"maximumHeight", self)
        self._collapse_animation.setDuration(190)
        self._collapse_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._collapse_animation.finished.connect(
            self._on_collapse_animation_finished)
        self.setRisk(risk)

    def addBodyWidget(self, widget: QWidget) -> None:  # noqa: N802
        self._body_layout.addWidget(widget)

    def addHeaderWidget(self, widget: QWidget) -> None:  # noqa: N802
        """Insert a control immediately before the help button."""
        insert_index = max(0, self._hdr_layout.indexOf(self._help))
        self._hdr_layout.insertWidget(
            insert_index, widget, 0, Qt.AlignVCenter)

    def addTitleWidget(self, widget: QWidget) -> None:  # noqa: N802
        """Insert a control after the title and before flexible space."""
        self._hdr_layout.insertWidget(
            self._title_insert_idx, widget, 0, Qt.AlignVCenter)
        self._title_insert_idx += 1

    def insertBodyWidget(self, index: int, widget: QWidget) -> None:  # noqa: N802
        self._body_layout.insertWidget(index, widget)

    def headerToggle(self):  # noqa: N802
        """Return the header ToggleSwitch, if this card owns one."""
        return self._header_toggle

    def isCollapsed(self) -> bool:  # noqa: N802
        return self._collapsed

    def setCollapsed(self, collapsed: bool, animate: bool = True) -> None:  # noqa: N802
        """Expand or collapse the body without destroying its controls."""
        collapsed = bool(collapsed)
        if (collapsed == self._collapsed
                and self._collapse_animation.state()
                != QAbstractAnimation.Running):
            self._sync_collapse_button(collapsed)
            return

        self._collapsed = collapsed
        self._collapse_target = collapsed
        self.setProperty("collapsed", collapsed)
        _repolish(self)
        self._sync_collapse_button(collapsed)

        self._collapse_animation.stop()
        if not animate or not self.isVisible():
            self._body.setVisible(not collapsed)
            self._body.setMaximumHeight(0 if collapsed else 16777215)
            self.collapsedChanged.emit(collapsed)
            return

        if collapsed:
            start_height = max(1, self._body.height())
            self._body.setMaximumHeight(start_height)
            end_height = 0
        else:
            self._body.setVisible(True)
            self._body.setMaximumHeight(16777215)
            end_height = max(1, self._body.sizeHint().height())
            self._body.setMaximumHeight(0)
            start_height = 0

        self._collapse_animation.setStartValue(start_height)
        self._collapse_animation.setEndValue(end_height)
        self._collapse_animation.start()
        self.collapsedChanged.emit(collapsed)

    def setRisk(self, risk: str) -> None:  # noqa: N802
        """Update the semantic risk state used by QSS and help UI."""
        bucket = _ACCENT_BUCKET.get(risk, "safe")
        self.setProperty("risk", bucket)
        self._accent.setProperty("risk", bucket)
        self._risk_badge.setProperty("risk", bucket)
        self._risk_badge.setText(
            "CAUTION" if bucket == "medium"
            else "HIGH RISK" if bucket == "high"
            else ""
        )
        self._risk_badge.setVisible(bucket != "safe")
        _repolish(self)
        _repolish(self._accent)
        _repolish(self._risk_badge)
        self._help.setRisk(risk)

    def setGroupRole(self, role: str | None) -> None:  # noqa: N802
        """Tag a parent/child card relationship for QSS hierarchy."""
        if role not in (None, "parent", "child"):
            role = None
        self.setProperty("groupRole", role or "")
        _repolish(self)

    def apply_theme(self, theme_name: str) -> None:
        """Refresh custom descendants after the application theme changes."""
        if self._header_toggle is not None:
            self._header_toggle.apply_theme(theme_name)
        _repolish(self)
        _repolish(self._accent)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.EnabledChange:
            self.setProperty("state", "active" if self.isEnabled()
                             else "disabled")
            _repolish(self)

    def _sync_collapse_button(self, collapsed: bool) -> None:
        blocker = QSignalBlocker(self._collapse_button)
        self._collapse_button.setChecked(collapsed)
        del blocker
        self._collapse_button.setArrowType(
            Qt.RightArrow if collapsed else Qt.DownArrow)
        self._collapse_button.setAccessibleName(
            ("Expand " if collapsed else "Collapse ")
            + self.accessibleName().removesuffix(" settings"))
        self._collapse_button.setToolTip(
            "Expand section" if collapsed else "Collapse section")

    def _on_collapse_animation_finished(self) -> None:
        if self._collapse_target:
            self._body.setVisible(False)
            self._body.setMaximumHeight(0)
        else:
            self._body.setMaximumHeight(16777215)
