"""
Application appearance, lifecycle, persistence, and diagnostics panel.
"""

from html import escape
from time import localtime, monotonic, strftime
from types import SimpleNamespace

from PySide6.QtCore import QSignalBlocker, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication, QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QRadioButton, QSizePolicy, QVBoxLayout, QWidget

from cs2esp.diagnostics import run_full_health_check
from cs2esp.display_info import get_display_refresh_hz
from cs2esp.risk import bind_risk
from cs2esp.ui.qt.panels.base import PanelBase
from cs2esp.ui.qt.widgets import ComboRow, FeatureCard, LabeledRow, LabeledSlider, ToggleSwitch


_THEME_LABELS = {"midnight": "Midnight — high-contrast near-black", "slate": "Slate — balanced graphite (default)", "daylight": "Daylight — clean light"}

_RENDER_PROFILES = (("efficient", "Efficient"), ("balanced", "Balanced"), ("low_latency", "Low latency"), ("energy_saving", "Energy saving"))
_PROFILE_DESCRIPTIONS = {
	"efficient": "90 Hz state reader",
	"balanced": "75 Hz state reader",
	"low_latency": "120 Hz state reader",
	"energy_saving": "60 Hz state reader",
}


_OFFSET_WORKERS: set[QThread] = set()


def _finish_offset_workers() -> None:
	'''Join bounded network/cache work before QApplication tears its threads down.'''
	for worker in tuple(_OFFSET_WORKERS):
		worker.requestInterruption()
		worker.wait()
	_OFFSET_WORKERS.clear()


class _OffsetRefreshWorker(QThread):
	reportReady = Signal(object)

	def __init__(self) -> None:
		super().__init__()
		app = QApplication.instance()
		if app is not None and not app.property("offsetWorkerShutdownConnected"):
			app.aboutToQuit.connect(_finish_offset_workers)
			app.setProperty("offsetWorkerShutdownConnected", True)

	def run(self) -> None:
		try:
			from cs2esp.offsets import refresh_offsets_live

			self.reportReady.emit({"report": refresh_offsets_live()})
		except Exception as exc:
			self.reportReady.emit({"error": repr(exc)})


