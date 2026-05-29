"""Combat settings for aim, firing, and movement automation."""

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QSlider, QVBoxLayout, QWidget,
)

from cs2esp.ui.qt.panels.base import PanelBase
from cs2esp.ui.qt.widgets import (
    ComboRow, FeatureCard, KeybindRow, LabeledRow, LabeledSlider, ToggleSwitch,
)
from cs2esp.ui.qt.widgets.segmented import SegmentedSelector


_DEFAULT_BANDS: list[dict] = [
    {"min_m": 4.0, "max_m": 9999.0, "fov_deg": 0.5,
     "skeleton_fov_deg": 0.5, "label": "long"},
    {"min_m": 2.0, "max_m": 4.0, "fov_deg": 2.0,
     "skeleton_fov_deg": 1.0, "label": "mid"},
    {"min_m": 0.5, "max_m": 2.0, "fov_deg": 4.0,
     "skeleton_fov_deg": 2.0, "label": "close"},
    {"min_m": 0.0, "max_m": 0.5, "fov_deg": 8.0,
     "skeleton_fov_deg": 4.0, "label": "point_blank"},
]

_INFINITY_M = 9999.0
_BAND_SLIDER_CAP_M = 50.0


def _format_distance(value: float) -> str:
    """Format the infinity sentinel and ordinary metre values."""
    if float(value) >= _INFINITY_M - 0.5:
        return "∞"
    return f"{float(value):.1f} m"


def _format_band_range(band: dict) -> str:
    """Format a band's visible range without changing its stored values."""
    minimum = _format_distance(float(band.get("min_m", 0.0)))
    maximum = _format_distance(float(band.get("max_m", 0.0)))
    return f"{minimum} — {maximum}"


