"""Theme-aware animated toggle switch used throughout the Qt menu."""

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QSizePolicy

from cs2esp.ui.qt.theme import PALETTES


_TRACK_SIZE = QSize(42, 22)
_MOTION_MS = 180


def _mix(first: QColor, second: QColor, progress: float) -> QColor:
    """Blend two colours without leaking theme values into the widget."""
    progress = max(0.0, min(1.0, progress))
    return QColor(
        int(first.red() + (second.red() - first.red()) * progress),
        int(first.green() + (second.green() - first.green()) * progress),
        int(first.blue() + (second.blue() - first.blue()) * progress),
        int(first.alpha() + (second.alpha() - first.alpha()) * progress),
    )


class ToggleSwitch(QAbstractButton):
    """Compact keyboard-accessible switch with restrained state motion."""

    def __init__(
        self,
        checked: bool = False,
        theme_name: str = "midnight",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ToggleSwitch")
        self.setCheckable(True)
        self.setChecked(bool(checked))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setMinimumSize(_TRACK_SIZE)

        self._position = 1.0 if checked else 0.0
        self._hover_progress = 0.0

        self._position_animation = QPropertyAnimation(self, b"position", self)
        self._position_animation.setDuration(_MOTION_MS)
        self._position_animation.setEasingCurve(QEasingCurve.OutCubic)

        self._hover_animation = QPropertyAnimation(self, b"hoverProgress", self)
        self._hover_animation.setDuration(_MOTION_MS)
        self._hover_animation.setEasingCurve(QEasingCurve.OutCubic)

        self.apply_theme(theme_name)
        self.toggled.connect(self._on_toggled)

    def _get_position(self) -> float:
        return self._position

    def _set_position(self, value: float) -> None:
        self._position = max(0.0, min(1.0, float(value)))
        self.update()

    position = Property(float, _get_position, _set_position)

    def _get_hover_progress(self) -> float:
        return self._hover_progress

    def _set_hover_progress(self, value: float) -> None:
        self._hover_progress = max(0.0, min(1.0, float(value)))
        self.update()

    hoverProgress = Property(  # noqa: N815
        float, _get_hover_progress, _set_hover_progress
    )

    def apply_theme(self, theme_name: str) -> None:
        """Reload semantic paint colours for a live theme change."""
        palette = PALETTES.get(theme_name, next(iter(PALETTES.values())))
        self.setProperty("themeName", theme_name)
        self._surface = QColor(palette["surface_1"])
        self._track_off = QColor(palette.get("surface_hi", palette["surface_2"]))
        self._track_on = QColor(palette["accent"])
        self._track_hover = QColor(palette.get("accent_hi", palette["accent"]))
        self._knob_off = QColor(palette.get("text_dim", palette["text"]))
        self._knob_on = QColor(palette["bg_app"])
        self._disabled = QColor(palette.get("text_mute", palette["text_dim"]))
        self._border = QColor(palette.get("divider", palette["border"]))
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return _TRACK_SIZE

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        if self.isEnabled():
            self._animate_hover(1.0)

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self._animate_hover(0.0)

    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self.update()

    def _animate_hover(self, target: float) -> None:
        self._hover_animation.stop()
        self._hover_animation.setStartValue(self._hover_progress)
        self._hover_animation.setEndValue(target)
        self._hover_animation.start()

    def _on_toggled(self, checked: bool) -> None:
        self._position_animation.stop()
        self._position_animation.setStartValue(self._position)
        self._position_animation.setEndValue(1.0 if checked else 0.0)
        self._position_animation.start()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        track_rect = QRectF(1.5, 1.5, self.width() - 3.0, self.height() - 3.0)
        radius = track_rect.height() / 2.0
        position = self._position

        track = _mix(self._track_off, self._track_on, position)
        hover_target = _mix(track, self._track_hover, 0.2)
        track = _mix(track, hover_target, self._hover_progress)
        knob = _mix(self._knob_off, self._knob_on, position)
        border = self._border

        if not self.isEnabled():
            track = _mix(self._surface, track, 0.46)
            knob = _mix(self._surface, self._disabled, 0.72)
            border = _mix(self._surface, border, 0.55)

        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(track_rect, radius, radius)

        painter.setPen(QPen(border, 1.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(track_rect, radius, radius)

        knob_padding = max(2.5, track_rect.height() * 0.13)
        knob_diameter = track_rect.height() - 2.0 * knob_padding
        knob_travel = track_rect.width() - knob_diameter - 2.0 * knob_padding
        knob_rect = QRectF(
            track_rect.left() + knob_padding + position * knob_travel,
            track_rect.top() + knob_padding,
            knob_diameter,
            knob_diameter,
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(knob)
        painter.drawEllipse(knob_rect)

        if self.hasFocus() and self.isEnabled():
            focus_color = QColor(self._track_on)
            focus_color.setAlphaF(0.78)
            focus_rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
            painter.setPen(QPen(focus_color, 1.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(
                focus_rect,
                focus_rect.height() / 2.0,
                focus_rect.height() / 2.0,
            )

    def hitButton(self, pos) -> bool:  # noqa: N802
        point = pos.toPoint() if hasattr(pos, "toPoint") else pos
        return self.rect().contains(point)
