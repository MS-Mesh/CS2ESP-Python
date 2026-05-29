"""Responsive label/control and slider form rows."""

from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QSlider, QSpinBox, QStyle, QVBoxLayout, QWidget,
)

from cs2esp.ui.qt import theme as qtheme
from cs2esp.ui.qt.icons import HAS_ICONS, icon
from cs2esp.ui.qt.widgets.help import install_themed_hover_tooltip


class _WheelEater(QObject):
    """Swallow wheel changes while leaving drag and keyboard input intact."""

    def eventFilter(self, _obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Wheel:
            event.ignore()
            return True
        return False


class LabeledRow(QWidget):
    """Responsive setting label with a right-aligned control."""

    def __init__(self, label: str, control: QWidget,
                 *, help_text: str | None = None,
                 risk: str = "safe",
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("LabeledRow")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 4, 0, 4)
        self._layout.setHorizontalSpacing(16)
        self._layout.setVerticalSpacing(7)

        self._label = QLabel(label, self)
        self._label.setObjectName("SettingLabel")
        self._label.setWordWrap(True)
        self._label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        if help_text:
            install_themed_hover_tooltip(self._label, help_text, risk=risk)

        self._control = control
        self._control.setParent(self)
        self._label.setBuddy(control)
        self._compact: bool | None = None
        self._set_compact(False)

    def apply_theme(self, theme_name: str) -> None:
        """Forward theme changes to a custom child control when supported."""
        apply_theme = getattr(self._control, "apply_theme", None)
        if callable(apply_theme):
            apply_theme(theme_name)
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
        em = max(1, self.fontMetrics().horizontalAdvance("M"))
        control_width = max(44, self._control.minimumSizeHint().width())
        breakpoint = max(560, 46 * em + control_width)
        self._set_compact(width > 0 and width < breakpoint)

    def _set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._layout.removeWidget(self._label)
        self._layout.removeWidget(self._control)
        if compact:
            self._layout.addWidget(self._label, 0, 0)
            self._layout.addWidget(
                self._control, 1, 0, Qt.AlignLeft | Qt.AlignVCenter)
            self._layout.setColumnStretch(0, 1)
            self._layout.setColumnStretch(1, 0)
        else:
            self._layout.addWidget(self._label, 0, 0)
            self._layout.addWidget(
                self._control, 0, 1, Qt.AlignRight | Qt.AlignVCenter)
            self._layout.setColumnStretch(0, 1)
            self._layout.setColumnStretch(1, 0)
        self._compact = compact
        self.setProperty("layoutMode", "compact" if compact else "wide")
        self.updateGeometry()


class LabeledSlider(QWidget):
    """Responsive slider with a linked numeric editor and step buttons."""

    valueChanged = Signal(object)

    def __init__(self, label: str, lo: float, hi: float, value: float,
                 *, decimals: int = 0, step: float | None = None,
                 help_text: str | None = None, risk: str = "safe",
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("LabeledSlider")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._decimals = max(0, int(decimals))
        self._scale = 10 ** self._decimals
        self._lo_f = float(lo)
        self._hi_f = float(hi)

        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 4, 0, 4)
        self._layout.setHorizontalSpacing(14)
        self._layout.setVerticalSpacing(7)

        self._label = QLabel(label, self)
        self._label.setObjectName("SettingLabel")
        self._label.setWordWrap(True)
        self._label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        if help_text:
            install_themed_hover_tooltip(self._label, help_text, risk=risk)

        self._slider = QSlider(Qt.Horizontal, self)
        self._slider.setObjectName("SettingSlider")
        self._slider.setRange(int(round(lo * self._scale)),
                              int(round(hi * self._scale)))
        self._slider.setSingleStep(1)
        self._slider.setPageStep(max(
            1, (self._slider.maximum() - self._slider.minimum()) // 20))
        self._slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._slider.setAccessibleName(label)

        spin: QSpinBox | QDoubleSpinBox
        self._step_f = float(step) if step is not None else 1.0 / self._scale
        if self._decimals == 0:
            spin = QSpinBox(self)
            spin.setRange(int(round(lo)), int(round(hi)))
            spin.setSingleStep(max(1, int(round(self._step_f))))
        else:
            spin = QDoubleSpinBox(self)
            spin.setRange(self._lo_f, self._hi_f)
            spin.setDecimals(self._decimals)
            spin.setSingleStep(self._step_f)
        spin.setObjectName("SliderSpinBox")
        spin.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        spin.setAccessibleName(f"{label} value")
        self._spin = spin

        self._arrow_column = QWidget(self)
        self._arrow_column.setObjectName("SliderStepButtons")
        arrow_layout = QVBoxLayout(self._arrow_column)
        arrow_layout.setContentsMargins(0, 0, 0, 0)
        arrow_layout.setSpacing(2)
        self._up_btn = QPushButton("", self._arrow_column)
        self._up_btn.setObjectName("SpinArrow")
        self._up_btn.setAccessibleName(f"Increase {label}")
        self._up_btn.setFocusPolicy(Qt.NoFocus)
        self._up_btn.setCursor(Qt.PointingHandCursor)
        self._up_btn.clicked.connect(self._spin.stepUp)
        arrow_layout.addWidget(self._up_btn)
        self._down_btn = QPushButton("", self._arrow_column)
        self._down_btn.setObjectName("SpinArrow")
        self._down_btn.setAccessibleName(f"Decrease {label}")
        self._down_btn.setFocusPolicy(Qt.NoFocus)
        self._down_btn.setCursor(Qt.PointingHandCursor)
        self._down_btn.clicked.connect(self._spin.stepDown)
        arrow_layout.addWidget(self._down_btn)

        self._value_control = QWidget(self)
        self._value_control.setObjectName("SliderValueControl")
        value_layout = QHBoxLayout(self._value_control)
        value_layout.setContentsMargins(0, 0, 0, 0)
        value_layout.setSpacing(5)
        value_layout.addWidget(self._spin)
        value_layout.addWidget(self._arrow_column)

        self._compact: bool | None = None
        self._set_compact(False)
        self.apply_theme(None)
        self._set_silent(value)

        self._wheel_eater: _WheelEater | None = None
        self.setWheelEnabled(False)

        self._syncing = False
        self._slider.valueChanged.connect(self._on_slider)
        if isinstance(self._spin, QDoubleSpinBox):
            self._spin.valueChanged.connect(self._on_spin_float)
        else:
            self._spin.valueChanged.connect(self._on_spin_int)

    def value(self) -> int | float:
        if self._decimals == 0:
            return int(self._spin.value())
        return float(self._spin.value())

    def setValue(self, value: int | float) -> None:  # noqa: N802
        self._set_silent(value)

    def apply_theme(self, theme_name: str | None) -> None:
        """Retint vector arrows and refresh font-dependent control metrics."""
        theme_name = theme_name or "slate"
        if HAS_ICONS:
            palette = qtheme.PALETTES.get(
                theme_name, qtheme.PALETTES["slate"])
            try:
                self._up_btn.setIcon(icon(
                    "mdi.chevron-up", color=palette["text_dim"],
                    color_active=palette["accent"]))
                self._down_btn.setIcon(icon(
                    "mdi.chevron-down", color=palette["text_dim"],
                    color_active=palette["accent"]))
            except Exception:
                self._set_native_arrow_icons()
        else:  # pragma: no cover
            self._set_native_arrow_icons()
        self._update_metrics()
        self._update_layout_mode()

    def setWheelEnabled(self, enabled: bool) -> None:  # noqa: N802
        """Allow or block mouse-wheel changes on the slider and editor."""
        if not enabled:
            if self._wheel_eater is None:
                self._wheel_eater = _WheelEater(self)
                self._slider.installEventFilter(self._wheel_eater)
                self._spin.installEventFilter(self._wheel_eater)
        elif self._wheel_eater is not None:
            self._slider.removeEventFilter(self._wheel_eater)
            self._spin.removeEventFilter(self._wheel_eater)
            self._wheel_eater.deleteLater()
            self._wheel_eater = None

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_layout_mode(event.size().width())

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._update_metrics()
            self._update_layout_mode()

    def _set_native_arrow_icons(self) -> None:
        self._up_btn.setIcon(self.style().standardIcon(QStyle.SP_ArrowUp))
        self._down_btn.setIcon(self.style().standardIcon(QStyle.SP_ArrowDown))

    def _update_metrics(self) -> None:
        font_height = max(14, self.fontMetrics().height())
        scale = max(1.0, font_height / 19.0)
        arrow_width = max(20, int(round(20 * scale)))
        arrow_height = max(13, int(round(13 * scale)))
        icon_size = max(12, int(round(13 * scale)))
        self._arrow_column.setFixedWidth(arrow_width)
        self._up_btn.setFixedSize(arrow_width, arrow_height)
        self._down_btn.setFixedSize(arrow_width, arrow_height)
        self._up_btn.setIconSize(QSize(icon_size, icon_size))
        self._down_btn.setIconSize(QSize(icon_size, icon_size))

        widest = max(
            self._spin.fontMetrics().horizontalAdvance(
                self._spin.textFromValue(self._spin.minimum())),
            self._spin.fontMetrics().horizontalAdvance(
                self._spin.textFromValue(self._spin.maximum())),
        )
        self._spin.setMinimumWidth(max(76, widest + int(round(30 * scale))))

    def _update_layout_mode(self, width: int | None = None) -> None:
        width = self.width() if width is None else width
        em = max(1, self.fontMetrics().horizontalAdvance("M"))
        value_width = self._value_control.minimumSizeHint().width()
        breakpoint = max(680, 42 * em + value_width + 140)
        self._set_compact(width > 0 and width < breakpoint)

    def _set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        for widget in (self._label, self._slider, self._value_control):
            self._layout.removeWidget(widget)
        if compact:
            self._layout.addWidget(self._label, 0, 0, 1, 2)
            self._layout.addWidget(self._slider, 1, 0, Qt.AlignVCenter)
            self._layout.addWidget(
                self._value_control, 1, 1, Qt.AlignRight | Qt.AlignVCenter)
            self._layout.setColumnStretch(0, 1)
            self._layout.setColumnStretch(1, 0)
            self._layout.setColumnStretch(2, 0)
        else:
            self._layout.addWidget(self._label, 0, 0)
            self._layout.addWidget(self._slider, 0, 1, Qt.AlignVCenter)
            self._layout.addWidget(
                self._value_control, 0, 2, Qt.AlignRight | Qt.AlignVCenter)
            self._layout.setColumnStretch(0, 2)
            self._layout.setColumnStretch(1, 3)
            self._layout.setColumnStretch(2, 0)
        self._compact = compact
        self.setProperty("layoutMode", "compact" if compact else "wide")
        self.updateGeometry()

    def _set_silent(self, value: int | float) -> None:
        self._syncing = True
        try:
            if self._decimals == 0:
                int_value = int(round(float(value)))
                self._spin.setValue(int_value)
                self._slider.setValue(int_value * self._scale)
            else:
                float_value = float(value)
                self._spin.setValue(float_value)
                self._slider.setValue(int(round(float_value * self._scale)))
        finally:
            self._syncing = False

    def _on_slider(self, raw: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            if self._decimals == 0:
                self._spin.setValue(raw)
                self.valueChanged.emit(int(raw))
            else:
                float_value = raw / self._scale
                self._spin.setValue(float_value)
                self.valueChanged.emit(float(float_value))
        finally:
            self._syncing = False

    def _on_spin_int(self, value: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            self._slider.setValue(value * self._scale)
            self.valueChanged.emit(int(value))
        finally:
            self._syncing = False

    def _on_spin_float(self, value: float) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            self._slider.setValue(int(round(value * self._scale)))
            self.valueChanged.emit(float(value))
        finally:
            self._syncing = False