class SettingsPanel(PanelBase):
	"""Settings panel; on_theme_change is forwarded to MainWindow."""
	RESET_SCOPES = {
		"Appearance": ("general.ui_theme", "general.ui_scale", "general.custom_theme_color"),
		"Runtime & persistence": ("general.target_fps", "general.autosave_interval_sec"),
		"Overlay renderer": ("overlay.render_device", "overlay.render_profile", "overlay.render_profiles"),
	}

	def __init__(self, config, on_theme_change=None, parent=None) -> None:
		super().__init__(config, parent)
		self._on_theme_change = on_theme_change
		self._toggles: list[ToggleSwitch] = []
		self._sliders: list[LabeledSlider] = []

		general_config = self.config.general
		self.h1("Settings")
		self.intro(
			(
				"Tune the menu's appearance, launch behavior and runtime cadence. Diagnostic results describe implementation preconditions, "
				'not permission or detector compatibility.'
			)
		)

		display_card = FeatureCard(
			"Appearance",
			help_text=(
				"Pick the palette the menu paints itself with. The "
				"theme picker and the sidebar pill share the same "
				"config field, so changes here are visible everywhere "
				"immediately."
			),
			risk=bind_risk('appearance', lambda: self.config),
		)
		self.add_widget(display_card)

		theme_row = QWidget(display_card)
		theme_row.setObjectName("ThemeOptions")
		theme_layout = QVBoxLayout(theme_row)
		theme_layout.setContentsMargins(0, 0, 0, 0)
		theme_layout.setSpacing(6)

		theme_label = QLabel("Theme")
		theme_label.setObjectName("ThemeOptionsLabel")
		theme_layout.addWidget(theme_label)

		self._theme_group = QButtonGroup(self)
		cur_theme = str(getattr(general_config, "ui_theme", "midnight"))
		for name in ("midnight", "slate", "daylight"):
			theme_button = QRadioButton(_THEME_LABELS[name], theme_row)
			theme_button.setChecked(name == cur_theme)
			theme_button.toggled.connect(lambda checked, n=name: self._on_theme_pick(n, checked))
			self._theme_group.addButton(theme_button)
			theme_layout.addWidget(theme_button)

		display_card.addBodyWidget(theme_row)

		scale_value = float(getattr(general_config, "ui_scale", 1.0) or 1.0)
		if scale_value < 0.5:
			scale_value = 1.0
		scale_slider = LabeledSlider(
			"UI scale",
			lo=0.5,
			hi=2.5,
			value=scale_value,
			decimals=2,
			step=0.05,
			help_text=(
				"Multiplier applied to every menu font size. 1.0 = "
				"baseline (auto). Useful on 4K monitors where Qt's "
				"built-in HiDPI scaling isn't aggressive enough for "
				"comfortable reading. Use the up/down arrows or type "
				"a value directly — the mouse wheel is intentionally "
				"disabled here so you can't bump it by mistake while "
				"scrolling through the panel."
			),
		)
		scale_slider.setWheelEnabled(False)
		scale_slider.valueChanged.connect(self._on_scale_changed)
		self._track_slider(scale_slider)
		display_card.addBodyWidget(scale_slider)

		window_card = FeatureCard(
			"Window behaviour",
			help_text=(
				"Controls how the menu window behaves at launch. The "
				"menu lives in its own OS window with always-on-top "
				"set, so it appears above CS2 even in borderless-"
				"fullscreen mode — you don't have to alt-tab to "
				"see it."
			),
			risk=bind_risk('window_behavior', lambda: self.config),
		)
		self.add_widget(window_card)

		for label, attr, tip in [
			(
				"Auto-focus CS2 on launch",
				"focus_cs2_on_launch",
				('Requests game focus at launch when enabled. This is window behavior only; operating-system foreground rules may refuse the request.'),
			),
			(
				"Hide terminal window",
				"hide_terminal",
				(
					'Requests hiding a console owned solely by the app. A shared terminal is left visible. Hiding a console does not hide the '
					'process or establish anti-cheat approval.'
				),
			),
			(
				"Start with menu visible",
				"start_menu_visible",
				"Show the menu on launch instead of waiting for the "
				"toggle hotkey. When off, the menu subprocess only "
				"spawns on the first hotkey press — saves "
				"\u2248200 MB of resident PySide6 RAM until you need it.",
			),
		]:
			switch = ToggleSwitch(checked=bool(getattr(general_config, attr, False)), theme_name=cur_theme)
			switch._live_section = general_config  # type: ignore[attr-defined]
			switch._live_attr = attr  # type: ignore[attr-defined]
			switch.toggled.connect(lambda checked, a=attr: self._set_attr(general_config, a, checked))
			self._track_toggle(switch)
			window_card.addBodyWidget(LabeledRow(label, switch, help_text=tip))

		persist_card = FeatureCard(
			"Runtime & persistence",
			help_text=(
				'Numeric changes use the configured autosave debounce. Boolean and string controls may save immediately, and manual Save '
				'remains available; disabling autosave does not prevent every disk write.'
			),
			risk=bind_risk('persistence', lambda: self.config),
		)
		self.add_widget(persist_card)

		autosave = ToggleSwitch(checked=bool(getattr(general_config, "autosave", True)), theme_name=cur_theme)
		autosave.toggled.connect(lambda checked: self._set_attr(general_config, "autosave", checked))
		self._track_toggle(autosave)
		persist_card.addBodyWidget(
			LabeledRow("Autosave config on change", autosave, help_text='Debounces pending numeric changes; immediate-save controls and manual Save remain active.')
		)

		autosave_int = LabeledSlider(
			"Autosave interval (s)",
			lo=0.1,
			hi=10.0,
			value=float(getattr(general_config, "autosave_interval_sec", 1.0)),
			decimals=1,
			help_text="Debounce window before the file is written.",
		)
		autosave_int.valueChanged.connect(lambda v: self._set_attr(general_config, "autosave_interval_sec", float(v)))
		self._track_slider(autosave_int)
		persist_card.addBodyWidget(autosave_int)
		autosave.toggled.connect(lambda c: autosave_int.setVisible(bool(c)))
		autosave_int.setVisible(bool(getattr(general_config, "autosave", True)))

		try:
			refresh_hz_raw = float(get_display_refresh_hz())
			refresh_hz = int(round(refresh_hz_raw))
			fps_max = max(60, min(refresh_hz, 480))
		except Exception:
			refresh_hz_raw = 0.0
			fps_max = 144
		current_fps = int(getattr(general_config, "target_fps", 144))
		if current_fps > fps_max:
			current_fps = fps_max
		fps = LabeledSlider(
			f"Target FPS · detected panel: {fps_max} Hz",
			lo=30,
			hi=fps_max,
			value=current_fps,
			help_text=(
				"Caps the main loop (overlay + memory reads). "
				f"Detected panel refresh: <b>{fps_max} Hz</b>"
				+ (f" ({refresh_hz_raw:.3f} Hz precise)" if refresh_hz_raw > 0 else "")
				+ ". The slider's maximum is your display's "
				"nominal rate read at app launch via DWM "
				"composition timing — anything higher than "
				"that physically can't show, so we clamp it. A 60 "
				"Hz user sees a 60-max slider, a 144 Hz user 144, "
				"a 240 Hz user 240, a 360 Hz user 360. Re-launch "
				"the app if you switch displays mid-session so the "
				"slider re-detects."
			),
		)
		fps.valueChanged.connect(lambda v: self._set_attr(general_config, "target_fps", int(v)))
		self._track_slider(fps)
		persist_card.addBodyWidget(fps)

		overlay_config = getattr(self.config, "overlay", None)
		if overlay_config is not None:
			vsync_switch = ToggleSwitch(checked=bool(getattr(overlay_config, "vsync", False)), theme_name=cur_theme)
			vsync_switch._live_section = overlay_config  # type: ignore[attr-defined]
			vsync_switch._live_attr = "vsync"  # type: ignore[attr-defined]
			vsync_switch.toggled.connect(lambda checked: self._set_attr(overlay_config, "vsync", checked))
			self._track_toggle(vsync_switch)
			persist_card.addBodyWidget(
				LabeledRow(
					"V-Sync (vertical sync)",
					vsync_switch,
					help_text=(
						"Synchronize overlay presentation to display refresh. "
						"Disabling V-Sync can reduce presentation waiting but "
						"does not guarantee zero latency. Enable it if it "
						"improves frame pacing on your display."
					),
				)
			)

			render_card = FeatureCard(
				"Overlay renderer",
				help_text=(
					"Direct GPU selection is not supported yet: Windows chooses "
					"the hardware renderer, and the running overlay reports the "
					"actual renderer. Selecting a GPU here only chooses its "
					"background-reader profile; a different selected device's "
					"profile takes effect after restart. The CPU option requests "
					"software rendering and also requires restarting. "
					"Profiles only change the background state-reader frequency; "
					"they do not change the target FPS or disable any features."
				),
				risk=bind_risk('renderer', lambda: self.config),
			)
			render_card.setObjectName("RendererCard")
			self.add_widget(render_card)
			self._build_render_device_row(render_card, overlay_config, cur_theme)

		self._build_offsets_card()
		self._build_health_card()

		diag_card = FeatureCard(
			"Resolved configuration",
			help_text=(
				"Read-only dump of every config field the app has "
				"loaded. Useful when reporting bugs — copy this whole "
				"block into the issue. Refreshes live as you change "
				"values; click <b>Refresh</b> if a write happened "
				"outside the menu (e.g. you edited the TOML by hand)."
			),
			risk=bind_risk('persistence', lambda: self.config),
		)
		self.add_widget(diag_card)

		refresh_btn = QPushButton("Refresh", diag_card)
		refresh_btn.setObjectName("DiagRefreshBtn")
		refresh_btn.setFocusPolicy(Qt.NoFocus)
		refresh_btn.setCursor(Qt.PointingHandCursor)
		refresh_btn.setToolTip("Re-read every config section and repaint the dump.")
		diag_card.addHeaderWidget(refresh_btn)

		diag_lbl = QLabel("", diag_card)
		diag_lbl.setProperty("muted", "true")
		diag_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
		diag_lbl.setWordWrap(True)
		diag_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
		diag_lbl.setObjectName("DiagReadout")
		diag_card.addBodyWidget(diag_lbl)
		self._diag_lbl = diag_lbl

		refresh_btn.clicked.connect(self._refresh_diagnostics)
		self._refresh_diagnostics()

		self.end()

	def _build_offsets_card(self) -> None:
		'''Offsets auto-update card.'''
		offsets_card = FeatureCard(
			"Offsets",
			help_text=(
				"Update game offsets from <b>a2x/cs2-dumper</b> on GitHub. "
				"CS2 patches shift memory layouts every ~2 weeks; after a "
				"game update, click <b>Update Offsets</b> to pull the latest "
				"values. The app auto-updates on startup, but this button "
				"lets you refresh mid-session without restarting.<br><br>"
				"The update runs three stages:<br>"
				"&nbsp;&nbsp;1. <b>Remote fetch</b> from a2x/cs2-dumper GitHub<br>"
				"&nbsp;&nbsp;2. <b>Local sigscan</b> on client.dll (if CS2 is running)<br>"
				"&nbsp;&nbsp;3. <b>Live verify</b> of netvars from memory<br><br>"
				"All three stages are automatic — you just click the button."
			),
			risk=bind_risk('offsets', lambda: self.config),
		)
		self.add_widget(offsets_card)

		offsets_btn = QPushButton("Update Offsets", offsets_card)
		offsets_btn.setObjectName("OffsetsUpdateBtn")
		offsets_btn.setMinimumSize(280, 44)
		offsets_btn.setMaximumWidth(420)
		offsets_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
		offsets_btn.setFocusPolicy(Qt.NoFocus)
		offsets_btn.setCursor(Qt.PointingHandCursor)
		offsets_btn.setToolTip("Fetch latest offsets from GitHub, run sigscan, verify netvars")
		self._offsets_btn = offsets_btn

		self._offsets_status_lbl = QLabel("<i>Click <b>Update Offsets</b> to refresh from a2x/cs2-dumper.</i>", offsets_card)
		self._offsets_status_lbl.setObjectName("OffsetsStatus")
		self._offsets_status_lbl.setTextFormat(Qt.RichText)
		self._offsets_status_lbl.setWordWrap(True)
		offsets_card.addBodyWidget(self._offsets_status_lbl)
		offsets_actions = QWidget(offsets_card)
		offsets_actions.setObjectName("OffsetsActions")
		actions_layout = QHBoxLayout(offsets_actions)
		actions_layout.setContentsMargins(0, 8, 0, 0)
		actions_layout.addStretch(1)
		actions_layout.addWidget(offsets_btn, 2)
		actions_layout.addStretch(1)
		offsets_card.addBodyWidget(offsets_actions)

		offsets_btn.clicked.connect(self._run_offset_update)

	def _build_health_card(self) -> None:
		'''Health & verification card.'''
		health_card = FeatureCard(
			"Health & verification",
			help_text=(
				"Attaches to <code>cs2.exe</code>, re-runs the live "
				"sigscan on <code>client.dll</code>, then probes each "
				"feature's memory dependencies. Click <b>Run "
				"diagnostics</b> to refresh — every row gets a colour-"
				"coded verdict:<br>"
				"&nbsp;&nbsp;<b style='color:#4ade80;'>PASS</b> &mdash; "
				"feature's preconditions are met<br>"
				"&nbsp;&nbsp;<b style='color:#fbbf24;'>WARN</b> &mdash; "
				"works, but a non-fatal issue was found<br>"
				"&nbsp;&nbsp;<b style='color:#f87171;'>FAIL</b> &mdash; "
				"feature will NOT function as configured<br>"
				"&nbsp;&nbsp;<b style='color:#94a3b8;'>SKIP</b> &mdash; "
				"disabled in config or no live data yet<br><br>"
				"Run this <b>before</b> a match, not during &mdash; "
				"the probe takes ~200&nbsp;ms and reads the entity "
				"list."
			),
			risk=bind_risk('diagnostics', lambda: self.config),
		)
		self.add_widget(health_card)

		health_btn = QPushButton("Run diagnostics", health_card)
		health_btn.setObjectName("DiagRefreshBtn")
		health_btn.setFocusPolicy(Qt.NoFocus)
		health_btn.setCursor(Qt.PointingHandCursor)
		health_btn.setToolTip("Attach to cs2.exe and run the full live-verification probe.")
		health_card.addHeaderWidget(health_btn)
		self._health_btn = health_btn

		self._health_summary_lbl = QLabel("<i>Click <b>Run diagnostics</b> to test the cheat against the currently running CS2.</i>", health_card)
		self._health_summary_lbl.setObjectName("HealthSummary")
		self._health_summary_lbl.setTextFormat(Qt.RichText)
		self._health_summary_lbl.setWordWrap(True)
		health_card.addBodyWidget(self._health_summary_lbl)

		self._health_rows_lbl = QLabel("", health_card)
		self._health_rows_lbl.setObjectName("HealthRows")
		self._health_rows_lbl.setTextFormat(Qt.RichText)
		self._health_rows_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
		self._health_rows_lbl.setWordWrap(True)
		self._health_rows_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
		health_card.addBodyWidget(self._health_rows_lbl)

		health_btn.clicked.connect(self._run_health_check)

		self.set_cs2_attached(False)

	def _build_render_device_row(self, card, overlay_config, theme_name) -> None:
		self._render_overlay_config = overlay_config
		try:
			from cs2esp.gpu_devices import list_render_devices

			devices = list_render_devices()
		except Exception as exc:
			print(f"[settings] render-device list failed: {exc!r}")
			return
		self._render_device_kinds = {device.key: device.kind for device in devices}
		self._render_device_names = {device.key: getattr(device, "name", "") or device.label for device in devices}
		options = [(device.key, device.label) for device in devices]
		current = str(getattr(overlay_config, "render_device", "auto") or "auto")
		if current not in {key for key, _ in options}:
			options.append((current, f"{current} (not detected)"))
		self._render_device_row = ComboRow("", overlay_config, "render_device", options, default="auto", theme_name=theme_name)
		self._render_device_row.setToolTip(
			"Windows chooses the hardware renderer. Direct GPU selection is not "
			"supported yet; a GPU selection stores its reader profile. CPU requests "
			"the Mesa software renderer after restart. The actual renderer is reported below."
		)
		self._render_device_row.changed.connect(self._on_render_device_changed)
		device_field = self._renderer_field("Device preference", self._render_device_row, "GPU chosen by Windows", card)
		card.addBodyWidget(device_field)
		self._render_device_note = QLabel("Hardware selection is advisory. The actual GPU is shown below.", card)
		self._render_device_note.setObjectName("RendererFieldNote")
		self._render_device_note.setWordWrap(True)
		card.addBodyWidget(self._render_device_note)

		stored = self._render_profile_map()
		default_profile = self._default_render_profile(current)
		current_profile = stored.get(current, getattr(overlay_config, "render_profile", default_profile))
		if current_profile not in _PROFILE_DESCRIPTIONS:
			current_profile = default_profile
		self._render_profile_view = SimpleNamespace(value=current_profile)
		self._render_profile_row = ComboRow("", self._render_profile_view, "value", _RENDER_PROFILES, default=default_profile, theme_name=theme_name)
		self._render_profile_row.setToolTip(
			"Background-reader frequency for this device. The running device's "
			"profile applies live; another device's profile applies after restart. "
			"The FPS limit and all visual features remain unchanged."
		)
		self._render_profile_row.changed.connect(self._on_render_profile_changed)
		profile_field = self._renderer_field("Device profile", self._render_profile_row, "", card)
		self._render_profile_default = profile_field.findChild(QLabel, "RendererFieldHint")
		card.addBodyWidget(profile_field)
		self._render_profile_note = QLabel("", card)
		self._render_profile_note.setObjectName("RendererFieldNote")
		self._render_profile_note.setWordWrap(True)
		card.addBodyWidget(self._render_profile_note)
		self._update_render_profile_note()

		active = QFrame(card)
		active.setObjectName("RendererActivePanel")
		layout = QVBoxLayout(active)
		layout.setContentsMargins(16, 14, 16, 14)
		layout.setSpacing(7)
		heading = QLabel("ACTIVE RENDERER", active)
		heading.setObjectName("RendererEyebrow")
		layout.addWidget(heading)
		active_row = QWidget(active)
		active_layout = QHBoxLayout(active_row)
		active_layout.setContentsMargins(0, 0, 0, 0)
		active_layout.setSpacing(12)
		copy = QWidget(active_row)
		copy_layout = QVBoxLayout(copy)
		copy_layout.setContentsMargins(0, 0, 0, 0)
		copy_layout.setSpacing(3)
		self._renderer_note = QLabel("Waiting for overlay…", copy)
		self._renderer_note.setObjectName("RendererActiveName")
		self._renderer_note.setTextFormat(Qt.PlainText)
		self._renderer_note.setWordWrap(True)
		self._renderer_note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
		copy_layout.addWidget(self._renderer_note)
		self._renderer_detail = QLabel("No live renderer status yet.", copy)
		self._renderer_detail.setObjectName("RendererFieldNote")
		self._renderer_detail.setWordWrap(True)
		copy_layout.addWidget(self._renderer_detail)
		active_layout.addWidget(copy, 1)
		self._renderer_status = QLabel("Waiting", active_row)
		self._renderer_status.setObjectName("RendererStatus")
		self._renderer_status.setProperty("tone", "muted")
		active_layout.addWidget(self._renderer_status, 0, Qt.AlignTop)
		layout.addWidget(active_row)
		self._renderer_pending = QLabel("", active)
		self._renderer_pending.setObjectName("RendererPending")
		self._renderer_pending.setTextFormat(Qt.PlainText)
		self._renderer_pending.setWordWrap(True)
		self._renderer_pending.hide()
		layout.addWidget(self._renderer_pending)
		card.addBodyWidget(active)
		self._renderer_active_requested = None
		self._renderer_initial_requested = current

	@staticmethod
	def _renderer_field(title, row, hint, parent):
		field = QWidget(parent)
		field.setObjectName("RendererField")
		layout = QVBoxLayout(field)
		layout.setContentsMargins(0, 8, 0, 0)
		layout.setSpacing(5)
		heading = QWidget(field)
		heading_layout = QHBoxLayout(heading)
		heading_layout.setContentsMargins(0, 0, 0, 0)
		label = QLabel(title, heading)
		label.setObjectName("RendererFieldLabel")
		if "&" not in title:
			label.setBuddy(row._combo)
		row._combo.setAccessibleName(title)
		heading_layout.addWidget(label)
		hint_label = QLabel(hint, heading)
		hint_label.setObjectName("RendererFieldHint")
		hint_label.setWordWrap(True)
		hint_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
		hint_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
		heading_layout.addWidget(hint_label, 1)
		layout.addWidget(heading)
		row.layout().takeAt(row.layout().count() - 1)
		row.layout().setStretch(0, 1)
		row._combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
		row._combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
		row._combo.setMinimumContentsLength(8)
		row._combo.setMinimumWidth(0)
		layout.addWidget(row)
		return field

	def _render_profile_map(self) -> dict:
		profiles = getattr(self._render_overlay_config, "render_profiles", {})
		if hasattr(profiles, "_data"):
			profiles = profiles._data
		return dict(profiles) if isinstance(profiles, dict) else {}

	def sync_renderer_settings(self) -> None:
		'''Reflect a config reload without writing it back from widget signals.'''
		row = getattr(self, "_render_device_row", None)
		if row is None:
			return
		key = str(getattr(self._render_overlay_config, "render_device", "auto") or "auto")
		if row._combo.findData(key) < 0:
			row._options.append((key, f"{key} (not detected)"))
			row._combo.addItem(f"{key} (not detected)", key)
		with QSignalBlocker(row._combo):
			row.setValue(key)
		default = self._default_render_profile(key)
		profile = self._render_profile_map().get(key, getattr(self._render_overlay_config, "render_profile", default))
		if profile not in _PROFILE_DESCRIPTIONS:
			profile = default
		self._render_profile_view.value = profile
		with QSignalBlocker(self._render_profile_row._combo):
			self._render_profile_row.setValue(profile)
		self._update_render_profile_note()
		self._set_renderer_note()

	def _default_render_profile(self, device_key: str) -> str:
		kind = self._render_device_kinds.get(device_key)
		return "energy_saving" if device_key == "cpu" or kind == "cpu" else "efficient"

	def _update_render_profile_note(self) -> None:
		profile = self._render_profile_view.value
		self._render_profile_note.setText(f"{_PROFILE_DESCRIPTIONS.get(profile, _PROFILE_DESCRIPTIONS['efficient'])} · FPS cap and visual features unchanged.")
		self._render_profile_default.setText("Saved per device")

	def _on_render_device_changed(self, key: str) -> None:
		profile = self._render_profile_map().get(key, self._default_render_profile(key))
		if profile not in _PROFILE_DESCRIPTIONS:
			profile = self._default_render_profile(key)
		self._render_profile_view.value = profile
		with QSignalBlocker(self._render_profile_row._combo):
			self._render_profile_row.setValue(profile)
		self._set_attr(self._render_overlay_config, "render_profile", profile)
		self._update_render_profile_note()
		self._set_renderer_note()

	def _on_render_profile_changed(self, profile: str) -> None:
		key = self._render_device_row.value()
		existing = getattr(self._render_overlay_config, "render_profiles", None)
		if hasattr(existing, "_data"):
			setattr(existing, key, profile)
		else:
			profiles = self._render_profile_map()
			profiles[key] = profile
			setattr(self._render_overlay_config, "render_profiles", profiles)
			# Dict mutations are debounced by Config; persist immediately so the other process picks up this profile too
			root = getattr(self._render_overlay_config, "_root", None)
			if root is not None:
				root.save()
		self._set_attr(self._render_overlay_config, "render_profile", profile)
		self._update_render_profile_note()

	def _set_renderer_note(self) -> None:
		note = getattr(self, "_renderer_note", None)
		if note is None:
			return
		active_name = getattr(self, "_renderer_active", "")
		pending = ""
		tone, status = "muted", "Waiting"
		detail = "No live renderer status yet."
		if active_name:
			tone, status = "good", "Active"
			selected_key = self._render_device_row.value()
			applied_key = self._renderer_active_requested if self._renderer_active_requested is not None else self._renderer_initial_requested
			software = any(word in active_name.casefold() for word in ("llvmpipe", "softpipe", "software", "gdi generic"))
			detail = "OpenGL · software rendering" if software else "OpenGL · Windows-selected GPU"
			if selected_key != applied_key:
				tone = "warning"
				if selected_key.startswith("gpu:"):
					status = "Profile pending"
					pending = "The selected device's profile applies after restart. Windows still chooses the GPU."
				else:
					status = "Restart required"
					pending = "Restart the overlay to apply the selected rendering mode."
			elif selected_key.startswith("gpu:"):
				selected_name = self._render_device_names.get(selected_key, "")
				normalise = lambda value: "".join(character for character in value.casefold().replace("(tm)", "").replace("(r)", "") if character.isalnum())
				actual, preferred = normalise(active_name), normalise(selected_name)
				if preferred and preferred not in actual and actual not in preferred:
					tone, status = "warning", "Different GPU"
					pending = "The preferred GPU is not active. Windows chose the renderer shown above."
			elif selected_key == "cpu" and not software:
				tone, status = "warning", "Hardware active"
				pending = "Software rendering was requested, but the overlay reports a hardware renderer."
			warning = getattr(self, "_renderer_warning", None)
			if warning:
				advisory = "direct GPU selection is not supported yet" in str(warning) and "Windows chooses the renderer" in str(warning)
				if not advisory:
					tone, status = "warning", "Check selection"
					pending = f"{pending}\n{warning}" if pending else str(warning)
			self._renderer_pending.setToolTip(str(warning or ""))
		else:
			self._renderer_pending.setToolTip("")
		note.setText(active_name or "Waiting for overlay…")
		self._renderer_detail.setText(detail)
		self._renderer_status.setText(status)
		self._renderer_status.setProperty("tone", tone)
		self._renderer_status.style().unpolish(self._renderer_status)
		self._renderer_status.style().polish(self._renderer_status)
		self._renderer_pending.setText(pending)
		self._renderer_pending.setVisible(bool(pending))

	def set_renderer(self, renderer: str | None, *, requested_device: str | None = None, warning: str | None = None) -> None:
		self._renderer_active = renderer or None
		self._renderer_warning = warning if renderer else None
		if requested_device is not None:
			self._renderer_active_requested = str(requested_device)
		elif not renderer:
			self._renderer_active_requested = None
		self._set_renderer_note()

	def set_cs2_attached(self, attached: bool) -> None:
		'''Toggle the Health-card run button when CS2 attach state flips.'''
		button = getattr(self, "_health_btn", None)
		if button is None:
			return
		self._health_cs2_attached = bool(attached)
		button.setEnabled(bool(attached) and not getattr(self, "_offset_update_busy", False) and not getattr(self, "_health_check_busy", False))
		if attached:
			button.setText("Run diagnostics")
			button.setToolTip("Attach to cs2.exe and run the full live-verification probe.")
		else:
			button.setText("Launch CS2 first")
			button.setToolTip("Disabled because the runtime hasn't detected cs2.exe yet — launch the game first.")

	def _set_attr(self, section, attr: str, value) -> None:
		'''Write the new value through and let Config autosave.'''
		try:
			setattr(section, attr, value)
		except Exception as e:
			print(f"[settings] set {attr}={value!r} failed: {e!r}")

	def _track_toggle(self, switch: ToggleSwitch) -> None:
		'''Remember every ToggleSwitch so theme-change can re-skin them.'''
		self._toggles.append(switch)

	def _track_slider(self, slider: LabeledSlider) -> None:
		'''Remember every LabeledSlider so theme changes re-tint arrows.'''
		self._sliders.append(slider)

	def _apply_theme_to_controls(self, name: str) -> None:
		'''Re-skin every tracked switch, slider and renderer combo for the selected theme.'''
		for switch in self._toggles:
			try:
				switch.apply_theme(name)
			except Exception:
				pass
		for slider in self._sliders:
			try:
				slider.apply_theme(name)
			except Exception:
				pass
		for attr in ("_render_device_row", "_render_profile_row"):
			combo = getattr(self, attr, None)
			if combo is not None:
				combo.apply_theme(name)

	def _on_theme_pick(self, name: str, checked: bool) -> None:
		if not checked:
			return
		self._set_attr(self.config.general, "ui_theme", name)
		self._apply_theme_to_controls(name)
		if callable(self._on_theme_change):
			self._on_theme_change(name)

	def _on_scale_changed(self, value: float) -> None:
		'''Persist UI-scale + ask the window to rebuild QSS at the new scale.'''
		self._set_attr(self.config.general, "ui_scale", float(value))
		win = self.window()
		if win is not None and hasattr(win, "apply_theme"):
			try:
				win.apply_theme(str(getattr(self.config.general, "ui_theme", "midnight")), float(value))
			except Exception:
				pass

	def _refresh_diagnostics(self) -> None:
		'''Walk every Config section and repaint the readout label.'''
		if not hasattr(self, "_diag_lbl"):
			return

		config = self.config
		config_data = getattr(config, "_data", {}) or {}

		lines: list[str] = []
		top_scalars = [field_name for field_name, field_value in config_data.items() if not isinstance(field_value, dict)]
		if top_scalars:
			lines.append("[root]")
			for field_name in sorted(top_scalars):
				lines.append(self._format_diag_pair(field_name, config_data[field_name]))
			lines.append("")

		section_names = sorted(field_name for field_name, field_value in config_data.items() if isinstance(field_value, dict))
		for section_name in section_names:
			section_data = config_data[section_name] or {}
			lines.append(f"[{section_name}]")
			scalars = [(field_name, field_value) for field_name, field_value in section_data.items() if not isinstance(field_value, dict)]
			for field_name, field_value in sorted(scalars, key=lambda kv: kv[0]):
				lines.append(self._format_diag_pair(field_name, field_value))
			for sub_name in sorted(field_name for field_name, field_value in section_data.items() if isinstance(field_value, dict)):
				lines.append(f" [{section_name}.{sub_name}]")
				nested_data = section_data[sub_name] or {}
				for field_name, field_value in sorted(nested_data.items()):
					if isinstance(field_value, dict):
						lines.append(f" {field_name:<26} = <dict, len={len(field_value)}>")
					else:
						lines.append(self._format_diag_pair(field_name, field_value))
			lines.append("")

		text = "\n".join(lines).rstrip()
		self._diag_lbl.setText(text)

	@staticmethod
	def _format_diag_pair(name: str, value: object) -> str:
		'''One-line key = value formatter.'''
		if isinstance(value, (str, int, float, bool)) or value is None:
			return f" {name:<28} = {value!r}"
		if isinstance(value, (list, tuple)):
			return f" {name:<28} = <{type(value).__name__}, len={len(value)}>"

		if isinstance(value, dict):
			return f" {name:<28} = <dict, len={len(value)}>"
		return f" {name:<28} = <{type(value).__name__}>"

	def _run_health_check(self) -> None:
		'''Attach to cs2.exe, run the diagnostic, render the report.'''
		if getattr(self, "_offset_update_busy", False) or getattr(self, "_health_check_busy", False):
			return
		self._health_check_busy = True
		self._offsets_btn.setEnabled(False)
		button = self.sender()
		if button is not None:
			button.setEnabled(False)
			button.setText("Probing…")
			QApplication.processEvents()

		try:
			report = run_full_health_check(self.config)
		except Exception as e:
			self._health_summary_lbl.setText(f"<b style='color:#f87171;'>FAIL</b> &mdash; probe raised: <code>{e!r}</code>")
			self._health_rows_lbl.setText("")
			self._restore_health_button(button)
			return

		tally = report.summary()
		timestamp = strftime("%H:%M:%S", localtime(report.timestamp))
		attach_blurb = f"PID {report.cs2_pid}" if report.attached else "<span style='color:#f87171;'>not attached</span>"
		summary_html = (
			f"<b>{timestamp}</b> &middot; cs2.exe {attach_blurb} &middot; "
			f"<b style='color:#4ade80;'>{tally['PASS']} PASS</b> &middot; "
			f"<b style='color:#fbbf24;'>{tally['WARN']} WARN</b> &middot; "
			f"<b style='color:#f87171;'>{tally['FAIL']} FAIL</b> &middot; "
			f"<b style='color:#94a3b8;'>{tally['SKIP']} SKIP</b>"
		)
		self._health_summary_lbl.setText(summary_html)

		rank = {"FAIL": 0, "WARN": 1, "PASS": 2, "SKIP": 3}
		colour = {"PASS": "#4ade80", "WARN": "#fbbf24", "FAIL": "#f87171", "SKIP": "#94a3b8"}
		lines: list[str] = []
		for category in report.categories():
			category_rows = [health_row for health_row in report.rows if health_row.category == category]
			category_rows.sort(key=lambda r: rank.get(r.status.value, 9))
			lines.append(f"<b style='color:#e2e8f0;'>[ {category} ]</b>")
			for health_row in category_rows:
				badge = f"<b style='color:{colour[health_row.status.value]};'>{health_row.status.value:<4}</b>"
				name = health_row.name
				if len(name) > 38:
					name = name[:35] + "..."
				name_padded = name + ("&nbsp;" * (40 - len(name)))
				reason = (health_row.reason or "").replace("<", "&lt;").replace(">", "&gt;")
				if len(reason) > 110:
					reason = reason[:107] + "..."
				lines.append(f"&nbsp;&nbsp;{badge}&nbsp;{name_padded}{reason}")
			lines.append("")
		self._health_rows_lbl.setText("<br>".join(lines).rstrip())
		self._restore_health_button(button)

	def _restore_health_button(self, button) -> None:
		'''Re-enable the Health-card action after a diagnostic run.'''
		if button is not None:
			button.setEnabled(True)
			button.setText("Run diagnostics")
		self._health_check_busy = False
		self._offsets_btn.setEnabled(not getattr(self, "_offset_update_busy", False))

	def _run_offset_update(self) -> None:
		'''Run network and live verification off the GUI thread; correlate runtime apply separately.'''
		if getattr(self, "_offset_update_busy", False) or getattr(self, "_health_check_busy", False):
			return
		self._offset_update_busy = True
		self._health_btn.setEnabled(False)
		self._offsets_btn.setEnabled(False)
		self._offsets_btn.setText("Updating…")
		self._offsets_status_lbl.setText("Downloading offsets and checking the live process…")
		worker = _OffsetRefreshWorker()
		self._offset_worker = worker
		_OFFSET_WORKERS.add(worker)
		worker.reportReady.connect(self._on_offset_report, Qt.QueuedConnection)
		worker.finished.connect(lambda task=worker: _OFFSET_WORKERS.discard(task))
		worker.finished.connect(worker.deleteLater)
		worker.start()

	def _on_offset_report(self, result: dict) -> None:
		error = result.get("error")
		if error:
			self._offsets_status_lbl.setText(f"<b>Update failed</b><br>{escape(str(error))}")
			self._finish_offset_update()
			return
		report = result["report"]
		self._offset_report = report
		self._offset_report_lines = self._offset_report_details(report)
		if not report.get("cache_saved", False):
			self._show_offset_result("Update not saved", "The offset cache was not written.", "warn")
			self._finish_offset_update()
			return
		self._offset_expected_hash = report.get("cache_sha256")
		try:
			from cs2esp.ipc import read_instance_pid, request_offset_refresh

			self._offset_expected_pid = read_instance_pid()
			request_ts = request_offset_refresh(cache_sha256=self._offset_expected_hash)
			self._offset_request_ts = float(request_ts or 0.0)
		except Exception as exc:
			self._offset_request_ts = 0.0
			error = str(exc)
		if not self._offset_request_ts:
			message = "Runtime request was not delivered; the saved cache is ready for the next launch."
			if error:
				message += f" {error}"
			self._show_offset_result("Cache saved", message, "warn")
			self._finish_offset_update()
			return
		self._show_offset_result("Cache saved", "Waiting for the running overlay to confirm application…", "warn")
		self._offset_ack_deadline = monotonic() + 10.0
		self._offset_ack_error = None
		timer = getattr(self, "_offset_ack_timer", None)
		if timer is None:
			timer = QTimer(self)
			timer.setInterval(50)
			timer.timeout.connect(self._poll_offset_ack)
			self._offset_ack_timer = timer
		timer.start()

	@staticmethod
	def _offset_report_details(report: dict) -> list[str]:
		source = report.get("source", "none")
		source_text = {"remote": "Remote download", "cache": "Existing local cache", "none": "No valid source"}.get(source, str(source))
		lines = [f"Source: {escape(source_text)} · cache: {'saved' if report.get('cache_saved') else 'not saved'}"]
		attached = report.get("attached", False)
		if attached:
			lines.append(
				f"Live check: sigscan {int(report.get('sigscan_count', 0))} · "
				f"netvars corrected {int(report.get('netvars_corrected', 0))} · "
				f"changed fields {int(report.get('changed', 0))}"
			)
		else:
			lines.append("Live check: no game process attached")
		if not report.get("remote_complete", False):
			lines.append("The remote download was incomplete; this is not a confirmed remote refresh.")
		messages = [str(value) for value in report.get("warnings", []) if value]
		if report.get("error"):
			messages.append(str(report["error"]))
		lines.extend(escape(message) for message in dict.fromkeys(messages))
		return lines

	def _show_offset_result(self, heading: str, runtime: str, tone: str) -> None:
		from cs2esp.ui.qt.theme import PALETTES

		theme = str(getattr(self.config.general, "ui_theme", "slate"))
		palette = PALETTES.get(theme, PALETTES["slate"])
		colour = palette["safe" if tone == "good" else "warn"]
		lines = [f"<b style='color:{colour};'>{escape(heading)}</b>", *self._offset_report_lines, f"Runtime: {escape(runtime)}"]
		self._offsets_status_lbl.setText("<br>".join(lines))

	def _poll_offset_ack(self) -> None:
		try:
			from cs2esp.ipc import read_offset_ack

			ack = read_offset_ack()
		except Exception as exc:
			ack = None
			self._offset_ack_error = str(exc)
		try:
			matching = bool(ack and abs(float(ack.get("request_ts", 0.0)) - self._offset_request_ts) <= 1e-6)
		except (TypeError, ValueError):
			matching = False
		if matching:
			expected = self._offset_expected_hash
			same_cache = bool(expected and ack.get("applied_cache_sha256") == expected)
			same_runtime = bool(self._offset_expected_pid > 0 and ack.get("pid") == self._offset_expected_pid)
			confirmed = ack.get("success") is True and ack.get("cache_loaded") is True and ack.get("cache_saved") is True and same_cache and same_runtime
			if confirmed:
				live_verified = ack.get("attached") is True and ack.get("live_verified") is True
				complete = (
					self._offset_report.get("success") is True
					and self._offset_report.get("remote_complete") is True
					and self._offset_report.get("cache_saved") is True
					and live_verified
				)
				self._show_offset_result(
					"Updated" if complete else "Cached offsets applied" if live_verified else "Cache loaded",
					("Applied and verified by the running overlay." if live_verified else "Loaded by overlay; live verification unavailable."),
					"good" if complete else "warn",
				)
			else:
				error = ack.get("error")
				message = str(error) if error else "Reply received, but cache identity and successful application were not confirmed."
				self._show_offset_result("Runtime application unconfirmed", message, "warn")
			self._finish_offset_update()
		elif monotonic() >= self._offset_ack_deadline:
			message = "Confirmation timed out; the saved cache is ready for the next launch."
			if self._offset_ack_error:
				message += f" {self._offset_ack_error}"
			self._show_offset_result("Cache saved", message, "warn")
			self._finish_offset_update()

	def _finish_offset_update(self) -> None:
		timer = getattr(self, "_offset_ack_timer", None)
		if timer is not None:
			timer.stop()
		self._offset_update_busy = False
		self._offsets_btn.setEnabled(True)
		self._offsets_btn.setText("Update Offsets")
		self.set_cs2_attached(getattr(self, "_health_cs2_attached", False))

	def sync_theme_radios(self, name: str) -> None:
		'''Sync the picker after the title-bar theme pill changes theme.'''
		for button in self._theme_group.buttons():
			text = button.text().lower()
			should_check = text.startswith(name.lower())
			button.blockSignals(True)
			try:
				button.setChecked(should_check)
			finally:
				button.blockSignals(False)
		self._apply_theme_to_controls(name)
