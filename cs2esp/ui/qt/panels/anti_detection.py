"""
Existing overlay, read-loop and scheduling controls with their audited capability limits.
"""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QWidget

from cs2esp.risk import bind_risk
from cs2esp.ui.qt.panels.base import PanelBase
from cs2esp.ui.qt.widgets import FeatureCard, LabeledRow, LabeledSlider, ToggleSwitch
from cs2esp.ui.qt.widgets.choice import _ChevronCombo


class AntiDetectionPanel(PanelBase):
	"""Expose existing settings with their documented technical and policy limits."""
	RESET_SCOPES = {
		"Read cadence": ("anti_detection.randomize_read_jitter_ms",),
		"Module display-path alias": ("anti_detection.module_spoof_masquerade_name",),
		"Read-pattern obfuscation": ("anti_detection.read_obfuscator_bogus_per_real",),
		"Window-class masquerade": ("anti_detection.window_class_masquerade_target",),
	}

	def __init__(self, config, parent: QWidget = None) -> None:
		super().__init__(config, parent)
		anti_detection = self.config.anti_detection

		self.h1("Anti-detection")
		self.intro(
			"Configure capture visibility, window identity, read timing and write/input scheduling. "
			"Startup-only controls require restarting the relevant process."
		)

		self.h2("Runtime controls")
		overlay_card = self.add_widget(FeatureCard(
			"Overlay visibility",
			help_text=(
				"Changes the app's own overlay capture affinity, taskbar presentation and startup title. "
				"Capture exclusion covers only supported OS capture paths; window presentation does not hide the process. "
				"These controls do not establish anti-cheat approval or a detection guarantee."
			),
			risk=bind_risk("capture_exclusion", self.config),
		))
		capture_switch = None
		auto_capture_row = None
		for label, attribute, help_text, capability_id in [
			("Hide overlay from screen capture", "hide_overlay_from_capture",
				"<b>Live.</b> Requests <code>SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)</code> on the app's own overlay window. "
				"The request may fail and depends on supported Windows/DWM behavior. It covers a specific set of public OS capture APIs, "
				"not every capture path, security boundary or anti-cheat snapshot. No detection outcome is established.", "capture_exclusion"),
			("Hide from taskbar", "hide_from_taskbar",
				"<b>Live.</b> Changes <code>WS_EX_TOOLWINDOW</code> and the overlay's taskbar/Alt-Tab presentation without stopping rendering. "
				"It does not hide the process or prevent inspection.", "taskbar_visibility"),
			("Randomise window title each launch", "randomize_window_title",
				"<b>Restart required.</b> Selects a title from the existing pool at startup. When OFF, the overlay uses "
				"<code>\"CS2 Master Overlay\"</code>. Changing the setting does not rename the current window or hide executable/module identity. "
				"No detector benefit is established.", "window_title"),
			("Reapply capture exclusion each second", "auto_capture_protect_on_focus_loss",
				"<b>Live.</b> Reapplies capture affinity roughly once per second while capture exclusion and this option are enabled. "
				"Despite the stored setting name, this loop does not check focus loss. Reapplying does not extend the API's coverage "
				"or establish a detection guarantee.", "capture_reapply"),
		]:
			switch = ToggleSwitch()
			switch.setChecked(bool(getattr(anti_detection, attribute)))
			switch.toggled.connect(lambda checked, setting=attribute: setattr(anti_detection, setting, bool(checked)))
			row = LabeledRow(label, switch, help_text=help_text, risk=bind_risk(capability_id, self.config))
			overlay_card.addBodyWidget(row)
			if attribute == "hide_overlay_from_capture":
				capture_switch = switch
			elif attribute == "auto_capture_protect_on_focus_loss":
				auto_capture_row = row
		if capture_switch is not None and auto_capture_row is not None:
			capture_switch.toggled.connect(lambda checked: auto_capture_row.setVisible(bool(checked)))
			auto_capture_row.setVisible(bool(getattr(anti_detection, "hide_overlay_from_capture", False)))

		read_card = self.add_widget(FeatureCard(
			"Read cadence",
			help_text=(
				"<b>Live.</b> Adds a configured random sleep to the normal render/read-loop tail. "
				"It does not wrap each <code>ReadProcessMemory</code> call, and background-reader cadence can differ. "
				"Timing changes leave the target addresses and gameplay capabilities unchanged; no detector benefit is established."
			),
			risk=bind_risk("read_jitter", self.config),
		))
		read_jitter_slider = LabeledSlider(
			"Read jitter (ms)", lo=0, hi=10, value=int(getattr(anti_detection, "randomize_read_jitter_ms", 2)), decimals=0,
			help_text="<b>Live.</b> Sets the normal loop's additional random sleep range, not a delay on every individual memory read.",
			risk=bind_risk("read_jitter", self.config),
		)
		read_jitter_slider.valueChanged.connect(lambda value: setattr(anti_detection, "randomize_read_jitter_ms", int(value)))
		read_card.addBodyWidget(read_jitter_slider)

		write_card = self.add_widget(FeatureCard(
			"Write guard",
			help_text=(
				"<b>Live.</b> Stops normal Aimbot/Glow updates and automated firing, clicking, jumping and recoil input. "
				"Entering the guard releases input still held by this app. State cleanup and already in-flight writes can finish; "
				"external reads and ESP continue. This does not change process-handle permissions."
			),
			risk=bind_risk("write_guard", self.config),
		))
		read_only_switch = ToggleSwitch()
		read_only_switch.setChecked(bool(getattr(anti_detection, "read_only_mode", False)))
		read_only_switch.toggled.connect(lambda checked: setattr(anti_detection, "read_only_mode", bool(checked)))
		write_card.addBodyWidget(LabeledRow(
			"Read-only scheduling guard", read_only_switch,
			help_text=(
				"<b>Live.</b> Blocks normal write/input scheduling, including recoil control, without disabling information displays. "
				"Owned cleanup, in-flight writes and separately executed macros are not a global read-only guarantee."
			),
			risk=bind_risk("write_guard", self.config),
		))

		self.h2("Process identity")
		self.section_intro(
			"These controls change this application's own window or loader metadata at startup. "
			"A requested setting is distinct from a successfully applied change."
		)

		module_card = self.add_widget(FeatureCard(
			"Module display-path alias",
			help_text=(
				"<b>Overlay restart required.</b> Changes the running interpreter's loader display path and verifies the complete value. "
				"The actual loader basename remains unchanged so later Python extensions can load normally. No DLL is copied or loaded. "
				"The mapped image, file on disk and other loader indexes keep their real identity."
			),
			risk=bind_risk("module_spoof", self.config),
			toggle_target=(anti_detection, "module_spoof_enabled"),
		))
		unlink_switch = ToggleSwitch()
		unlink_switch.setChecked(bool(getattr(anti_detection, "module_spoof_unlink_from_peb", True)))
		unlink_switch.toggled.connect(lambda checked: setattr(anti_detection, "module_spoof_unlink_from_peb", bool(checked)))
		module_card.addBodyWidget(LabeledRow(
			"Unlink from PEB load-order list", unlink_switch,
			help_text=(
				"<b>Overlay restart required.</b> Optionally detaches the interpreter entry from the load-order list after checking its links. "
				"OFF changes the display path without detaching the entry. Other lists and image identity stay intact; exit cleanup restores this bookkeeping."
			),
			risk=bind_risk("module_unlink", self.config),
		))

		obfuscator_card = self.add_widget(FeatureCard(
			"Read-pattern obfuscation",
			help_text=(
				"<b>Overlay restart required.</b> Adds N extra native read attempts before each typed or block read made through the overlay's MemoryReader. "
				"The real read keeps its own address, destination and result. Extra attempts can fail and add CPU cost. "
				"This is a per-reader wrapper, not a global hook or a verified reduction in detection risk."
			),
			risk=bind_risk("read_obfuscator", self.config),
			toggle_target=(anti_detection, "read_obfuscator_enabled"),
		))
		bogus_reads_slider = LabeledSlider(
			"Extra reads per wrapped read", lo=1, hi=16, value=int(getattr(anti_detection, "read_obfuscator_bogus_per_real", 4)), decimals=0,
			help_text=(
				"<b>Restart required.</b> Sets the number of additional random-address attempts before a wrapped read. "
				"More attempts can add overhead; no preferred count or detector benefit is established."
			),
			risk=bind_risk("read_obfuscator", self.config),
		)
		bogus_reads_slider.valueChanged.connect(lambda value: setattr(anti_detection, "read_obfuscator_bogus_per_real", int(value)))
		obfuscator_card.addBodyWidget(bogus_reads_slider)

		window_card = self.add_widget(FeatureCard(
			"Window-class masquerade",
			help_text=(
				"<b>Menu restart required.</b> Registers an app-owned native host with the selected Win32 class and embeds the existing Qt menu inside it. "
				"The applied host class is read back from Windows; the embedded child retains its Qt class. "
				"No matching external app needs to be running. This changes window identity, not process or image identity."
			),
			risk=bind_risk("window_class", self.config),
			toggle_target=(anti_detection, "window_class_masquerade_enabled"),
		))
		class_combo = _ChevronCombo()
		class_combo.addItems(["Notepad", "CalcFrame", "ApplicationFrameWindow", "Explorer"])
		current_class = str(getattr(anti_detection, "window_class_masquerade_target", "Notepad"))
		class_index = class_combo.findText(current_class)
		if class_index >= 0:
			class_combo.setCurrentIndex(class_index)
		class_combo.currentTextChanged.connect(lambda value: setattr(anti_detection, "window_class_masquerade_target", str(value)))
		window_card.addBodyWidget(LabeledRow(
			"Masquerade as", class_combo,
			help_text=(
				"<b>Menu restart required.</b> Selects the class registered for this app's host window. "
				"Explorer uses <code>CabinetWClass</code>; no Explorer window or another process is modified."
			),
			risk=bind_risk("window_class", self.config),
		))
		self._class_status = QLabel()
		self._class_status.setWordWrap(True)
		self._class_status.setAccessibleName("Applied window class")
		self._class_status_row = LabeledRow("Applied class", self._class_status,
			help_text="The verified class of the active native menu host, not the requested setting.")
		window_card.addBodyWidget(self._class_status_row)
		class_combo.currentTextChanged.connect(self._refresh_window_class_status)
		window_card.headerToggle().toggled.connect(self._refresh_window_class_status)
		self._class_status_timer = QTimer(self)
		self._class_status_timer.setSingleShot(True)
		self._class_status_timer.timeout.connect(self._refresh_window_class_status)
		self._class_status_timer.start(0)
		self._refresh_window_class_status()

		self.end()

	def _refresh_window_class_status(self, *_args) -> None:
		'''Keep requested startup settings distinct from the host's actual Win32 class.'''
		settings = self.config.anti_detection
		enabled = bool(getattr(settings, "window_class_masquerade_enabled", False))
		requested = str(getattr(settings, "window_class_masquerade_target", "Notepad"))
		window = self.window()
		status = getattr(window, "window_class_status", {})
		host = getattr(window, "_window_class_host", None)
		actual = host.current_class() if host is not None else status.get("applied_class")
		self._class_status_row.setVisible(bool(enabled or actual or status.get("error")))
		if actual:
			pending = not enabled or requested != status.get("requested_class")
			text = f"{actual} · active" + (" · menu restart required for the new setting" if pending else "")
		elif status.get("error"):
			text = "Not applied: " + str(status["error"])
		else:
			text = "Pending menu restart" if enabled else "Off"
		self._class_status.setText(text)


__all__ = ["AntiDetectionPanel"]
