"""
Combat settings for aim, firing, and movement automation.
"""

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget

from cs2esp.risk import bind_risk
from cs2esp.ui.qt.panels.base import PanelBase
from cs2esp.ui.qt.widgets import ChoiceRow, ComboRow, FeatureCard, KeybindRow, LabeledRow, LabeledSlider, ToggleSwitch
from cs2esp.ui.qt.widgets.segmented import SegmentedSelector
from cs2esp.ui.qt.widgets.reset import ResetButton


_DEFAULT_BANDS: list[dict] = [
	{"min_m": 4.0, "max_m": 9999.0, "fov_deg": 0.5, "skeleton_fov_deg": 0.5, "label": "long"},
	{"min_m": 2.0, "max_m": 4.0, "fov_deg": 2.0, "skeleton_fov_deg": 1.0, "label": "mid"},
	{"min_m": 0.5, "max_m": 2.0, "fov_deg": 4.0, "skeleton_fov_deg": 2.0, "label": "close"},
	{"min_m": 0.0, "max_m": 0.5, "fov_deg": 8.0, "skeleton_fov_deg": 4.0, "label": "point_blank"},
]

_INFINITY_M = 9999.0
_BAND_SLIDER_CAP_M = 50.0


def _format_distance(value: float) -> str:
	'''Format the infinity sentinel and ordinary metre values.'''
	if float(value) >= _INFINITY_M - 0.5:
		return "∞"
	return f"{float(value):.1f} m"


def _format_band_range(band: dict) -> str:
	'''Format a band's visible range without changing its stored values.'''
	minimum = _format_distance(float(band.get("min_m", 0.0)))
	maximum = _format_distance(float(band.get("max_m", 0.0)))
	return f"{minimum} — {maximum}"


def _overlay_hit_test_hidden(config) -> bool:
	'''True when a glow or chams overlay supplies the wallhack-compliant hit-test.'''
	trigger_config = config.triggerbot
	wallhack_config = config.wallhack
	wh_comp = bool(getattr(trigger_config, "wh_compliant", True))
	wh_on = bool(getattr(wallhack_config, "enabled", True))
	glow_on = bool(getattr(getattr(config, "glow", None), "enabled", False))
	chams_on = bool(getattr(getattr(config.visuals, "chams", None), "enabled", False))
	return wh_comp and wh_on and (glow_on or chams_on)


