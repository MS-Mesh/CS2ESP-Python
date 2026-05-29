"""Theme-aware hover and pinned help popovers."""

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, QTimer, Qt
from PySide6.QtGui import QFontMetrics, QTextDocument
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QToolTip,
    QVBoxLayout, QWidget,
)

from cs2esp.ui.qt import theme as qtheme
from cs2esp.ui.qt.icons import HAS_ICONS, icon


_RISK_COPY: dict[str, tuple[str, str]] = {
    "medium": (
        "Medium risk · FACEIT only",
        "Elevated detection profile on kernel-driver anti-cheat. "
        "VAC exposure remains low.",
    ),
    "high": (
        "High risk · FACEIT",
        "Known-flagged technique on kernel-driver anti-cheat. "
        "VAC exposure is medium.",
    ),
}

_RISK_BUCKETS = {
    "safe": "safe",
    "medium": "medium",
    "medium_faceit_only": "medium",
    "high": "high",
    "high_faceit": "high",
    "high_both": "high",
}


def _risk_bucket(level: str) -> str:
    return _RISK_BUCKETS.get(level, "safe")


class HelpIcon(QPushButton):
    """Small accessible button that opens contextual help."""

    def __init__(self, tooltip: str, risk: str = "safe", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("HelpIcon")
        self.setFlat(True)
        self.setCursor(Qt.WhatsThisCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("Show help")

        self._raw_text = tooltip or ""
        self._risk = _risk_bucket(risk)
        self._theme_name = "slate"
        self._pinned: _PinnedTooltip | None = None
        self._hover: _PinnedTooltip | None = None

        self.setToolTip("")
        self.setProperty("risk", self._risk)
        self.clicked.connect(self._toggle_pinned)
        self.apply_theme(self._theme_name)

    def apply_theme(self, theme_name: str) -> None:
        """Retint the vector glyph for the active theme and risk."""
        self._theme_name = theme_name or self._theme_name
        if not HAS_ICONS:
            self.setText("?")
            return
        palette = qtheme.PALETTES.get(
            self._theme_name, qtheme.PALETTES["slate"]
        )
        key = {
            "medium": "warn",
            "high": "danger",
        }.get(self._risk, "text_mute")
        try:
            self.setText("")
            self.setIcon(icon(
                "mdi.help",
                color=palette[key],
                color_active=palette["text"],
            ))
            self.setIconSize(QSize(12, 12))
        except Exception:  # pragma: no cover
            self.setText("?")

    def setRisk(self, level: str) -> None:  # noqa: N802
        """Update the semantic risk level used by QSS and popovers."""
        self._risk = _risk_bucket(level)
        self.setProperty("risk", self._risk)
        self.style().unpolish(self)
        self.style().polish(self)
        self.apply_theme(self._theme_name)
        for popup in (self._pinned, self._hover):
            if popup is not None:
                popup.setContent(self._raw_text, self._risk)

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        if self._pinned is not None:
            return
        self._close_hover()
        self._hover = _PinnedTooltip(
            self._raw_text, self._risk, anchor=self, ephemeral=True
        )
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
        popup = _PinnedTooltip(
            self._raw_text, self._risk, anchor=self, ephemeral=False
        )
        popup.destroyed.connect(self._on_pinned_destroyed)
        self._pinned = popup
        popup.show()
        popup.raise_()

    def _on_pinned_destroyed(self, *_args) -> None:
        self._pinned = None


class _PinnedTooltip(QFrame):
    """Popover anchored to a widget inside the current scroll panel."""

    POPUP_MAX_WIDTH = 600
    POPUP_MIN_WIDTH = 220
    GAP_PX = 6
    RIGHT_MARGIN_PX = 16

    def __init__(self, text: str, risk: str, anchor: QWidget,
                 *, ephemeral: bool = False) -> None:
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
        self.setObjectName("PinnedTooltip")
        self.setProperty("mode", "hover" if ephemeral else "pinned")
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        if ephemeral:
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAutoFillBackground(True)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 10, 8 if not ephemeral else 14, 10)
        outer.setSpacing(8)

        copy = QWidget(self)
        copy_layout = QVBoxLayout(copy)
        copy_layout.setContentsMargins(0, 0, 0, 0)
        copy_layout.setSpacing(4)

        self._risk_title = QLabel(copy)
        self._risk_title.setObjectName("TooltipRisk")
        copy_layout.addWidget(self._risk_title)

        self._risk_detail = QLabel(copy)
        self._risk_detail.setObjectName("TooltipRiskDetail")
        self._risk_detail.setWordWrap(True)
        copy_layout.addWidget(self._risk_detail)

        self._body = QLabel(copy)
        self._body.setObjectName("PinnedBody")
        self._body.setTextFormat(Qt.RichText)
        self._body.setWordWrap(True)
        self._body.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        copy_layout.addWidget(self._body)
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
                    palette = qtheme.PALETTES.get(
                        theme_name, qtheme.PALETTES["slate"]
                    )
                    close_button.setText("")
                    close_button.setIcon(icon(
                        "mdi.close",
                        color=palette["text_mute"],
                        color_active=palette["text"],
                    ))
                    close_button.setIconSize(QSize(12, 12))
                except Exception:  # pragma: no cover
                    close_button.setText("×")
            close_button.clicked.connect(self.close)
            outer.addWidget(close_button, 0, Qt.AlignTop)

        self.setContent(text, risk)
        self.style().unpolish(self)
        self.style().polish(self)
        self.adjustSize()
        self._reposition()

        anchor.installEventFilter(self)
        host.installEventFilter(self)

    def setContent(self, text: str, risk: str) -> None:  # noqa: N802
        """Update body and semantic risk copy, then recompute width."""
        risk = _risk_bucket(risk)
        title, detail = _RISK_COPY.get(risk, ("", ""))
        self.setProperty("risk", risk)
        self._risk_title.setProperty("risk", risk)
        self._risk_detail.setProperty("risk", risk)
        self._risk_title.setText(title)
        self._risk_detail.setText(detail)
        self._risk_title.setVisible(bool(title))
        self._risk_detail.setVisible(bool(detail))

        body = text.strip() if text else "No details are available yet."
        self._body.setText(body)
        self._set_copy_width(body, title, detail)
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
        unwrapped = max(
            (metrics.horizontalAdvance(line) for line in plain.splitlines()),
            default=self.POPUP_MIN_WIDTH,
        ) + 30
        host_limit = max(
            self.POPUP_MIN_WIDTH,
            int(getattr(self._host, "width", lambda: 640)()) - 40,
        )
        target = min(self.POPUP_MAX_WIDTH, host_limit)
        target = max(self.POPUP_MIN_WIDTH, min(unwrapped, target))
        self._body.parentWidget().setFixedWidth(target)

    def _reposition(self) -> None:
        if self._host is None or self._anchor is None:
            return
        anchor_top = self._anchor.mapTo(self._host, QPoint(0, 0))
        anchor_bottom = self._anchor.mapTo(
            self._host, QPoint(0, self._anchor.height())
        )
        host_width = self._host.width()
        host_height = self._host.height()
        x = min(
            anchor_bottom.x(),
            host_width - self.width() - self.RIGHT_MARGIN_PX,
        )
        x = max(8, x)
        y = anchor_bottom.y() + self.GAP_PX
        if y + self.height() > host_height - 6:
            y = max(8, anchor_top.y() - self.height() - self.GAP_PX)
        self.move(x, y)

    def eventFilter(self, _watched, event) -> bool:  # noqa: N802
        if event.type() in (
            QEvent.Move, QEvent.Resize, QEvent.LayoutRequest,
            QEvent.ParentChange,
        ):
            self._reposition()
        return False


class _ThemedTipFilter(QObject):
    """Delayed hover popover shared by labeled controls."""

    HOVER_DELAY_MS = 600

    def __init__(self, widget: QWidget, text: str, risk: str) -> None:
        super().__init__(widget)
        self._widget = widget
        self._text = text
        self._risk = _risk_bucket(risk)
        self._popup: _PinnedTooltip | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.HOVER_DELAY_MS)
        self._timer.timeout.connect(self._spawn_popup)

    def _spawn_popup(self) -> None:
        if self._popup is not None:
            return
        try:
            self._popup = _PinnedTooltip(
                self._text, self._risk,
                anchor=self._widget, ephemeral=True,
            )
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


def install_themed_hover_tooltip(
    widget: QWidget,
    text: str,
    *,
    risk: str = "safe",
) -> None:
    """Attach a delayed themed help popover to a form label."""
    if not text:
        return
    widget.setToolTip("")
    tooltip_filter = _ThemedTipFilter(widget, text, risk)
    widget.installEventFilter(tooltip_filter)
    widget._themed_tip_filter = tooltip_filter  # type: ignore[attr-defined]
