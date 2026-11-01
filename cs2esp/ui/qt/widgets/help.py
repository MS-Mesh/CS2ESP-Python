"""
Theme-aware hover and pinned help with centrally audited policy metadata. Live bindings refresh open popovers without recreating them.
"""

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, QTimer, Qt
from PySide6.QtGui import QFontMetrics, QTextDocument
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QToolTip, QVBoxLayout, QWidget

from cs2esp.risk import RiskAssessment, RiskBinding, RiskInput, help_html, resolve_risk
from cs2esp.ui.qt import theme as qtheme
from cs2esp.ui.qt.icons import HAS_ICONS, icon
from cs2esp.ui.qt.widgets.risk_badge import subscribe_risk


def _repolish(widget: QWidget) -> None:
	'''Refresh selectors that depend on a dynamic Qt property.'''
	style = widget.style()
	style.unpolish(widget)
	style.polish(widget)
	widget.update()


class HelpIcon(QPushButton):
	"""Accessible contextual help button; its risk source may be a mutable configuration binding."""
	def __init__(self, tooltip: str, risk: RiskInput = "safe", parent=None) -> None:
		super().__init__(parent)
		self.setObjectName("HelpIcon")
		self.setFlat(True)
		self.setCursor(Qt.WhatsThisCursor)
		self.setFocusPolicy(Qt.StrongFocus)
		self.setAccessibleName("Show help")
		self._raw_text = tooltip or ""
		self._risk_source = risk
		self._assessment = None
		self._risk = "medium"
		self._theme_name = "slate"
		self._pinned: _PinnedTooltip | None = None
		self._hover: _PinnedTooltip | None = None
		self.setToolTip("")
		self.clicked.connect(self._toggle_pinned)
		self.setRisk(risk)

	def apply_theme(self, theme_name: str) -> None:
		'''Retint the vector glyph; QSS risk buckets are presentation only.'''
		self._theme_name = theme_name or self._theme_name
		if not HAS_ICONS:
			self.setText("?")
			return
		palette = qtheme.PALETTES.get(self._theme_name, qtheme.PALETTES["slate"])
		color_key = {"medium": "warn", "high": "danger"}.get(self._risk, "text_mute")
		try:
			self.setText("")
			self.setIcon(icon("mdi.help", color=palette[color_key], color_active=palette["text"]))
			self.setIconSize(QSize(12, 12))
		except Exception:
			self.setText("?")

	def setRisk(self, level: RiskInput) -> None:  # noqa: N802
		'''Preserve string alias compatibility while allowing live capability metadata.'''
		self._risk_source = level
		self.refreshRisk()
		subscribe_risk(self)

	def refreshRisk(self, assessment: RiskAssessment | None = None) -> RiskAssessment:  # noqa: N802
		'''Update already-open help when mutable mode/configuration values change.'''
		assessment = assessment or resolve_risk(self._risk_source)
		if assessment == self._assessment:
			return assessment
		self._assessment = assessment
		self._risk = assessment.bucket
		self.setProperty("risk", self._risk)
		self.setProperty("capabilityId", assessment.capability_id)
		self.setAccessibleDescription(assessment.plain_text)
		self.style().unpolish(self)
		self.style().polish(self)
		self.apply_theme(self._theme_name)
		for popup in (self._pinned, self._hover):
			if popup is not None:
				popup.setContent(self._raw_text, assessment)
		return assessment

	def riskAssessment(self) -> RiskAssessment:  # noqa: N802
		return self.refreshRisk()

	def enterEvent(self, event) -> None:  # noqa: N802
		super().enterEvent(event)
		if self._pinned is not None:
			return
		self._close_hover()
		self._hover = _PinnedTooltip(self._raw_text, self.refreshRisk(), anchor=self, ephemeral=True)
		self._hover.destroyed.connect(self._on_hover_destroyed)
		self._hover.show()
		self._hover.raise_()

	def leaveEvent(self, event) -> None:  # noqa: N802
		super().leaveEvent(event)
		self._close_hover()

	def _close_hover(self) -> None:
		if self._hover is not None:
			self._hover.close()
			self._hover = None

	def _on_hover_destroyed(self, *_args) -> None:
		self._hover = None

	def _toggle_pinned(self) -> None:
		if self._pinned is not None:
			self._pinned.close()
			self._pinned = None
			return
		self._close_hover()
		QToolTip.hideText()
		popup = _PinnedTooltip(self._raw_text, self.refreshRisk(), anchor=self, ephemeral=False)
		popup.destroyed.connect(self._on_pinned_destroyed)
		self._pinned = popup
		popup.show()
		popup.raise_()

	def _on_pinned_destroyed(self, *_args) -> None:
		self._pinned = None