class _MaxDistanceControl(QWidget):
	"""Bounded distance editor with an infinity-sentinel shortcut."""
	valueChanged = Signal(float)

	def __init__(self, value: float, parent=None) -> None:
		super().__init__(parent)
		self.setObjectName("BandMaxDistanceControl")
		self.setProperty("infinite", False)
		self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
		self._wheel_eater = _BandWheelEater(self)

		layout = QHBoxLayout(self)
		layout.setContentsMargins(0, 0, 0, 0)
		layout.setSpacing(8)

		self._slider = QSlider(Qt.Horizontal, self)
		self._slider.setObjectName("BandMaxDistanceSlider")
		self._slider.setAccessibleName("Maximum range")
		self._slider.setRange(0, int(_BAND_SLIDER_CAP_M * 10))
		self._slider.setSingleStep(1)
		self._slider.setPageStep(int(_BAND_SLIDER_CAP_M * 10) // 20 or 1)
		self._slider.installEventFilter(self._wheel_eater)
		layout.addWidget(self._slider, 1)

		self._spin = QDoubleSpinBox(self)
		self._spin.setObjectName("BandMaxDistanceSpin")
		self._spin.setAccessibleName("Maximum range value")
		self._spin.setRange(0.0, _BAND_SLIDER_CAP_M)
		self._spin.setDecimals(1)
		self._spin.setSingleStep(0.1)
		self._spin.setMinimumWidth(80)
		self._spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
		self._spin.installEventFilter(self._wheel_eater)
		layout.addWidget(self._spin, 0)

		self._inf_btn = QPushButton("∞", self)
		self._inf_btn.setObjectName("BandInfinityBtn")
		self._inf_btn.setCheckable(True)
		self._inf_btn.setMinimumSize(28, 22)
		self._inf_btn.setAccessibleName("Use infinite maximum range")
		self._inf_btn.setFocusPolicy(Qt.NoFocus)
		self._inf_btn.setCursor(Qt.PointingHandCursor)
		self._inf_btn.setToolTip("Treat this band's upper bound as infinity (no cap).")
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
			value = raw / 10.0
			self._spin.setValue(value)
			self.valueChanged.emit(value)
		finally:
			self._suppress = False

	def _on_spin_changed(self, value: float) -> None:
		if self._suppress or self._inf_btn.isChecked():
			return
		self._suppress = True
		try:
			self._slider.setValue(int(round(value * 10)))
			self.valueChanged.emit(float(value))
		finally:
			self._suppress = False

	def _on_infinity_toggled(self, enabled: bool) -> None:
		if self._suppress:
			return
		self.setProperty("infinite", bool(enabled))
		self._slider.setEnabled(not enabled)
		self._spin.setEnabled(not enabled)
		self.valueChanged.emit(_INFINITY_M if enabled else float(self._spin.value()))


class _BandWheelEater(QObject):
	"""Swallow wheel events so scrolling the panel never changes a band value."""
	def eventFilter(self, _obj, event):  # noqa: N802
		if event.type() == QEvent.Wheel:
			event.ignore()
			return True
		return False


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
		header_layout = QHBoxLayout(header)
		header_layout.setContentsMargins(0, 0, 0, 0)
		header_layout.setSpacing(8)

		self._chevron = QPushButton("▸ Distance-band targeting", header)
		self._chevron.setObjectName("BandsChevron")
		self._chevron.setProperty("expanded", False)
		self._chevron.setFocusPolicy(Qt.NoFocus)
		self._chevron.setCursor(Qt.PointingHandCursor)
		self._chevron.setCheckable(True)
		self._chevron.toggled.connect(self._on_expand_toggled)
		header_layout.addWidget(self._chevron, 1, Qt.AlignVCenter)

		reset_button = ResetButton("Distance-band targeting", config, ("triggerbot.distance_bands",), header)
		reset_button.applied.connect(lambda _changed: self._rebuild_rows())
		header_layout.addWidget(reset_button, 0, Qt.AlignVCenter)

		outer.addWidget(header)

		self._body = QWidget(self)
		self._body.setObjectName("BandsBody")
		self._body.setProperty("sectionRole", "advanced")
		body_layout = QVBoxLayout(self._body)
		body_layout.setContentsMargins(0, 4, 0, 0)
		body_layout.setSpacing(10)

		bands_description = QLabel("Tune the point and skeleton target cones for each range.", self._body)
		bands_description.setObjectName("BandsDescription")
		bands_description.setWordWrap(True)
		body_layout.addWidget(bands_description)

		self._bands_layout = QVBoxLayout()
		self._bands_layout.setContentsMargins(0, 0, 0, 0)
		self._bands_layout.setSpacing(12)
		body_layout.addLayout(self._bands_layout)

		add_row = QHBoxLayout()
		add_row.setContentsMargins(0, 0, 0, 0)
		add_button = QPushButton("+ Add band", self._body)
		add_button.setObjectName("BandsAdd")
		add_button.setFocusPolicy(Qt.NoFocus)
		add_button.setCursor(Qt.PointingHandCursor)
		add_button.setToolTip("Append a new band to the list with placeholder values; you can rename and re-range it after creation.")
		add_button.clicked.connect(self._add_band)
		add_row.addWidget(add_button, 0, Qt.AlignLeft)
		add_row.addStretch(1)
		body_layout.addLayout(add_row)

		self._body.setVisible(False)
		outer.addWidget(self._body)

		bands = getattr(self._tb, "distance_bands", None)
		if not isinstance(bands, list) or not bands:
			self._tb.distance_bands = [dict(band) for band in _DEFAULT_BANDS]

		self._rebuild_rows()

		self._visibility_timer = QTimer(self)
		self._visibility_timer.setInterval(250)
		self._visibility_timer.timeout.connect(self._refresh_visibility)
		self._visibility_timer.start()
		self._refresh_visibility()

	def _refresh_visibility(self) -> None:
		'''Hide the bone-angle bands when Glow or Chams supplies the hit-test.'''
		try:
			hidden = _overlay_hit_test_hidden(self._config)
		except AttributeError:
			self.setVisible(True)
			return
		self.setVisible(not hidden)

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
			self._bands_layout.addWidget(self._build_band_row(index, band, len(bands)))

	def _build_band_row(self, band_index: int, band: dict, band_count: int) -> QWidget:
		row = QFrame(self)
		row.setObjectName("BandRow")
		row.setProperty("bandIndex", band_index)
		row.setFrameShape(QFrame.NoFrame)
		layout = QVBoxLayout(row)
		layout.setContentsMargins(12, 10, 12, 12)
		layout.setSpacing(8)

		header = QWidget(row)
		header.setObjectName("BandRowHeader")
		title_row = QHBoxLayout(header)
		title_row.setContentsMargins(0, 0, 0, 0)
		title_row.setSpacing(8)

		title = QLabel(f"Band {band_index + 1}", header)
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
		remove_button.setToolTip("Delete this band from the list." if band_count > 1 else "At least one distance band is required.")
		remove_button.clicked.connect(lambda _checked=False, selected_index=band_index: self._remove_band(selected_index))
		title_row.addWidget(remove_button, 0, Qt.AlignVCenter)
		layout.addWidget(header)

		min_slider = LabeledSlider("Min distance (m)", lo=0.0, hi=_BAND_SLIDER_CAP_M, value=float(band.get("min_m", 0.0)), decimals=1, step=0.1)
		min_slider.valueChanged.connect(lambda value, selected_index=band_index, label=range_label: self._write_band(selected_index, "min_m", float(value), label))
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
		max_ctrl = _MaxDistanceControl(float(band.get("max_m", 0.0)), max_row)
		max_ctrl.valueChanged.connect(lambda value, selected_index=band_index, label=range_label: self._write_band(selected_index, "max_m", float(value), label))
		max_lbl_row.addWidget(max_ctrl, 3)
		layout.addWidget(max_row)

		for label, key in [("FOV (point)", "fov_deg"), ("FOV (skeleton)", "skeleton_fov_deg")]:
			slider = LabeledSlider(label, lo=0.1, hi=12.0, value=float(band.get(key, 0.0)), decimals=1, step=0.1)
			slider.valueChanged.connect(lambda value, selected_index=band_index, field=key: self._write_band(selected_index, field, float(value)))
			layout.addWidget(slider)
		return row

	def _write_band(self, band_index: int, key: str, value: float, range_label: QLabel | None = None) -> None:
		bands = getattr(self._tb, "distance_bands", None)
		if not isinstance(bands, list) or band_index >= len(bands):
			return
		bands = [dict(band) for band in bands]
		bands[band_index][key] = value
		self._tb.distance_bands = bands
		if key in ("min_m", "max_m") and range_label is not None:
			range_label.setText(_format_band_range(bands[band_index]))

	def _add_band(self) -> None:
		'''Append a new band with placeholder values.'''
		bands = list(getattr(self._tb, "distance_bands", []) or [])
		biggest = 0.0
		for band in bands:
			try:
				current_maximum = float(band.get("max_m", 0.0))
				if current_maximum >= _INFINITY_M - 0.5:
					biggest = _BAND_SLIDER_CAP_M
					break
				if current_maximum > biggest:
					biggest = current_maximum
			except Exception:
				continue
		start = min(_BAND_SLIDER_CAP_M, biggest + 1.0)
		bands.append({"min_m": float(start), "max_m": _INFINITY_M, "fov_deg": 1.0, "skeleton_fov_deg": 1.0, "label": f"custom-{len(bands) + 1}"})
		self._tb.distance_bands = bands
		self._rebuild_rows()

	def _remove_band(self, band_index: int) -> None:
		'''Delete one band; the final band is kept so the runtime always has a fallback.'''
		bands = list(getattr(self._tb, "distance_bands", []) or [])
		if band_index < 0 or band_index >= len(bands):
			return
		if len(bands) <= 1:
			return
		bands.pop(band_index)
		self._tb.distance_bands = bands
		self._rebuild_rows()


class CombatPanel(PanelBase):
	"""Configure aim, firing, and movement automation."""
	RESET_SCOPES = {
		"Triggerbot": ("triggerbot",), "Aimbot": ("aimbot",), "Rapid-fire": ("rapidfire",),
		"Auto-pistol": ("autopistol",), "BHop (frame-perfect rejump)": ("bhop",),
	}

	def __init__(self, config, parent: QWidget = None) -> None:
		super().__init__(config, parent)

		self.h1("Combat")
		self.intro(
			(
				'Tune target acquisition, shot timing and movement automation. Aimbot writes view angles; normal calls honor the existing '
				'read-only flag. Badges describe policy and implementation, not detection probabilities.'
			)
		)

		self.h2("Aim, fire & movement")
		trigger_config = self.config.triggerbot
		tb_card = self.add_widget(
			FeatureCard(
				"Triggerbot",
				risk=bind_risk('triggerbot', lambda: self.config),
				help_text=(
					"<b>Feedback:</b> reads the current target and health, fires through synthetic input, then pauses after an unconfirmed hit. "
					"<b>Vanilla:</b> uses the engine target with bone, head-ring or overlay-pixel gates. Neither trigger path writes game memory. "
					"Native Glow is a separate memory-writing feature; both Chams renderers draw only in this app's overlay. "
					"The policy badge follows the configured mode while this panel is open. Automated firing remains prohibited in competitive use; "
					"delay and geometry filters do not establish detector coverage."
				),
				toggle_target=(trigger_config, "enabled"),
			)
		)

		self._tb_card = tb_card
		self._refresh_tb_risk = tb_card.refreshRisk

		tb_selector = SegmentedSelector(trigger_config, "version", [("feedback", "Feedback"), ("vanilla", "Vanilla")], default="feedback", equal_width=True)
		tb_card.addTitleWidget(tb_selector)

		def _tb_toggle_row(attr: str, label: str, tip: str, on_change=None) -> LabeledRow:
			switch = ToggleSwitch()
			switch.setChecked(bool(getattr(trigger_config, attr, True)))
			switch._live_section = trigger_config  # type: ignore[attr-defined]
			switch._live_attr = attr  # type: ignore[attr-defined]
			switch.toggled.connect(lambda checked, attr=attr: setattr(trigger_config, attr, bool(checked)))
			if on_change is not None:
				switch.toggled.connect(lambda _checked: on_change())
			return LabeledRow(label, switch, help_text=tip)

		tb_card.addBodyWidget(
			_tb_toggle_row(
				"head_only",
				"Shoot only on head",
				(
					'Adds the implemented bone-derived head-geometry gate to firing. The projected ring and estimated sphere are not '
					'authoritative engine hitboxes or proof of wall visibility.'
				),
			)
		)

		tb_card.addBodyWidget(
			_tb_toggle_row(
				"scoped_require_scope",
				"Scoped weapons: fire only when scoped",
				"ON (default): with an AWP/SSG/SCAR/G3SG1/AUG/SG the triggerbot shoots only while actually scoped in. Applies to both modes.",
			)
		)

		tb_card.addBodyWidget(
			ComboRow(
				"Fire condition",
				trigger_config,
				"fire_condition",
				[("always", "Always"), ("shift", "While Shift held"), ("ctrl", "While Ctrl held"), ("no_move", "Only while standing still")],
				default="always",
			)
		)

		# Weapon-class whitelist
		_wclass_hdr = QLabel("Fire on weapons")
		tb_card.addBodyWidget(_wclass_hdr)
		for label, attr, tip in [
			("Rifles & SMGs", "allow_rifles", "AK/M4/Galil/FAMAS/SG553 — and the SMG toggle below."),
			("SMGs", "allow_smgs", "MAC-10, MP9, MP5, UMP-45, P90, MP7."),
			("Shotguns", "allow_shotguns", "Nova, XM1014, MAG-7, Sawed-Off."),
			("Sniper rifles", "allow_snipers", "AWP, SSG08, SCAR-20, G3SG1. Still subject to the fire-only-when-scoped gate above."),
			("Pistols", "allow_pistols", "Glock through Deagle, P250, Five-SeveN, Tec-9, CZ, R8."),
			("LMGs", "allow_lmg", "M249, Negev."),
			("Grenades", "allow_grenades", "HE, flash, smoke, decoy, molotov, incendiary."),
			("Knife & Zeus", "allow_knife_zeus", "All knife skins plus the Zeus x27 taser."),
		]:
			tb_card.addBodyWidget(_tb_toggle_row(attr, label, tip))

		for label, attr, minimum, maximum, tip in [
			(
				"Refire cooldown (ms)",
				"refire_cooldown_ms",
				0,
				2000,
				"Minimum milliseconds between two auto-fires — the floor "
				"between bursts, so the trigger can't re-fire faster than "
				"this even while the crosshair stays on the target.",
			),
			("Reaction delay (ms)", "delay_ms", 0, 500, "Base human-reaction delay applied after the crosshair lands on an enemy hitbox before the shot is sent."),
			(
				"Delay jitter (±ms)",
				"delay_jitter_ms",
				0,
				200,
				"Random ± milliseconds added to each reaction delay (100 ms ±20 gives 80–120 ms). "
				"This changes timing only; no detector-avoidance benefit is established.",
			),
			(
				"Min reaction (ms)",
				"min_delay_ms",
				0,
				500,
				"Hard floor the jittered reaction delay can never drop below, so jitter can't produce an inhumanly fast shot.",
			),
		]:
			slider = LabeledSlider(label, lo=minimum, hi=maximum, value=int(getattr(trigger_config, attr)), decimals=0, help_text=tip)
			slider.valueChanged.connect(lambda value, attr=attr: setattr(trigger_config, attr, int(value)))
			tb_card.addBodyWidget(slider)

		tb_card.addBodyWidget(KeybindRow("Triggerbot · hold", trigger_config, "hotkey"))
		tb_card.addBodyWidget(KeybindRow("Triggerbot · toggle", trigger_config, "hotkey_toggle"))

		feedback_group = QWidget()
		feedback_group.setObjectName("TriggerModePanel")
		feedback_group.setProperty("mode", "feedback")
		fb_lay = QVBoxLayout(feedback_group)
		fb_lay.setContentsMargins(0, 0, 0, 0)
		fb_lay.setSpacing(10)
		fb_confirm = LabeledSlider(
			"Confirm window (ms)",
			lo=0,
			hi=1000,
			value=int(getattr(trigger_config, "feedback_confirm_ms", 100)),
			decimals=0,
			risk=bind_risk('triggerbot', lambda: self.config),
			help_text=(
				"Per-shot window to confirm a hit. If the target's health doesn't drop within this many ms of a click, that shot counts as a confirmed miss."
			),
		)
		fb_confirm.valueChanged.connect(lambda value: setattr(trigger_config, "feedback_confirm_ms", int(value)))
		fb_lay.addWidget(fb_confirm)
		fb_resume = LabeledSlider(
			"Resume pause (ms)",
			lo=0,
			hi=2000,
			value=int(getattr(trigger_config, "feedback_resume_ms", 100)),
			decimals=0,
			risk=bind_risk('triggerbot', lambda: self.config),
			help_text=("After a confirmed miss, pause firing this many ms before re-baselining the target and resuming."),
		)
		fb_resume.valueChanged.connect(lambda value: setattr(trigger_config, "feedback_resume_ms", int(value)))
		fb_lay.addWidget(fb_resume)

		vanilla_group = QWidget()
		vanilla_group.setObjectName("TriggerModePanel")
		vanilla_group.setProperty("mode", "vanilla")
		van_lay = QVBoxLayout(vanilla_group)
		van_lay.setContentsMargins(0, 0, 0, 0)
		van_lay.setSpacing(10)

		van_lay.addWidget(
			_tb_toggle_row(
				"wh_compliant",
				"Wallhack-compliant",
				(
					'Selects the configured visual hit-test: Glow, Chams, projected head ring, then default bone-angle gates. Overlay pixels and '
					'spotted state are not a game-world wall-occlusion test. Turning this off selects the default geometry path; it does not '
					'change automation policy.'
				),
				on_change=tb_card.refreshRisk,
			)
		)

		_scoped_mul_sl = LabeledSlider(
			"Scoped FOV multiplier",
			lo=0.0,
			hi=1.0,
			value=float(getattr(trigger_config, "scoped_band_multiplier", 0.30)),
			decimals=2,
			step=0.05,
			help_text=(
				(
					'Scales distance-band fire cones while scoped. A value of 0 suppresses this scoped band path; 1 leaves it unchanged. Glow and '
					'Chams paths use different target or overlay geometry, not authoritative wall visibility.'
				)
			),
		)
		_scoped_mul_sl.setProperty("settingRole", "scopedMultiplier")
		_scoped_mul_sl.valueChanged.connect(lambda value: setattr(trigger_config, "scoped_band_multiplier", float(value)))
		van_lay.addWidget(_scoped_mul_sl)

		def _refresh_scoped_visibility() -> None:
			try:
				hidden = _overlay_hit_test_hidden(self.config)
			except AttributeError:
				_scoped_mul_sl.setVisible(True)
				return
			allow_scoped = bool(getattr(trigger_config, "allow_scoped", True))
			_scoped_mul_sl.setVisible(allow_scoped and not hidden)

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

		tb_selector.changed.connect(lambda value: (feedback_group.setVisible(value == "feedback"), vanilla_group.setVisible(value == "vanilla"), tb_card.refreshRisk()))
		_tb_version = str(getattr(trigger_config, "version", "feedback"))
		feedback_group.setVisible(_tb_version == "feedback")
		vanilla_group.setVisible(_tb_version == "vanilla")
		tb_card.refreshRisk()

		aim_config = self.config.aimbot
		am_card = self.add_widget(
			FeatureCard(
				"Aimbot",
				risk=bind_risk('aimbot', lambda: self.config),
				help_text=(
					"Selects an enemy bone and writes <code>dwViewAngles</code> to steer the view. The existing read-only flag gates normal aim writes. "
					"Spotted state is team metadata, not a local wall-occlusion test. Smoothing and randomized paths do not change the automation policy "
					"or establish a VAC/FACEIT detection probability."
				),
				toggle_target=(aim_config, "enabled"),
			)
		)
		am_fov = LabeledSlider(
			"FOV (deg)",
			lo=0.0,
			hi=60.0,
			value=float(getattr(aim_config, "max_fov_deg", 30.0)),
			decimals=1,
			risk=bind_risk('aimbot', lambda: self.config),
			help_text=("Target-selection cone half-angle. Only enemies whose bone falls within this many degrees of your view are eligible."),
		)
		am_fov.valueChanged.connect(lambda value: setattr(aim_config, "max_fov_deg", float(value)))
		am_card.addBodyWidget(am_fov)

		am_steps = LabeledSlider(
			"Smoothness (steps)",
			lo=1,
			hi=30,
			value=int(getattr(aim_config, "smooth_steps", 6)),
			decimals=0,
			risk=bind_risk('aimbot', lambda: self.config),
			help_text=("Interpolation ticks per flick - higher is slower and smoother, lower snaps faster."),
		)
		am_steps.valueChanged.connect(lambda value: setattr(aim_config, "smooth_steps", int(value)))
		am_card.addBodyWidget(am_steps)

		am_jitter = LabeledSlider(
			"Jitter (deg)",
			lo=0.0,
			hi=1.0,
			value=float(getattr(aim_config, "jitter_deg", 0.05)),
			decimals=2,
			step=0.01,
			risk=bind_risk('aimbot', lambda: self.config),
			help_text=("Uniform per-axis angular noise added to each step so the path isn't perfectly clean."),
		)
		am_jitter.valueChanged.connect(lambda value: setattr(aim_config, "jitter_deg", float(value)))
		am_card.addBodyWidget(am_jitter)

		am_hp = ToggleSwitch()
		am_hp.setChecked(bool(getattr(aim_config, "humanize_path", True)))
		am_hp._live_section = aim_config  # type: ignore[attr-defined]
		am_hp._live_attr = "humanize_path"  # type: ignore[attr-defined]
		am_hp.toggled.connect(lambda checked: setattr(aim_config, "humanize_path", bool(checked)))
		am_card.addBodyWidget(LabeledRow("Humanize aim path", am_hp, risk=bind_risk('aimbot', lambda: self.config), help_text="Randomized curved approach per flick."))

		am_curv = LabeledSlider(
			"Curvature",
			lo=0.0,
			hi=1.0,
			value=float(getattr(aim_config, "curvature", 0.3)),
			decimals=2,
			step=0.01,
			risk=bind_risk('aimbot', lambda: self.config),
			help_text=("Arc magnitude of the humanized approach. 0 = a straight cosine-ease line."),
		)
		am_curv.valueChanged.connect(lambda value: setattr(aim_config, "curvature", float(value)))
		am_card.addBodyWidget(am_curv)
		am_hp.toggled.connect(am_curv.setVisible)
		am_curv.setVisible(bool(getattr(aim_config, "humanize_path", True)))

		# Extra humanizer knobs
		humanize_rows = []

		def _am_slider(label, attr, lo, hi, default, decimals, step, help_text, cast=float):
			slider = LabeledSlider(
				label, lo=lo, hi=hi, value=cast(getattr(aim_config, attr, default)), decimals=decimals, step=step,
				risk=bind_risk('aimbot', lambda: self.config), help_text=help_text
			)
			slider.valueChanged.connect(lambda value, attr=attr: setattr(aim_config, attr, cast(value)))
			am_card.addBodyWidget(slider)
			return slider

		humanize_rows.append(
			_am_slider(
				"Zigzag waves",
				"zigzag_waves",
				1,
				5,
				3,
				0,
				1,
				"How many times the approach weaves across the straight line. 1 = a single arc; higher = an S/zigzag path. Scaled by Curvature.",
				cast=int,
			)
		)
		humanize_rows.append(
			_am_slider(
				"Speed variance",
				"speed_variance",
				0.0,
				0.9,
				0.35,
				2,
				0.01,
				"Random uneven speed along the path per flick (fast start / slow finish or the reverse). Endpoints stay fixed.",
			)
		)
		humanize_rows.append(
			_am_slider(
				"Overshoot (%)",
				"overshoot_pct",
				0.0,
				25.0,
				0.0,
				1,
				0.5,
				"Flick past the target by up to this share of the turn, then the tracking pass pulls back. 0 = never overshoot.",
			)
		)
		humanize_rows.append(
			_am_slider("Lock tremor (deg)", "tremor_deg", 0.0, 0.3, 0.0, 2, 0.01, "Small hand-shake while locked on. Keep near 0 for a steady lock.")
		)
		for row in humanize_rows:
			am_hp.toggled.connect(row.setVisible)
			row.setVisible(bool(getattr(aim_config, "humanize_path", True)))

		am_adapt = ToggleSwitch()
		am_adapt.setChecked(bool(getattr(aim_config, "adaptive_steps", True)))
		am_adapt._live_section = aim_config  # type: ignore[attr-defined]
		am_adapt._live_attr = "adaptive_steps"  # type: ignore[attr-defined]
		am_adapt.toggled.connect(lambda checked: setattr(aim_config, "adaptive_steps", bool(checked)))
		am_card.addBodyWidget(
			LabeledRow(
				"Distance-adaptive smoothing",
				am_adapt,
				risk=bind_risk('aimbot', lambda: self.config),
				help_text="Small corrections finish faster, big turns take longer (0.6x to 1.5x of Smoothness), like a real wrist.",
			)
		)

		_am_slider("Reaction min (ms)", "reaction_min_ms", 0.0, 400.0, 0.0, 0, 5, "Shortest pause between pressing the aim key and the first move.")
		_am_slider("Reaction max (ms)", "reaction_max_ms", 0.0, 400.0, 0.0, 0, 5, "Longest reaction pause; each press picks a random value in range.")
		_am_slider(
			"Target switch delay (ms)",
			"switch_delay_ms",
			0.0,
			500.0,
			120.0,
			0,
			5,
			"Pause before moving to a different enemy after the current one dies or leaves the cone. Bone changes on the same enemy never wait.",
		)

		am_card.addBodyWidget(
			ComboRow(
				"Bone target",
				aim_config,
				"bone_priority",
				[("head", "Head"), ("body", "Body")],
				default="head",
				theme_name=str(getattr(self.config.general, "ui_theme", "slate")),
			)
		)

		am_card.addBodyWidget(
			ComboRow(
				"Target visibility",
				aim_config,
				"visibility",
				[("visible", "Visible only"), ("recent", "Visible + short peek grace"), ("any", "Through walls")],
				default="visible",
				theme_name=str(getattr(self.config.general, "ui_theme", "slate")),
			)
		)

		am_card.addBodyWidget(KeybindRow("Aimbot · hold", aim_config, "hotkey"))
		am_card.addBodyWidget(KeybindRow("Aimbot · toggle", aim_config, "hotkey_toggle"))

		rapidfire_config = self.config.rapidfire
		rf_card = self.add_widget(
			FeatureCard(
				"Rapid-fire",
				risk=bind_risk('rapidfire', lambda: self.config),
				help_text=(
					(
						'Sends continuous taps or fixed-size click bursts while a firearm is held. Burst pause starts after the last release; '
						'CPS controls the nominal cadence; jitter varies it. The game still controls accepted shots. Auto-pistol takes priority on supported handguns.'
					)
				),
				toggle_target=(rapidfire_config, "enabled"),
			)
		)
		rapidfire_pattern = ChoiceRow("Click pattern", rapidfire_config, "pattern", [("continuous", "Continuous"), ("burst", "Burst")], default="continuous")
		rf_card.addBodyWidget(rapidfire_pattern)
		rapidfire_cps = LabeledSlider("Clicks / sec", lo=1.0, hi=25.0, value=float(rapidfire_config.cps), decimals=1, step=0.1, help_text="Target click rate.")
		rapidfire_cps.valueChanged.connect(lambda value: setattr(rapidfire_config, "cps", float(value)))
		rf_card.addBodyWidget(rapidfire_cps)
		rapidfire_jitter = LabeledSlider(
			"Jitter (±)", lo=0.0, hi=0.50, value=float(rapidfire_config.jitter_pct), decimals=2, step=0.01, help_text="Per-click rate randomisation."
		)
		rapidfire_jitter.valueChanged.connect(lambda value: setattr(rapidfire_config, "jitter_pct", float(value)))
		rf_card.addBodyWidget(rapidfire_jitter)
		burst_size = LabeledSlider("Clicks per burst", lo=2, hi=10, value=int(getattr(rapidfire_config, "burst_size", 3)), decimals=0, step=1)
		burst_size.valueChanged.connect(lambda value: setattr(rapidfire_config, "burst_size", int(value)))
		rf_card.addBodyWidget(burst_size)
		burst_pause = LabeledSlider("Pause between bursts (ms)", lo=0, hi=1000,
			value=float(getattr(rapidfire_config, "burst_pause_ms", 150.0)), decimals=0, step=5)
		burst_pause.valueChanged.connect(lambda value: setattr(rapidfire_config, "burst_pause_ms", float(value)))
		rf_card.addBodyWidget(burst_pause)

		def sync_burst_controls(pattern: str) -> None:
			burst_size.setVisible(pattern == "burst")
			burst_pause.setVisible(pattern == "burst")

		rapidfire_pattern.changed.connect(sync_burst_controls)
		sync_burst_controls(str(getattr(rapidfire_config, "pattern", "continuous")))
		rf_card.addBodyWidget(KeybindRow("Rapid-fire · hold", rapidfire_config, "hotkey"))
		rf_card.addBodyWidget(KeybindRow("Rapid-fire · toggle", rapidfire_config, "hotkey_toggle"))

		pistol_config = self.config.autopistol
		ap_card = self.add_widget(
			FeatureCard(
				"Auto-pistol",
				risk=bind_risk('autopistol', lambda: self.config),
				help_text=(
					(
						'Taps only while a supported semi-auto handgun is held and checks its primary-attack tick when the game clock is readable. '
						'Weapon switches cancel the old press. CZ75-Auto, R8, non-pistols and unreadable weapon identities pause this clicker. '
						'If both clickers are active, this one owns handgun taps. CPS controls tap cadence; it does not bypass weapon cooldowns.'
					)
				),
				toggle_target=(pistol_config, "enabled"),
			)
		)
		autopistol_cps = LabeledSlider("Clicks / sec", lo=1.0, hi=12.0, value=float(pistol_config.cps), decimals=1, step=0.1)
		autopistol_cps.valueChanged.connect(lambda value: setattr(pistol_config, "cps", float(value)))
		ap_card.addBodyWidget(autopistol_cps)
		autopistol_jitter = LabeledSlider("Jitter (±)", lo=0.0, hi=0.50, value=float(pistol_config.jitter_pct), decimals=2, step=0.01)
		autopistol_jitter.valueChanged.connect(lambda value: setattr(pistol_config, "jitter_pct", float(value)))
		ap_card.addBodyWidget(autopistol_jitter)
		ap_card.addBodyWidget(KeybindRow("Auto-pistol · hold", pistol_config, "hotkey"))
		ap_card.addBodyWidget(KeybindRow("Auto-pistol · toggle", pistol_config, "hotkey_toggle"))

		bhop_config = self.config.bhop
		bh_card = self.add_widget(
			FeatureCard(
				"BHop (frame-perfect rejump)",
				risk=bind_risk('bhop', lambda: self.config),
				help_text=(
					(
						'Reads movement state and predicts landing timing to synthesize SPACE events. Air-strafing remains manual. Prediction can '
						'miss; skips and jitter change timing, not the policy classification or detector coverage.'
					)
				),
				toggle_target=(bhop_config, "enabled"),
			)
		)
		bh_warning = QFrame(bh_card)
		bh_warning.setObjectName("FeatureWarning")
		bh_warning.setProperty("severity", "warning")
		warning_layout = QVBoxLayout(bh_warning)
		warning_layout.setContentsMargins(0, 0, 0, 0)
		warning_layout.setSpacing(3)
		warning_title = QLabel('Server rules and automation policy', bh_warning)
		warning_title.setObjectName("FeatureWarningTitle")
		warning_layout.addWidget(warning_title)
		warning_text = QLabel(
			(
				'Valve and FACEIT restrict gameplay automation. Community servers may also use VAC and impose their own bans; a '
				'practice-server label is not approval.'
			),
			bh_warning,
		)
		warning_text.setObjectName("FeatureWarningText")
		warning_text.setWordWrap(True)
		warning_layout.addWidget(warning_text)
		bh_card.addBodyWidget(bh_warning)
		for label, attr, minimum, maximum in [("Min gap between jumps (ms)", "min_gap_ms", 20, 300), ("Press jitter (±ms)", "delay_jitter_ms", 0, 30)]:
			slider = LabeledSlider(label, lo=minimum, hi=maximum, value=int(getattr(bhop_config, attr)), decimals=0)
			slider.valueChanged.connect(lambda value, attr=attr: setattr(bhop_config, attr, int(value)))
			bh_card.addBodyWidget(slider)
		hum_sw = ToggleSwitch()
		hum_sw.setChecked(bool(bhop_config.humanize))
		hum_sw.toggled.connect(lambda checked: setattr(bhop_config, "humanize", bool(checked)))
		bh_card.addBodyWidget(
			LabeledRow(
				"Humanize (occasionally skip a bounce)",
				hum_sw,
				help_text=(
					'Skips one rejump every N landings; N is drawn from the configured range. No detector-avoidance benefit is established.'
				),
			)
		)
		for label, attr, minimum, maximum in [
			("Skip every N — minimum N", "humanize_skip_min", 1, 30),
			("Skip every N — maximum N", "humanize_skip_max", 1, 30),
			("Skip-resume delay (ms)", "skip_resume_delay_ms", 20, 1000),
		]:
			slider = LabeledSlider(label, lo=minimum, hi=maximum, value=int(getattr(bhop_config, attr)), decimals=0)
			slider.valueChanged.connect(lambda value, attr=attr: setattr(bhop_config, attr, int(value)))
			bh_card.addBodyWidget(slider)
		bh_card.addBodyWidget(KeybindRow("BHop · hold", bhop_config, "hotkey"))
		bh_card.addBodyWidget(KeybindRow("BHop · toggle", bhop_config, "hotkey_toggle"))

		self.end()

	def showEvent(self, event) -> None:
		'''Refresh the Triggerbot risk badge on show (visuals live in ESP tab).'''
		super().showEvent(event)
		card = getattr(self, "_tb_card", None)
		if card is not None:
			card.refreshRisk()


__all__ = ["CombatPanel"]
