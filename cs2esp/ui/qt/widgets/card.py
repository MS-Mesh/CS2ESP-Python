"""
Collapsible settings cards with the original compact warning design and live contextual risk data.
"""

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QEvent, QPropertyAnimation, QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from cs2esp.risk import RiskAssessment, RiskInput, resolve_risk
from cs2esp.ui.qt.widgets.help import HelpIcon, _repolish, inherit_card_risk
from cs2esp.ui.qt.widgets.toggle import ToggleSwitch
from cs2esp.ui.qt.widgets.risk_badge import RiskBadge, subscribe_risk
from cs2esp.ui.qt.widgets.reset import ResetButton


_MAX_BODY_HEIGHT = 16777215  # Qt's "no limit" widget height


class FeatureCard(QFrame):
	"""Settings section with a compact header, live metadata, and an optional bound feature toggle."""
	collapsedChanged = Signal(bool)
	settingsReset = Signal()

	def __init__(self, title: str, *, help_text: str | None = None, risk: RiskInput = "safe", toggle_target: tuple | None = None, parent=None) -> None:
		super().__init__(parent)
		self.setObjectName("FeatureCard")
		self.setFrameShape(QFrame.NoFrame)
		self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
		self.setAccessibleName(f"{title} settings")
		self._collapsed = False
		self._collapse_target = False
		self._risk_source = risk
		self._assessment = None
		self._reset_button = None
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
		accent = QFrame(header)
		accent.setObjectName("FeatureCardAccent")
		accent.setFixedSize(4, 4)
		header_layout.addWidget(accent, 0, Qt.AlignVCenter)
		title_label = QLabel(title, header)
		title_label.setObjectName("FeatureCardTitle")
		title_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
		header_layout.addWidget(title_label, 0, Qt.AlignVCenter)
		self._title_insert_idx = header_layout.count()
		self._risk_badge = RiskBadge(header)
		self._risk_badge.setObjectName("RiskBadge")
		self._risk_badge.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
		header_layout.addWidget(self._risk_badge, 0, Qt.AlignVCenter)
		header_layout.addStretch(1)

		self._header_toggle = None
		if toggle_target is not None:
			section, attr = toggle_target
			switch = ToggleSwitch(checked=bool(getattr(section, attr, False)), parent=header)
			switch.setAccessibleName(f"Enable {title}")
			switch._live_section = section  # type: ignore[attr-defined]
			switch._live_attr = attr  # type: ignore[attr-defined]

			def on_toggled(checked: bool, bound_section=section, bound_attr=attr) -> None:
				try:
					setattr(bound_section, bound_attr, bool(checked))
				except Exception as error:
					print(f'[card] header-toggle {bound_attr}={checked!r} failed: {error!r}')
				root = getattr(bound_section, "_root", None)
				if root is not None:
					try:
						root.save()
					except Exception as error:
						print(f'[card] explicit save after header toggle failed: {error!r}')

			switch.toggled.connect(on_toggled)
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
		self._collapse_animation = QPropertyAnimation(body, b"maximumHeight", self)
		self._collapse_animation.setDuration(190)
		self._collapse_animation.setEasingCurve(QEasingCurve.OutCubic)
		self._collapse_animation.finished.connect(self._on_collapse_animation_finished)

		self.setRisk(risk)

	def addBodyWidget(self, widget: QWidget) -> None:  # noqa: N802
		self._body_layout.addWidget(widget)
		inherit_card_risk(widget, self._risk_source)

	def addHeaderWidget(self, widget: QWidget) -> None:  # noqa: N802
		'''Insert a control immediately before the help button.'''
		self._hdr_layout.insertWidget(max(0, self._hdr_layout.indexOf(self._help)), widget, 0, Qt.AlignVCenter)
		inherit_card_risk(widget, self._risk_source)

	def addTitleWidget(self, widget: QWidget) -> None:  # noqa: N802
		'''Insert a control after the title and before flexible space.'''
		self._hdr_layout.insertWidget(self._title_insert_idx, widget, 0, Qt.AlignVCenter)
		self._title_insert_idx += 1
		inherit_card_risk(widget, self._risk_source)

	def insertBodyWidget(self, index: int, widget: QWidget) -> None:  # noqa: N802
		self._body_layout.insertWidget(index, widget)
		inherit_card_risk(widget, self._risk_source)

	def headerToggle(self):  # noqa: N802
		return self._header_toggle

	def setResetTarget(self, config, scopes: tuple[str, ...]) -> None:  # noqa: N802
		'''Add one scoped reset action without changing the header's feature switch.'''
		if self._reset_button is not None:
			return
		title = self.accessibleName().removesuffix(" settings")
		self._reset_button = ResetButton(title, config, scopes, self)
		self._reset_button.applied.connect(lambda _changed: self.settingsReset.emit())
		before = self._header_toggle or self._collapse_button
		self._hdr_layout.insertWidget(self._hdr_layout.indexOf(before), self._reset_button, 0, Qt.AlignVCenter)

	def isCollapsed(self) -> bool:  # noqa: N802
		return self._collapsed

	def setCollapsed(self, collapsed: bool, animate: bool = True) -> None:  # noqa: N802
		'''Expand or collapse the body without destroying its controls.'''
		collapsed = bool(collapsed)
		if collapsed == self._collapsed and self._collapse_animation.state() != QAbstractAnimation.Running:
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
			self._body.setMaximumHeight(0 if collapsed else _MAX_BODY_HEIGHT)
			self.collapsedChanged.emit(collapsed)
			return
		if collapsed:
			start_height = max(1, self._body.height())
			self._body.setMaximumHeight(start_height)
			end_height = 0
		else:
			self._body.setVisible(True)
			self._body.setMaximumHeight(_MAX_BODY_HEIGHT)
			end_height = max(1, self._body.sizeHint().height())
			self._body.setMaximumHeight(0)
			start_height = 0
		self._collapse_animation.setStartValue(start_height)
		self._collapse_animation.setEndValue(end_height)
		self._collapse_animation.start()
		self.collapsedChanged.emit(collapsed)

	def setRisk(self, risk: RiskInput) -> None:  # noqa: N802
		'''Accept legacy aliases or a live capability binding; unknown metadata never becomes approval.'''
		self._risk_source = risk
		self._help.setRisk(risk)
		inherit_card_risk(self, risk)
		self.refreshRisk()
		subscribe_risk(self)

	def refreshRisk(self, assessment: RiskAssessment | None = None) -> RiskAssessment:  # noqa: N802
		'''Refresh badge, accessibility metadata and open help without requiring a panel reopen.'''
		assessment = assessment or resolve_risk(self._risk_source)
		if assessment == self._assessment:
			return assessment
		self._assessment = assessment
		self.setProperty("risk", assessment.bucket)
		self.setProperty("policy", assessment.policy)
		self.setProperty("capabilityId", assessment.capability_id)
		self.setAccessibleDescription(assessment.plain_text)
		self._accent.setProperty("risk", assessment.bucket)
		self._risk_badge.setAssessment(assessment)
		for widget in (self, self._accent, self._risk_badge):
			_repolish(widget)
		self._help.refreshRisk(assessment)
		return assessment

	def riskAssessment(self) -> RiskAssessment:  # noqa: N802
		return self.refreshRisk()

	def setGroupRole(self, role: str | None) -> None:  # noqa: N802
		'''Tag a parent/child relationship for QSS hierarchy.'''
		self.setProperty("groupRole", role if role in ("parent", "child") else "")
		_repolish(self)

	def apply_theme(self, theme_name: str) -> None:
		if self._reset_button is not None:
			self._reset_button.apply_theme(theme_name)
		if self._header_toggle is not None:
			self._header_toggle.apply_theme(theme_name)
		self._help.apply_theme(theme_name)
		for widget in (self, self._accent, self._risk_badge):
			_repolish(widget)

	def changeEvent(self, event) -> None:  # noqa: N802
		super().changeEvent(event)
		if event.type() == QEvent.EnabledChange:
			self.setProperty("state", "active" if self.isEnabled() else "disabled")
			_repolish(self)

	def _sync_collapse_button(self, collapsed: bool) -> None:
		blocker = QSignalBlocker(self._collapse_button)
		self._collapse_button.setChecked(collapsed)
		del blocker
		self._collapse_button.setArrowType(Qt.RightArrow if collapsed else Qt.DownArrow)
		self._collapse_button.setAccessibleName(("Expand " if collapsed else "Collapse ") + self.accessibleName().removesuffix(" settings"))
		self._collapse_button.setToolTip("Expand section" if collapsed else "Collapse section")

	def _on_collapse_animation_finished(self) -> None:
		if self._collapse_target:
			self._body.setVisible(False)
			self._body.setMaximumHeight(0)
		else:
			self._body.setMaximumHeight(_MAX_BODY_HEIGHT)