class _PinnedTooltip(QFrame):
	"""Popover anchored to the current panel; policy facts and official sources stay separate from contextual copy."""
	POPUP_MAX_WIDTH = 600
	POPUP_MIN_WIDTH = 220
	GAP_PX = 6
	RIGHT_MARGIN_PX = 16

	def __init__(self, text: str, risk: RiskInput, anchor: QWidget, *, ephemeral: bool = False) -> None:
		host: QWidget | None = anchor
		while host is not None and not isinstance(host, QScrollArea):
			host = host.parentWidget()
		if isinstance(host, QScrollArea):
			host = host.widget() or host
		else:
			host = anchor.window()
		super().__init__(host)
		self._host = host
		self._anchor = anchor
		self._ephemeral = ephemeral
		self.setObjectName("PinnedTooltip")
		self.setProperty("mode", "hover" if ephemeral else "pinned")
		self.setAttribute(Qt.WA_DeleteOnClose, True)
		if ephemeral:
			self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
		self.setAutoFillBackground(True)

		outer = QHBoxLayout(self)
		outer.setContentsMargins(14, 10, 14 if ephemeral else 8, 10)
		outer.setSpacing(8)
		copy = QWidget(self)
		copy_layout = QVBoxLayout(copy)
		copy_layout.setContentsMargins(0, 0, 0, 0)
		copy_layout.setSpacing(4)

		self._risk_title = QLabel(copy)
		self._risk_title.setObjectName("TooltipRisk")
		self._risk_title.setTextFormat(Qt.PlainText)
		self._risk_title.setWordWrap(True)
		copy_layout.addWidget(self._risk_title)
		self._risk_detail = QLabel(copy)
		self._risk_detail.setObjectName("TooltipRiskDetail")
		self._risk_detail.setTextFormat(Qt.PlainText)
		self._risk_detail.setWordWrap(True)
		copy_layout.addWidget(self._risk_detail)
		self._body = QLabel(copy)
		self._body.setObjectName("PinnedBody")
		self._body.setTextFormat(Qt.RichText)
		self._body.setWordWrap(True)
		self._body.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
		copy_layout.addWidget(self._body)
		self._sources = QLabel(copy)
		self._sources.setObjectName("TooltipRiskDetail")
		self._sources.setTextFormat(Qt.RichText)
		self._sources.setWordWrap(True)
		self._sources.setTextInteractionFlags(Qt.TextBrowserInteraction)
		self._sources.setOpenExternalLinks(True)
		copy_layout.addWidget(self._sources)
		outer.addWidget(copy, 1)

		if not ephemeral:
			close_button = QPushButton("×", self)
			close_button.setObjectName("PinnedClose")
			close_button.setFixedSize(20, 20)
			close_button.setFocusPolicy(Qt.StrongFocus)
			close_button.setAccessibleName("Close help")
			close_button.setCursor(Qt.PointingHandCursor)
			if HAS_ICONS:
				try:
					theme_name = getattr(anchor.window(), "_theme_name", "slate")
					palette = qtheme.PALETTES.get(theme_name, qtheme.PALETTES["slate"])
					close_button.setText("")
					close_button.setIcon(icon("mdi.close", color=palette["text_mute"], color_active=palette["text"]))
					close_button.setIconSize(QSize(12, 12))
				except Exception:
					close_button.setText("×")
			close_button.clicked.connect(self.close)
			outer.addWidget(close_button, 0, Qt.AlignTop)

		self.setContent(text, risk)
		anchor.installEventFilter(self)
		host.installEventFilter(self)

	def setContent(self, text: str, risk: RiskInput) -> None:  # noqa: N802
		'''Replace live content in place; legacy safety assurances cannot override audited facts.'''
		assessment = resolve_risk(risk)
		show_risks = bool(assessment.badge) and isinstance(self._anchor, HelpIcon)
		owner = self._anchor
		while owner is not None:
			if owner.objectName() == "FeatureCard" and hasattr(owner, "riskAssessment"):
				show_risks = show_risks and bool(owner.riskAssessment().badge)
				break
			owner = owner.parentWidget()
		heading, detail = (assessment.heading, assessment.detail) if show_risks else ("", "")
		bucket = assessment.bucket if show_risks else "neutral"
		self.setProperty("risk", bucket)
		self._risk_title.setProperty("risk", bucket)
		self._risk_detail.setProperty("risk", bucket)
		self._risk_title.setText(heading)
		self._risk_detail.setText(detail)
		self._risk_title.setVisible(bool(heading))
		self._risk_detail.setVisible(bool(detail))
		body = help_html(text, assessment)
		self._body.setText(body)
		self._sources.setText("Sources: " + assessment.sources_html if show_risks and assessment.sources_html else "")
		self._sources.setVisible(show_risks and bool(assessment.sources_html))
		self._set_copy_width(body, heading, detail)
		for widget in (self, self._risk_title, self._risk_detail):
			widget.style().unpolish(widget)
			widget.style().polish(widget)
		self.adjustSize()
		self._reposition()

	def _set_copy_width(self, body: str, title: str, detail: str) -> None:
		document = QTextDocument()
		document.setHtml(body)
		plain = "\n".join((title, detail, document.toPlainText()))
		metrics = QFontMetrics(self._body.font())
		unwrapped = max((metrics.horizontalAdvance(line) for line in plain.splitlines()), default=self.POPUP_MIN_WIDTH) + 30
		host_limit = max(self.POPUP_MIN_WIDTH, int(getattr(self._host, "width", lambda: 640)()) - 40)
		margins = self.layout().contentsMargins()
		decoration = 2 + margins.left() + margins.right() + (28 if self.layout().count() > 1 else 0)
		target = max(self.POPUP_MIN_WIDTH - decoration, min(unwrapped, self.POPUP_MAX_WIDTH - decoration, host_limit - decoration))
		self._body.parentWidget().setFixedWidth(target)

	def _reposition(self) -> None:
		if self._host is None or self._anchor is None:
			return
		anchor_top = self._anchor.mapTo(self._host, QPoint(0, 0))
		anchor_bottom = self._anchor.mapTo(self._host, QPoint(0, self._anchor.height()))
		horizontal = max(8, min(anchor_bottom.x(), self._host.width() - self.width() - self.RIGHT_MARGIN_PX))
		vertical = anchor_bottom.y() + self.GAP_PX
		if vertical + self.height() > self._host.height() - 6:
			vertical = max(8, anchor_top.y() - self.height() - self.GAP_PX)
		self.move(horizontal, vertical)

	def eventFilter(self, _watched, event) -> bool:  # noqa: N802
		if event.type() in (QEvent.Move, QEvent.Resize, QEvent.LayoutRequest, QEvent.ParentChange):
			self._reposition()
		return False