class _MaxDistanceControl(QWidget):
    """Bounded distance editor with an infinity-sentinel shortcut."""

    valueChanged = Signal(float)

    def __init__(self, value: float, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("BandMaxDistanceControl")
        self.setProperty("infinite", False)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._slider = QSlider(Qt.Horizontal, self)
        self._slider.setObjectName("BandMaxDistanceSlider")
        self._slider.setAccessibleName("Maximum range")
        self._slider.setRange(0, int(_BAND_SLIDER_CAP_M * 10))
        self._slider.setSingleStep(1)
        self._slider.setPageStep(int(_BAND_SLIDER_CAP_M * 10) // 20 or 1)
        self._slider.installEventFilter(_BAND_WHEEL_EATER)
        layout.addWidget(self._slider, 1)

        self._spin = QDoubleSpinBox(self)
        self._spin.setObjectName("BandMaxDistanceSpin")
        self._spin.setAccessibleName("Maximum range value")
        self._spin.setRange(0.0, _BAND_SLIDER_CAP_M)
        self._spin.setDecimals(1)
        self._spin.setSingleStep(0.1)
        self._spin.setMinimumWidth(80)
        self._spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self._spin.installEventFilter(_BAND_WHEEL_EATER)
        layout.addWidget(self._spin, 0)

        self._inf_btn = QPushButton("∞", self)
        self._inf_btn.setObjectName("BandInfinityBtn")
        self._inf_btn.setCheckable(True)
        self._inf_btn.setMinimumSize(28, 22)
        self._inf_btn.setAccessibleName("Use infinite maximum range")
        self._inf_btn.setFocusPolicy(Qt.NoFocus)
        self._inf_btn.setCursor(Qt.PointingHandCursor)
        self._inf_btn.setToolTip(
            "Treat this band's upper bound as infinity (no cap)."
        )
        self._inf_btn.toggled.connect(self._on_infinity_toggled)
        layout.addWidget(self._inf_btn, 0)

        self._suppress = False
        self._slider.valueChanged.connect(self._on_slider_changed)
        self._spin.valueChanged.connect(self._on_spin_changed)

        self.setValue(value)

    def setValue(self, value: float) -> None:  # noqa: N802
        infinite = float(value) >= _INFINITY_M - 0.5
        self._suppress = True
        try:
            self.setProperty("infinite", infinite)
            if infinite:
                self._inf_btn.setChecked(True)
                self._slider.setEnabled(False)
                self._spin.setEnabled(False)
                self._spin.setValue(_BAND_SLIDER_CAP_M)
                self._slider.setValue(int(_BAND_SLIDER_CAP_M * 10))
            else:
                self._inf_btn.setChecked(False)
                self._slider.setEnabled(True)
                self._spin.setEnabled(True)
                clamped = max(0.0, min(_BAND_SLIDER_CAP_M, float(value)))
                self._spin.setValue(clamped)
                self._slider.setValue(int(round(clamped * 10)))
        finally:
            self._suppress = False

    def _on_slider_changed(self, raw: int) -> None:
        if self._suppress or self._inf_btn.isChecked():
            return
        self._suppress = True
        try:
            v = raw / 10.0
            self._spin.setValue(v)
            self.valueChanged.emit(v)
        finally:
            self._suppress = False

    def _on_spin_changed(self, v: float) -> None:
        if self._suppress or self._inf_btn.isChecked():
            return
        self._suppress = True
        try:
            self._slider.setValue(int(round(v * 10)))
            self.valueChanged.emit(float(v))
        finally:
            self._suppress = False

    def _on_infinity_toggled(self, on: bool) -> None:
        if self._suppress:
            return
        self.setProperty("infinite", bool(on))
        self._slider.setEnabled(not on)
        self._spin.setEnabled(not on)
        self.valueChanged.emit(_INFINITY_M if on else float(self._spin.value()))


class _BandWheelEater(QObject):
    def eventFilter(self, _obj, event):  # noqa: N802
        if event.type() == QEvent.Wheel:
            event.ignore()
            return True
        return False


_BAND_WHEEL_EATER = _BandWheelEater()


class _DistanceBandsEditor(QFrame):
    """Collapsed editor for ``config.triggerbot.distance_bands``."""

    def __init__(self, config: object, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.setObjectName("DistanceBandsEditor")
        self.setProperty("expanded", False)
        self.setFrameShape(QFrame.NoFrame)
        self._config = config
        self._tb = config.triggerbot
        self._expanded = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 6, 0, 0)
        outer.setSpacing(8)

        header = QWidget(self)
        header.setObjectName("BandsHeader")
        header.setProperty("sectionRole", "advanced")
        hdr = QHBoxLayout(header)
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(8)

        self._chevron = QPushButton("▸ Distance-band targeting", header)
        self._chevron.setObjectName("BandsChevron")
        self._chevron.setProperty("expanded", False)
        self._chevron.setFocusPolicy(Qt.NoFocus)
        self._chevron.setCursor(Qt.PointingHandCursor)
        self._chevron.setCheckable(True)
        self._chevron.toggled.connect(self._on_expand_toggled)
        hdr.addWidget(self._chevron, 1, Qt.AlignVCenter)

        reset_btn = QPushButton("Reset", header)
        reset_btn.setObjectName("BandsReset")
        reset_btn.setFocusPolicy(Qt.NoFocus)
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.setToolTip(
            "Restore the 4 default engagement bands "
            "(long / mid / close / point-blank)."
        )
        reset_btn.clicked.connect(self._reset_to_defaults)
        hdr.addWidget(reset_btn, 0, Qt.AlignVCenter)

        outer.addWidget(header)

        self._body = QWidget(self)
        self._body.setObjectName("BandsBody")
        self._body.setProperty("sectionRole", "advanced")
        body_lay = QVBoxLayout(self._body)
        body_lay.setContentsMargins(0, 4, 0, 0)
        body_lay.setSpacing(10)

        bands_description = QLabel(
            "Tune the point and skeleton target cones for each range.",
            self._body,
        )
        bands_description.setObjectName("BandsDescription")
        bands_description.setWordWrap(True)
        body_lay.addWidget(bands_description)

        self._bands_layout = QVBoxLayout()
        self._bands_layout.setContentsMargins(0, 0, 0, 0)
        self._bands_layout.setSpacing(12)
        body_lay.addLayout(self._bands_layout)

        add_row = QHBoxLayout()
        add_row.setContentsMargins(0, 0, 0, 0)
        add_btn = QPushButton("+ Add band", self._body)
        add_btn.setObjectName("BandsAdd")
        add_btn.setFocusPolicy(Qt.NoFocus)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setToolTip(
            "Append a new band to the list with placeholder values; "
            "you can rename and re-range it after creation."
        )
        add_btn.clicked.connect(self._add_band)
        add_row.addWidget(add_btn, 0, Qt.AlignLeft)
        add_row.addStretch(1)
        body_lay.addLayout(add_row)

        self._body.setVisible(False)
        outer.addWidget(self._body)

        bands = getattr(self._tb, "distance_bands", None)
        if not isinstance(bands, list) or not bands:
            self._tb.distance_bands = [dict(b) for b in _DEFAULT_BANDS]

        self._rebuild_rows()

        self._visibility_timer = QTimer(self)
        self._visibility_timer.setInterval(250)
        self._visibility_timer.timeout.connect(self._refresh_visibility)
        self._visibility_timer.start()
        self._refresh_visibility()

    def _refresh_visibility(self) -> None:
        """Hide the bone-angle bands when Glow or Chams supplies the hit-test."""
        try:
            tb = self._config.triggerbot
            wh = self._config.wallhack
            wh_comp = bool(getattr(tb, "wh_compliant", True))
            wh_on = bool(getattr(wh, "enabled", True))
            glow_on = bool(getattr(getattr(self._config, "glow", None),
                                   "enabled", False))
            chams_on = bool(getattr(getattr(self._config.visuals,
                                            "chams", None),
                                    "enabled", False))
        except AttributeError:
            self.setVisible(True)
            return
        hide = wh_comp and wh_on and (glow_on or chams_on)
        self.setVisible(not hide)

    def _on_expand_toggled(self, on: bool) -> None:
        self._expanded = bool(on)
        self._body.setVisible(self._expanded)
        self.setProperty("expanded", self._expanded)
        self._chevron.setProperty("expanded", self._expanded)
        glyph = "▾" if self._expanded else "▸"
        self._chevron.setText(f"{glyph} Distance-band targeting")
        self.style().unpolish(self)
        self.style().polish(self)

    def _rebuild_rows(self) -> None:
        while self._bands_layout.count():
            item = self._bands_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        bands = list(getattr(self._tb, "distance_bands", []))
        for index, band in enumerate(bands):
            self._bands_layout.addWidget(
                self._build_band_row(index, band, len(bands)))

    def _build_band_row(self, idx: int, band: dict,
                        band_count: int) -> QWidget:
        row = QFrame(self)
        row.setObjectName("BandRow")
        row.setProperty("bandIndex", idx)
        row.setFrameShape(QFrame.NoFrame)
        layout = QVBoxLayout(row)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        header = QWidget(row)
        header.setObjectName("BandRowHeader")
        title_row = QHBoxLayout(header)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)

        title = QLabel(f"Band {idx + 1}", header)
        title.setObjectName("BandRowTitle")
        title_row.addWidget(title, 0, Qt.AlignVCenter)

        band_name = str(band.get("label", "custom")).replace("_", " ")
        name = QLabel(band_name, header)
        name.setObjectName("BandRowName")
        name.setProperty("textRole", "secondary")
        title_row.addWidget(name, 0, Qt.AlignVCenter)
        title_row.addStretch(1)

        range_label = QLabel(_format_band_range(band), header)
        range_label.setObjectName("BandRowRange")
        range_label.setProperty("textRole", "metadata")
        title_row.addWidget(range_label, 0, Qt.AlignVCenter)

        remove_button = QPushButton("Remove", header)
        remove_button.setObjectName("BandsRemove")
        remove_button.setFocusPolicy(Qt.NoFocus)
        remove_button.setCursor(Qt.PointingHandCursor)
        remove_button.setEnabled(band_count > 1)
        remove_button.setToolTip(
            "Delete this band from the list."
            if band_count > 1 else "At least one distance band is required."
        )
        remove_button.clicked.connect(lambda _checked=False, i=idx:
                                      self._remove_band(i))
        title_row.addWidget(remove_button, 0, Qt.AlignVCenter)
        layout.addWidget(header)

        min_slider = LabeledSlider(
            "Min distance (m)", lo=0.0, hi=_BAND_SLIDER_CAP_M,
            value=float(band.get("min_m", 0.0)), decimals=1, step=0.1,
        )
        min_slider.valueChanged.connect(
            lambda v, i=idx, label=range_label: self._write_band(
                i, "min_m", float(v), label))
        layout.addWidget(min_slider)

        max_row = QWidget(row)
        max_row.setObjectName("BandMaxDistanceRow")
        max_lbl_row = QHBoxLayout(max_row)
        max_lbl_row.setContentsMargins(0, 0, 0, 0)
        max_lbl_row.setSpacing(16)
        max_lbl = QLabel("Max distance (m)", max_row)
        max_lbl.setObjectName("SettingLabel")
        max_lbl.setWordWrap(True)
        max_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        max_lbl_row.addWidget(max_lbl, 2, Qt.AlignVCenter)
        max_ctrl = _MaxDistanceControl(
            float(band.get("max_m", 0.0)), max_row)
        max_ctrl.valueChanged.connect(
            lambda v, i=idx, label=range_label: self._write_band(
                i, "max_m", float(v), label))
        max_lbl_row.addWidget(max_ctrl, 3)
        layout.addWidget(max_row)

        for label, key in [
            ("FOV (point)", "fov_deg"),
            ("FOV (skeleton)", "skeleton_fov_deg"),
        ]:
            slider = LabeledSlider(label, lo=0.1, hi=12.0,
                                   value=float(band.get(key, 0.0)),
                                   decimals=1, step=0.1)
            slider.valueChanged.connect(
                lambda v, i=idx, k=key: self._write_band(
                    i, k, float(v)))
            layout.addWidget(slider)
        return row

    def _write_band(self, idx: int, key: str, value: float,
                    range_label: QLabel | None = None) -> None:
        bands = getattr(self._tb, "distance_bands", None)
        if not isinstance(bands, list) or idx >= len(bands):
            return
        bands = [dict(band) for band in bands]
        bands[idx][key] = value
        self._tb.distance_bands = bands
        if key in ("min_m", "max_m") and range_label is not None:
            range_label.setText(_format_band_range(bands[idx]))

    def _reset_to_defaults(self) -> None:
        self._tb.distance_bands = [dict(b) for b in _DEFAULT_BANDS]
        self._rebuild_rows()

    def _add_band(self) -> None:
        """Append a new band with placeholder values."""
        bands = list(getattr(self._tb, "distance_bands", []) or [])
        biggest = 0.0
        for b in bands:
            try:
                cur = float(b.get("max_m", 0.0))
                if cur >= _INFINITY_M - 0.5:
                    biggest = _BAND_SLIDER_CAP_M
                    break
                if cur > biggest:
                    biggest = cur
            except Exception:
                continue
        start = min(_BAND_SLIDER_CAP_M, biggest + 1.0)
        bands.append({
            "min_m": float(start),
            "max_m": _INFINITY_M,
            "fov_deg": 1.0,
            "skeleton_fov_deg": 1.0,
            "label": f"custom-{len(bands) + 1}",
        })
        self._tb.distance_bands = bands
        self._rebuild_rows()

    def _remove_band(self, idx: int) -> None:
        """Delete band ``idx``; refuses to delete the final band so
        the runtime always has at least one band to fall back on."""
        bands = list(getattr(self._tb, "distance_bands", []) or [])
        if idx < 0 or idx >= len(bands):
            return
        if len(bands) <= 1:
            return
        bands.pop(idx)
        self._tb.distance_bands = bands
        self._rebuild_rows()


class CombatPanel(PanelBase):
    """Configure aim, firing, and movement automation."""

    def __init__(self, config, parent: QWidget = None) -> None:
        super().__init__(config, parent)

        self.h1("Combat")
        self.intro(
            "Tune target acquisition, shot timing, and movement assists. "
            "Most tools emit mouse or keyboard input; Aimbot writes view "
            "angles and follows the global write guard."
        )

        self.h2("Aim, fire & movement")
        tb = self.config.triggerbot
        tb_card = self.add_widget(FeatureCard(
            "Triggerbot",
            risk="medium",
            help_text=(
                "<b>Feedback mode (default):</b> auto-fires, then watches the "
                "target's health. If it doesn't drop within the confirm window "
                "the shot counts as a miss and firing pauses before trying "
                "again. Pure user-mode clicks — no memory writes, but kernel "
                "anti-cheats can still correlate click timing.<br><br>"
                "<b>Vanilla mode:</b> fires the instant your crosshair sits on "
                "an enemy, tuned by the per-range distance bands. Risk depends "
                "on which visual it rides when <i>Wallhack-compliant</i> is on: "
                "if <b>Glow or Chams</b> is enabled it inherits their memory "
                "writes (<b>high</b>); with only <b>bones/box/head-ring</b> — or "
                "Wallhack-compliant <b>off</b> — it's a read-only bone-angle "
                "test (<b>safe</b>).<br><br>"
                "<i>The badge above reflects the mode actually in use; it "
                "updates when you reopen this tab after changing visuals.</i>"
            ),
            toggle_target=(tb, "enabled"),
        ))

        def _refresh_tb_risk() -> None:
            # The badge follows the active hit-test path; Glow and Chams inherit write risk.
            if str(getattr(tb, "version", "feedback")) == "feedback":
                tb_card.setRisk("medium")
                return
            if not bool(getattr(tb, "wh_compliant", True)):
                tb_card.setRisk("safe")
                return
            try:
                from cs2esp.features.triggerbot_vanilla import _select_wh_mode
                mode = _select_wh_mode(self.config)
            except Exception:
                mode = "default"
            tb_card.setRisk("high" if mode in ("glow", "chams") else "safe")

        # Recompute on show because Glow, Chams, and Skeleton live in ESP.
        self._refresh_tb_risk = _refresh_tb_risk

        tb_selector = SegmentedSelector(
            tb, "version",
            [("feedback", "Feedback"), ("vanilla", "Vanilla")],
            default="feedback", equal_width=True,
        )
        tb_card.addTitleWidget(tb_selector)

        def _tb_toggle_row(attr: str, label: str, tip: str,
                           on_change=None) -> LabeledRow:
            sw = ToggleSwitch()
            sw.setChecked(bool(getattr(tb, attr, True)))
            sw._live_section = tb # type: ignore[attr-defined]
            sw._live_attr = attr # type: ignore[attr-defined]
            sw.toggled.connect(lambda c, a=attr: setattr(tb, a, bool(c)))
            if on_change is not None:
                sw.toggled.connect(lambda _c: on_change())
            return LabeledRow(label, sw, help_text=tip)

        tb_card.addBodyWidget(_tb_toggle_row(
            "head_only", "Shoot only on head",
            "Fire only when the crosshair is on the target's head. Feedback "
            "mode ray-tests the head hitbox; Vanilla mode requires the "
            "crosshair inside the green head ring. Applies to both modes and "
            "needs skeleton bone data for the target."))

        for label, attr, lo, hi, tip in [
            ("Refire cooldown (ms)", "refire_cooldown_ms", 0, 2000,
             "Minimum milliseconds between two auto-fires — the floor "
             "between bursts, so the trigger can't re-fire faster than "
             "this even while the crosshair stays on the target."),
            ("Reaction delay (ms)", "delay_ms", 0, 500,
             "Base human-reaction delay applied after the crosshair "
             "lands on an enemy hitbox before the shot is sent."),
            ("Delay jitter (±ms)", "delay_jitter_ms", 0, 200,
             "Random ± milliseconds added to the reaction delay on "
             "every shot, so the gap varies instead of being constant "
             "(e.g. 100 ms ±20 → each shot waits 80–120 ms). Breaks "
             "fixed-interval click-timing analysis."),
            ("Min reaction (ms)", "min_delay_ms", 0, 500,
             "Hard floor the jittered reaction delay can never drop "
             "below, so jitter can't produce an inhumanly fast shot."),
        ]:
            slider = LabeledSlider(label, lo=lo, hi=hi,
                                   value=int(getattr(tb, attr)), decimals=0,
                                   help_text=tip)
            slider.valueChanged.connect(
                lambda v, a=attr: setattr(tb, a, int(v)))
            tb_card.addBodyWidget(slider)

        tb_card.addBodyWidget(KeybindRow("Triggerbot · hold",
                                         tb, "hotkey"))
        tb_card.addBodyWidget(KeybindRow("Triggerbot · toggle",
                                         tb, "hotkey_toggle"))

        feedback_group = QWidget()
        feedback_group.setObjectName("TriggerModePanel")
        feedback_group.setProperty("mode", "feedback")
        fb_lay = QVBoxLayout(feedback_group)
        fb_lay.setContentsMargins(0, 0, 0, 0)
        fb_lay.setSpacing(10)
        fb_confirm = LabeledSlider(
            "Confirm window (ms)", lo=0, hi=1000,
            value=int(getattr(tb, "feedback_confirm_ms", 100)),
            decimals=0, risk="medium",
            help_text=(
                "Per-shot window to confirm a hit. If the target's "
                "health doesn't drop within this many ms of a click, "
                "that shot counts as a confirmed miss."
            ),
        )
        fb_confirm.valueChanged.connect(
            lambda v: setattr(tb, "feedback_confirm_ms", int(v)))
        fb_lay.addWidget(fb_confirm)
        fb_resume = LabeledSlider(
            "Resume pause (ms)", lo=0, hi=2000,
            value=int(getattr(tb, "feedback_resume_ms", 100)),
            decimals=0, risk="medium",
            help_text=(
                "After a confirmed miss, pause firing this many ms "
                "before re-baselining the target and resuming."
            ),
        )
        fb_resume.valueChanged.connect(
            lambda v: setattr(tb, "feedback_resume_ms", int(v)))
        fb_lay.addWidget(fb_resume)

        vanilla_group = QWidget()
        vanilla_group.setObjectName("TriggerModePanel")
        vanilla_group.setProperty("mode", "vanilla")
        van_lay = QVBoxLayout(vanilla_group)
        van_lay.setContentsMargins(0, 0, 0, 0)
        van_lay.setSpacing(10)

        van_lay.addWidget(_tb_toggle_row(
            "wh_compliant", "Wallhack-compliant",
            "the trigger's hit-test follows whichever "
            "wallhack primitive is currently visible, so the bot "
            "only fires when your crosshair is over what you "
            "<i>see</i> on screen.<br><br>"
            "Priority on tie: <b>glow</b> &gt; <b>chams</b> &gt; "
            "<b>skeleton head circle</b> &gt; default bone "
            "gates.<br><br>"
            "When <b>glow</b> or <b>chams</b> is on the silhouette "
            "test replaces the distance-banded bone-angle gates "
            "(those are hidden below to keep the panel clean). "
            "Skeleton + head adds the head-circle ring as an "
            "extra fire condition on top of the legacy gates. "
            "Off = always use the legacy bone-angle path "
            "regardless of which visuals are on.",
            on_change=_refresh_tb_risk))
        van_lay.addWidget(_tb_toggle_row(
            "allow_scoped", "Allow scoped weapons",
            "Permit auto-fire while a sniper rifle is scoped in. "
            "Off = scoped weapons fire only on your real click."))

        _scoped_mul_sl = LabeledSlider(
            "Scoped FOV multiplier",
            lo=0.0, hi=1.0,
            value=float(getattr(tb, "scoped_band_multiplier", 0.30)),
            decimals=2, step=0.05,
            help_text=(
                "Scales the matched FOV band's fire cones while "
                "scoped. <b>0.00 = no fire while scoped at all</b>. "
                "0.30 (default) is tight enough to wait for the "
                "head before firing; 1.00 disables damping. "
                "Ignored by the glow / chams hit-tests (those use "
                "engine truth instead of band FOV) - the slider "
                "is greyed out / hidden when either is active."
            ),
        )
        _scoped_mul_sl.setProperty("settingRole", "scopedMultiplier")
        _scoped_mul_sl.valueChanged.connect(
            lambda v: setattr(tb, "scoped_band_multiplier", float(v)))
        van_lay.addWidget(_scoped_mul_sl)

        def _refresh_scoped_visibility() -> None:
            try:
                wh = self.config.wallhack
                wh_comp = bool(getattr(tb, "wh_compliant", True))
                wh_on = bool(getattr(wh, "enabled", True))
                glow_on = bool(getattr(
                    getattr(self.config, "glow", None),
                    "enabled", False))
                chams_on = bool(getattr(
                    getattr(self.config.visuals, "chams", None),
                    "enabled", False))
            except AttributeError:
                _scoped_mul_sl.setVisible(True)
                return
            allow_scoped = bool(getattr(tb, "allow_scoped", True))
            hide = (not allow_scoped) or (wh_comp and wh_on
                                          and (glow_on or chams_on))
            _scoped_mul_sl.setVisible(not hide)

        self._scoped_mul_timer = QTimer(tb_card)
        self._scoped_mul_timer.setInterval(250)
        self._scoped_mul_timer.timeout.connect(_refresh_scoped_visibility)
        self._scoped_mul_timer.start()
        _refresh_scoped_visibility()

        van_lay.addWidget(_DistanceBandsEditor(self.config))

        mode_frame = QFrame()
        mode_frame.setObjectName("TriggerModeFrame")
        mode_frame.setProperty("sectionRole", "triggerMode")
        mode_lay = QVBoxLayout(mode_frame)
        mode_lay.setContentsMargins(12, 12, 12, 12)
        mode_lay.setSpacing(10)
        mode_lay.addWidget(feedback_group)
        mode_lay.addWidget(vanilla_group)
        tb_card.insertBodyWidget(0, mode_frame)

        tb_selector.changed.connect(lambda v: (
            feedback_group.setVisible(v == "feedback"),
            vanilla_group.setVisible(v == "vanilla"),
            _refresh_tb_risk(),
        ))
        _tb_version = str(getattr(tb, "version", "feedback"))
        feedback_group.setVisible(_tb_version == "feedback")
        vanilla_group.setVisible(_tb_version == "vanilla")
        _refresh_tb_risk()

        am = self.config.aimbot
        am_card = self.add_widget(FeatureCard(
            "Aimbot",
            risk="high",
            help_text=(
                "Smoothly steers your view toward the selected enemy bone "
                "while the aim key is held, using a cosine velocity profile "
                "with an optional humanized arc. Writes view angles directly "
                "into CS2 memory (<code>dwViewAngles</code>), so it's higher "
                "risk on Faceit than VAC. Honours the read-only and demo "
                "write-guards, so it goes silent whenever Glow does."
            ),
            toggle_target=(am, "enabled"),
        ))
        am_fov = LabeledSlider(
            "FOV (deg)", lo=0.0, hi=60.0,
            value=float(getattr(am, "max_fov_deg", 30.0)),
            decimals=1, risk="high",
            help_text=(
                "Target-selection cone half-angle. Only enemies whose "
                "bone falls within this many degrees of your view are "
                "eligible."
            ),
        )
        am_fov.valueChanged.connect(
            lambda v: setattr(am, "max_fov_deg", float(v)))
        am_card.addBodyWidget(am_fov)

        am_steps = LabeledSlider(
            "Smoothness (steps)", lo=1, hi=30,
            value=int(getattr(am, "smooth_steps", 6)),
            decimals=0, risk="high",
            help_text=(
                "Interpolation ticks per flick - higher is slower and "
                "smoother, lower snaps faster."
            ),
        )
        am_steps.valueChanged.connect(
            lambda v: setattr(am, "smooth_steps", int(v)))
        am_card.addBodyWidget(am_steps)

        am_jitter = LabeledSlider(
            "Jitter (deg)", lo=0.0, hi=1.0,
            value=float(getattr(am, "jitter_deg", 0.05)),
            decimals=2, step=0.01, risk="high",
            help_text=(
                "Uniform per-axis angular noise added to each step so "
                "the path isn't perfectly clean."
            ),
        )
        am_jitter.valueChanged.connect(
            lambda v: setattr(am, "jitter_deg", float(v)))
        am_card.addBodyWidget(am_jitter)

        am_hp = ToggleSwitch()
        am_hp.setChecked(bool(getattr(am, "humanize_path", True)))
        am_hp._live_section = am # type: ignore[attr-defined]
        am_hp._live_attr = "humanize_path" # type: ignore[attr-defined]
        am_hp.toggled.connect(
            lambda c: setattr(am, "humanize_path", bool(c)))
        am_card.addBodyWidget(LabeledRow(
            "Humanize aim path", am_hp, risk="high",
            help_text="Randomized curved approach per flick."))

        am_curv = LabeledSlider(
            "Curvature", lo=0.0, hi=1.0,
            value=float(getattr(am, "curvature", 0.3)),
            decimals=2, step=0.01, risk="high",
            help_text=(
                "Arc magnitude of the humanized approach. 0 = a "
                "straight cosine-ease line."
            ),
        )
        am_curv.valueChanged.connect(
            lambda v: setattr(am, "curvature", float(v)))
        am_card.addBodyWidget(am_curv)
        am_hp.toggled.connect(am_curv.setVisible)
        am_curv.setVisible(bool(getattr(am, "humanize_path", True)))

        am_card.addBodyWidget(ComboRow(
            "Bone target", am, "bone_priority",
            [("head", "Head"), ("body", "Body")],
            default="head",
            theme_name=str(getattr(self.config.general, "ui_theme",
                                   "slate"))))

        am_card.addBodyWidget(KeybindRow("Aimbot · hold", am, "hotkey"))
        am_card.addBodyWidget(KeybindRow("Aimbot · toggle",
                                         am, "hotkey_toggle"))

        rf = self.config.rapidfire
        rf_card = self.add_widget(FeatureCard(
            "Rapid-fire",
            risk="medium",
            help_text=(
                "Auto-clicks the left mouse at a set rate while the hold "
                "hotkey is down, letting semi-auto guns like the Deagle fire "
                "at CS2's cooldown limit. The <i>Jitter</i> slider varies each "
                "gap so the clicks aren't a perfect rhythm anti-cheats can "
                "spot. <b>Hold-only</b> — a toggle bind would keep firing past "
                "the engagement."
            ),
            toggle_target=(rf, "enabled"),
        ))
        rapidfire_cps = LabeledSlider(
            "Clicks / sec", lo=1.0, hi=25.0, value=float(rf.cps),
            decimals=1, step=0.1, help_text="Target click rate.",
        )
        rapidfire_cps.valueChanged.connect(
            lambda v: setattr(rf, "cps", float(v)))
        rf_card.addBodyWidget(rapidfire_cps)
        rapidfire_jitter = LabeledSlider(
            "Jitter (±)", lo=0.0, hi=0.50, value=float(rf.jitter_pct),
            decimals=2, step=0.01,
            help_text="Per-click rate randomisation.",
        )
        rapidfire_jitter.valueChanged.connect(
            lambda v: setattr(rf, "jitter_pct", float(v)))
        rf_card.addBodyWidget(rapidfire_jitter)
        rf_card.addBodyWidget(KeybindRow("Rapid-fire · hold",
                                         rf, "hotkey"))

        ap = self.config.autopistol
        ap_card = self.add_widget(FeatureCard(
            "Auto-pistol",
            risk="medium",
            help_text=(
                "Same click vector as rapid-fire, tuned for pistols: Glock "
                "and Deagle fire as fast as CS2's per-shot cooldown allows. "
                "<b>Hold-only</b> — a toggle bind would leave the click loop "
                "running past the engagement."
            ),
            toggle_target=(ap, "enabled"),
        ))
        autopistol_cps = LabeledSlider(
            "Clicks / sec", lo=1.0, hi=12.0, value=float(ap.cps),
            decimals=1, step=0.1,
        )
        autopistol_cps.valueChanged.connect(
            lambda v: setattr(ap, "cps", float(v)))
        ap_card.addBodyWidget(autopistol_cps)
        autopistol_jitter = LabeledSlider(
            "Jitter (±)", lo=0.0, hi=0.50,
            value=float(ap.jitter_pct), decimals=2, step=0.01,
        )
        autopistol_jitter.valueChanged.connect(
            lambda v: setattr(ap, "jitter_pct", float(v)))
        ap_card.addBodyWidget(autopistol_jitter)
        ap_card.addBodyWidget(KeybindRow("Auto-pistol · hold",
                                         ap, "hotkey"))

        b = self.config.bhop
        bh_card = self.add_widget(FeatureCard(
            "BHop (frame-perfect rejump)",
            risk="medium",
            help_text=(
                "Auto-taps <code>SPACE</code> the instant you land, so you "
                "rejump perfectly every time. You still steer speed with "
                "manual air-strafing — this only handles the timing. Perfect "
                "timing looks robotic to anti-cheats, so the humanise sliders "
                "(skip-every-N + jitter) randomise it."
            ),
            toggle_target=(b, "enabled"),
        ))
        bh_warning = QFrame(bh_card)
        bh_warning.setObjectName("FeatureWarning")
        bh_warning.setProperty("severity", "warning")
        warning_layout = QVBoxLayout(bh_warning)
        warning_layout.setContentsMargins(0, 0, 0, 0)
        warning_layout.setSpacing(3)
        warning_title = QLabel("Community servers only", bh_warning)
        warning_title.setObjectName("FeatureWarningTitle")
        warning_layout.addWidget(warning_title)
        warning_text = QLabel(
            "Use this on KZ, surf, jump, or other custom servers. "
            "Matchmaking rejects taps that are too fast, and VAC has "
            "flagged this kind of pattern before.",
            bh_warning,
        )
        warning_text.setObjectName("FeatureWarningText")
        warning_text.setWordWrap(True)
        warning_layout.addWidget(warning_text)
        bh_card.addBodyWidget(bh_warning)
        for label, attr, lo, hi in [
            ("Min gap between jumps (ms)", "min_gap_ms", 20, 300),
            ("Press jitter (±ms)", "delay_jitter_ms", 0, 30),
        ]:
            slider = LabeledSlider(label, lo=lo, hi=hi,
                                   value=int(getattr(b, attr)), decimals=0)
            slider.valueChanged.connect(
                lambda v, a=attr: setattr(b, a, int(v)))
            bh_card.addBodyWidget(slider)
        hum_sw = ToggleSwitch()
        hum_sw.setChecked(bool(b.humanize))
        hum_sw.toggled.connect(lambda c: setattr(b, "humanize", bool(c)))
        bh_card.addBodyWidget(LabeledRow(
            "Humanize (occasionally skip a bounce)", hum_sw,
            help_text=(
                "Skips one rejump every N landings, where N is rolled "
                "fresh from the [min, max] range below. Defeats "
                "behaviour analysers that fingerprint a constant "
                "every-bounce rhythm."
            ),
        ))
        for label, attr, lo, hi in [
            ("Skip every N — minimum N", "humanize_skip_min", 1, 30),
            ("Skip every N — maximum N", "humanize_skip_max", 1, 30),
            ("Skip-resume delay (ms)", "skip_resume_delay_ms", 20, 1000),
        ]:
            slider = LabeledSlider(label, lo=lo, hi=hi,
                                   value=int(getattr(b, attr)), decimals=0)
            slider.valueChanged.connect(
                lambda v, a=attr: setattr(b, a, int(v)))
            bh_card.addBodyWidget(slider)
        bh_card.addBodyWidget(KeybindRow("BHop · hold", b, "hotkey"))
        bh_card.addBodyWidget(KeybindRow("BHop · toggle", b, "hotkey_toggle"))

        self.end()

    def showEvent(self, event) -> None:
        """Refresh the Triggerbot risk badge on show (visuals live in ESP tab)."""
        super().showEvent(event)
        refresh = getattr(self, "_refresh_tb_risk", None)
        if callable(refresh):
            refresh()

__all__ = ["CombatPanel"]