class _ThemedTipFilter(QObject):
	"""Delayed label help that can inherit a card's binding or retain explicit setting-specific metadata."""
	HOVER_DELAY_MS = 600

	def __init__(self, widget: QWidget, text: str, risk: RiskInput) -> None:
		super().__init__(widget)
		self._widget = widget
		self._text = text
		self._risk_source = risk
		self._inherited = False
		self._assessment = None
		self._risk = "medium"
		self._popup: _PinnedTooltip | None = None
		self._timer = QTimer(self)
		self._timer.setSingleShot(True)
		self._timer.setInterval(self.HOVER_DELAY_MS)
		self._timer.timeout.connect(self._spawn_popup)
		self.setRisk(risk)

	def setRisk(self, risk: RiskInput, *, inherited: bool = False) -> None:  # noqa: N802
		self._risk_source = risk
		self._inherited = inherited
		self.refreshRisk()
		subscribe_risk(self)

	def inheritRisk(self, risk: RiskInput) -> None:  # noqa: N802
		if self._inherited or not isinstance(self._risk_source, (RiskBinding, RiskAssessment)):
			self.setRisk(risk, inherited=True)

	def refreshRisk(self, assessment: RiskAssessment | None = None) -> RiskAssessment:  # noqa: N802
		assessment = assessment or resolve_risk(self._risk_source)
		if assessment != self._assessment:
			self._assessment = assessment
			self._risk = assessment.bucket
			self._widget.setAccessibleDescription(assessment.plain_text)
			if self._popup is not None:
				self._popup.setContent(self._text, assessment)
		return assessment

	def riskAssessment(self) -> RiskAssessment:  # noqa: N802
		return self.refreshRisk()

	def _spawn_popup(self) -> None:
		if self._popup is not None:
			return
		try:
			self._popup = _PinnedTooltip(self._text, self.refreshRisk(), anchor=self._widget, ephemeral=True)
			self._popup.destroyed.connect(self._on_destroyed)
			self._popup.show()
			self._popup.raise_()
		except Exception:
			self._popup = None

	def _on_destroyed(self, *_args) -> None:
		self._popup = None

	def eventFilter(self, _obj, event) -> bool:  # noqa: N802
		event_type = event.type()
		if event_type == QEvent.Enter:
			if self._popup is None and not self._timer.isActive():
				self._timer.start()
		elif event_type in (QEvent.Leave, QEvent.Hide, QEvent.ParentChange):
			self._timer.stop()
			if self._popup is not None:
				self._popup.close()
				self._popup = None
		return False


def inherit_card_risk(widget: QWidget, risk: RiskInput) -> None:
	'''Propagate a capability binding to contextual labels, retaining explicit per-setting assessments.'''
	for label in (widget, *widget.findChildren(QLabel)):
		tooltip_filter = getattr(label, "_themed_tip_filter", None)
		if isinstance(tooltip_filter, _ThemedTipFilter):
			tooltip_filter.inheritRisk(risk)


def install_themed_hover_tooltip(widget: QWidget, text: str, *, risk: RiskInput = "safe") -> None:
	'''Attach or update label help without stacking event filters or leaving stale popovers.'''
	if not text:
		return
	widget.setToolTip("")
	tooltip_filter = getattr(widget, "_themed_tip_filter", None)
	if isinstance(tooltip_filter, _ThemedTipFilter):
		tooltip_filter._text = text
		tooltip_filter._assessment = None
		tooltip_filter.setRisk(risk)
		return
	tooltip_filter = _ThemedTipFilter(widget, text, risk)
	widget.installEventFilter(tooltip_filter)
	widget._themed_tip_filter = tooltip_filter  # type: ignore[attr-defined]
