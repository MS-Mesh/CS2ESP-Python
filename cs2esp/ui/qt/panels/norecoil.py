"""
Recoil-script generator for supported mouse vendors.
"""

from os import environ
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QWidget

from cs2esp.features import norecoil_scripts as _scripts
from cs2esp.features.recoil_profiles import RECOIL_WEAPONS, has_recoil_profile, remove_recoil_profile, resolve_recoil_profile, set_recoil_profile_value
from cs2esp.recoil_runtime import HeldWeapon, read_held_weapon_status
from cs2esp.risk import bind_risk
from cs2esp.ui.qt.panels.base import PanelBase
from cs2esp.ui.qt.widgets import ComboRow, FeatureCard, KeybindRow, LabeledSlider


class _ProfileChoice:
	"""Menu-only choice, deliberately outside serialized configuration."""
	selection = "held"


class NoRecoilPanel(PanelBase):
	"""Generate vendor-side recoil scripts without touching CS2 memory."""
	RESET_SCOPES = {"In-game recoil control": ("recoil_control",)}

	def __init__(self, config, parent: QWidget | None = None) -> None:
		super().__init__(config, parent)

		self.h1("Recoil scripts")
		self.intro(
			"In-game recoil control reads CS2's live aim punch and cancels it "
			"with mouse input. The vendor scripts below are the offline "
			"alternative: a G HUB Lua or AutoHotkey v2 sidecar that replays "
			"fixed patterns."
		)

		self._profile_choice = _ProfileChoice()
		self._held_weapon = HeldWeapon()
		self._edit_weapon_key = None
		self._rcs_sliders = {}
		self._rcs_edit_sessions = {}
		self._rcs_edit_controls = {}
		self._rcs_refreshing = False
		self._build_rcs_card()

		warn = QLabel(
			(
				'Fixed-pattern examples assume sensitivity 1.0 and do not use live recoil feedback. Their accuracy is not guaranteed for '
				'every weapon or patch. Generating a file does not execute it; competitive use of the generated recoil macro is prohibited.'
			)
		)
		warn.setObjectName("FeatureWarning")
		warn.setWordWrap(True)
		warn.setTextFormat(Qt.RichText)
		self.add_widget(warn)

		for vendor in _scripts.VENDORS:
			self._build_vendor_card(vendor)

		self.end()

	def _build_rcs_card(self) -> None:
		rcs = getattr(self.config, "recoil_control", None)
		if rcs is None:
			return
		card = self.add_widget(
			FeatureCard(
				"In-game recoil control",
				help_text=(
					"Reads CS2's current aim punch and shots fired; the existing "
					"relative <code>mouse_event</code> input path cancels live punch deltas. "
					"Optional held-weapon profiles change only strength and start bullet, not "
					"the spray pattern. Missing fields use global settings. Known non-firearms "
					"and unreadable weapon handles pause compensation. Sensitivity remains global. "
					"This feature reads metadata and never writes game memory; external input "
					"does not establish permission or a detection guarantee. Turn vendor scripts "
					"off while this is on, or both compensate."
				),
				risk=bind_risk('recoil_control', lambda: self.config),
				toggle_target=(rcs, "enabled"),
			)
		)

		self._profile_selector = ComboRow(
			"Edit profile",
			self._profile_choice,
			"selection",
			[("held", "Follow held weapon"), ("global", "Global fallback")] + [(weapon.key, weapon.name) for weapon in RECOIL_WEAPONS],
			default="held",
		)
		self._profile_selector.setObjectName("RecoilProfileSelector")
		self._profile_selector.changed.connect(lambda _selection: self._refresh_rcs_profile())
		card.addBodyWidget(self._profile_selector)
		self._held_label = QLabel(card)
		self._held_label.setObjectName("RecoilHeldWeapon")
		self._held_label.setWordWrap(True)
		card.addBodyWidget(self._held_label)

		for label, field, upper, decimals, step, help_text in [
			("Vertical strength", "strength_y", 1.5, 2, 0.01, "Share of the upward live kick to cancel. 1.0 = full."),
			("Horizontal strength", "strength_x", 1.5, 2, 0.01, "Share of the sideways live drift to cancel."),
			("Start after bullet", "start_bullet", 6, 0, 1, "Compensation starts at this shot count; earlier samples establish the live baseline."),
		]:
			profile = resolve_recoil_profile(rcs)
			widget = LabeledSlider(
				label, lo=1 if field == "start_bullet" else 0.0, hi=upper, value=getattr(profile, field), decimals=decimals, step=step, help_text=help_text
			)
			widget.setProperty("recoilField", field)
			widget.valueChanged.connect(lambda value, selected_field=field: self._write_rcs_parameter(selected_field, value))
			widget._slider.sliderPressed.connect(lambda selected_field=field: self._begin_rcs_edit(selected_field, "slider"))
			widget._slider.sliderReleased.connect(lambda selected_field=field: self._end_rcs_edit(selected_field, "slider"))
			widget._spin.editingFinished.connect(lambda selected_field=field: self._end_rcs_edit(selected_field, "numeric"))
			for control, kind in ((widget._spin, "numeric"), (widget._spin.lineEdit(), "numeric"), (widget._slider, "slider")):
				self._rcs_edit_controls[control] = (field, kind)
				control.installEventFilter(self)
			for button in (widget._up_btn, widget._down_btn):
				self._rcs_edit_controls[button] = (field, "button")
				button.installEventFilter(self)
				button.pressed.connect(lambda selected_field=field: self._begin_rcs_edit(selected_field, "button"))
				button.clicked.connect(lambda _checked=False, selected_field=field: self._end_rcs_edit(selected_field, "button"))
			self._rcs_sliders[field] = widget
			card.addBodyWidget(widget)

		self._reset_profile = QPushButton("Use global values for this weapon", card)
		self._reset_profile.setObjectName("RecoilProfileReset")
		self._reset_profile.clicked.connect(self._remove_selected_profile)
		card.addBodyWidget(self._reset_profile)
		sensitivity = LabeledSlider(
			"Sensitivity override (global)",
			lo=0.0,
			hi=8.0,
			value=float(getattr(rcs, "sensitivity", 0.0)),
			decimals=2,
			step=0.01,
			help_text="0 = read sensitivity from CS2's config automatically. Shared by every weapon; no scoped calibration is assumed.",
		)
		sensitivity.valueChanged.connect(lambda value: setattr(rcs, "sensitivity", float(value)))
		card.addBodyWidget(sensitivity)
		card.addBodyWidget(KeybindRow("Recoil control · hold", rcs, "hotkey"))
		card.addBodyWidget(KeybindRow("Recoil control · toggle", rcs, "hotkey_toggle"))
		self._rcs_timer = QTimer(self)
		self._rcs_timer.setInterval(250)
		self._rcs_timer.timeout.connect(self._poll_held_weapon)
		self._rcs_timer.start()
		self._poll_held_weapon()

	def _poll_held_weapon(self) -> None:
		self._held_weapon = read_held_weapon_status()
		self._refresh_rcs_profile()

	def _begin_rcs_edit(self, field: str, kind: str) -> None:
		if field not in self._rcs_edit_sessions:
			self._rcs_edit_sessions[field] = (self._edit_weapon_key, self._profile_choice.selection, kind)

	def _end_rcs_edit(self, field: str, kind: str) -> None:
		session = self._rcs_edit_sessions.get(field)
		if session is not None and session[2] == kind:
			del self._rcs_edit_sessions[field]
			self._refresh_rcs_profile()

	def _defer_rcs_edit_end(self, field: str, kind: str) -> None:
		session = self._rcs_edit_sessions.get(field)
		if session is None:
			return
		def finish() -> None:
			if self._rcs_edit_sessions.get(field) is session:
				self._end_rcs_edit(field, kind)
		QTimer.singleShot(0, self, finish)

	def eventFilter(self, control, event) -> bool:
		binding = self._rcs_edit_controls.get(control)
		if binding is None:
			return super().eventFilter(control, event)
		field, kind = binding
		event_type = event.type()
		if kind == "numeric":
			if event_type in (QEvent.FocusIn, QEvent.KeyPress, QEvent.MouseButtonPress, QEvent.InputMethod):
				self._begin_rcs_edit(field, kind)
			elif event_type in (QEvent.FocusOut, QEvent.Hide):
				# Qt may commit a pending value while losing focus; release only after that commit returns
				self._defer_rcs_edit_end(field, kind)
		elif event_type in (QEvent.KeyPress, QEvent.MouseButtonPress):
			self._begin_rcs_edit(field, kind)
		elif event_type == QEvent.KeyRelease and not event.isAutoRepeat():
			self._defer_rcs_edit_end(field, kind)
		elif event_type in (QEvent.MouseButtonRelease, QEvent.FocusOut, QEvent.Hide):
			self._defer_rcs_edit_end(field, kind)
		return super().eventFilter(control, event)

	def _refresh_rcs_profile(self) -> None:
		rcs = getattr(self.config, "recoil_control", None)
		if rcs is None or not self._rcs_sliders:
			return
		selected = self._profile_choice.selection
		following = selected == "held"
		held = self._held_weapon
		self._edit_weapon_key = held.weapon_key if following and held.valid and held.is_firearm else None
		if not following and selected != "global":
			self._edit_weapon_key = selected
		editable = not following or (held.valid and held.is_firearm)
		if held.valid:
			description = f"Held: {held.weapon_name}"
			if held.scope_capable:
				description += " · scoped" if held.is_scoped else " · scope state unavailable" if held.is_scoped is None else " · unscoped"
			if not held.is_firearm:
				description += " · non-firearm; compensation paused"
		else:
			description = "Held weapon unavailable · compensation paused; choose Global fallback to edit defaults"
		if self._edit_weapon_key:
			source = (
				"weapon overrides; omitted fields use global"
				if has_recoil_profile(rcs, self._edit_weapon_key)
				else "global fallback; edits create this weapon's overrides"
			)
			description += f" · editing {self._edit_weapon_key} ({source})"
		elif selected == "global":
			description += " · editing global fallback"
		self._held_label.setText(description)
		profile = resolve_recoil_profile(rcs, self._edit_weapon_key)
		self._rcs_refreshing = True
		try:
			for field, widget in self._rcs_sliders.items():
				widget.setEnabled(editable)
				widget.setValue(getattr(profile, field))
				widget.setProperty("recoilProfileKey", self._edit_weapon_key or "global")
		finally:
			self._rcs_refreshing = False
		self._reset_profile.setEnabled(bool(self._edit_weapon_key and has_recoil_profile(rcs, self._edit_weapon_key)))

	def _write_rcs_parameter(self, field: str, value: float | int) -> None:
		if self._rcs_refreshing:
			return
		session = self._rcs_edit_sessions.get(field)
		if session is not None and session[:2] != (self._edit_weapon_key, self._profile_choice.selection):
			self._refresh_rcs_profile()
			return
		if self._profile_choice.selection == "held":
			# A user edit validates the displayed identity again; a switch during a drag must not edit the new weapon
			latest = read_held_weapon_status()
			if not latest.valid or not latest.is_firearm or latest.weapon_key != self._edit_weapon_key:
				self._held_weapon = latest
				self._refresh_rcs_profile()
				return
		rcs = getattr(self.config, "recoil_control", None)
		if rcs is not None:
			set_recoil_profile_value(rcs, self._edit_weapon_key, field, value)
			self._refresh_rcs_profile()

	def _remove_selected_profile(self) -> None:
		selected_key = self._edit_weapon_key
		if self._profile_choice.selection == "held":
			latest = read_held_weapon_status()
			if not latest.valid or not latest.is_firearm or latest.weapon_key != selected_key:
				self._held_weapon = latest
				self._refresh_rcs_profile()
				return
		if selected_key is not None:
			remove_recoil_profile(self.config.recoil_control, selected_key)
			self._refresh_rcs_profile()

	def _build_vendor_card(self, vendor) -> None:
		'''Build one script card with download, copy, and setup guide actions.'''
		card = self.add_widget(
			FeatureCard(
				f"{vendor.display} · {vendor.language}",
				help_text=(
					f"A ready-to-paste <b>{vendor.language}</b> script with a "
					f"real per-bullet spray pattern for all 16 full-auto weapons, "
					f"so compensation follows each gun's actual recoil. Calibrated "
					f"for in-game sensitivity 1.0 — edit <code>MULT</code> to match "
					f"yours. Toggle <b>Caps Lock</b> in-game to enable/disable."
				),
				risk=bind_risk('recoil_script_logitech' if vendor.id == 'logitech' else 'recoil_script_ahk', lambda: self.config),
			)
		)

		row = QWidget(card)
		row.setObjectName("ScriptActions")
		layout = QHBoxLayout(row)
		layout.setContentsMargins(0, 0, 0, 0)
		layout.setSpacing(8)

		download_button = QPushButton(f"Download {vendor.extension}", row)
		download_button.setObjectName("PrimaryActionBtn")
		download_button.setFocusPolicy(Qt.NoFocus)
		download_button.setCursor(Qt.PointingHandCursor)
		download_button.setToolTip("Save the generated script to disk. Opens your system file dialog, defaulting to your Downloads folder.")
		download_button.clicked.connect(lambda *_: self._download(vendor))
		layout.addWidget(download_button)

		copy_button = QPushButton("Copy to clipboard", row)
		copy_button.setObjectName("SecondaryActionBtn")
		copy_button.setFocusPolicy(Qt.NoFocus)
		copy_button.setCursor(Qt.PointingHandCursor)
		copy_button.setToolTip("Copy the whole script to the system clipboard so you can paste it straight into the vendor's script editor.")
		copy_button.clicked.connect(lambda *_: self._copy(vendor))
		layout.addWidget(copy_button)

		guide_btn = QPushButton("Show guide", row)
		guide_btn.setObjectName("SecondaryActionBtn")
		guide_btn.setFocusPolicy(Qt.NoFocus)
		guide_btn.setCursor(Qt.PointingHandCursor)
		guide_btn.setToolTip("Click to expand the step-by-step setup guide.")
		layout.addWidget(guide_btn)

		layout.addStretch(1)
		card.addBodyWidget(row)

		guide_lbl = QLabel(vendor.guide_html, card)
		guide_lbl.setTextFormat(Qt.RichText)
		guide_lbl.setWordWrap(True)
		guide_lbl.setOpenExternalLinks(True)
		guide_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
		guide_lbl.setObjectName("ScriptGuide")
		guide_lbl.setVisible(False)
		card.addBodyWidget(guide_lbl)

		guide_btn.clicked.connect(lambda *_, guide_label=guide_lbl, guide_button=guide_btn: self._toggle_guide(guide_label, guide_button))

	def _toggle_guide(self, guide_label: QLabel, guide_button: QPushButton) -> None:
		'''Flip the guide visibility and swap the button label.'''
		showing = not guide_label.isVisible()
		guide_label.setVisible(showing)
		guide_button.setText("Hide guide" if showing else "Show guide")

	def _download(self, vendor) -> None:
		'''Save the script to disk via a standard Save dialog.'''
		try:
			content = _scripts.build_script(vendor.id)
		except Exception as e:
			self._message(QMessageBox.Warning, f"Script generation failed: {e!r}")
			return

		default_name = _scripts.suggested_filename(vendor.id)
		profile = environ.get("USERPROFILE")
		home_dir = Path(profile) if profile else Path.home()
		downloads_dir = home_dir / "Downloads"
		default_dir = downloads_dir if downloads_dir.exists() else home_dir
		default_path = str(default_dir / default_name)
		path, _ = QFileDialog.getSaveFileName(
			self, f"Save {vendor.display} script", default_path, f"{vendor.language} script (*{vendor.extension});;All files (*.*)"
		)
		if not path:
			return
		try:
			with open(path, "w", encoding="utf-8", newline="") as script_file:
				script_file.write(content)
			self._message(QMessageBox.Information, f"Saved <b>{Path(path).name}</b> to <code>{Path(path).parent}</code>.")
		except OSError as e:
			self._message(QMessageBox.Warning, f"Could not write the file: {e.strerror or e}.")

	def _copy(self, vendor) -> None:
		'''Copy the generated script to the system clipboard.'''
		try:
			content = _scripts.build_script(vendor.id)
		except Exception as e:
			self._message(QMessageBox.Warning, f"Script generation failed: {e!r}")
			return
		try:
			QGuiApplication.clipboard().setText(content)
		except Exception as e:
			self._message(QMessageBox.Warning, f"Clipboard error: {e!r}")
			return
		self._message(
			QMessageBox.Information,
			f"Copied the {vendor.display} {vendor.language} script to your clipboard — paste it into the vendor's script editor.",
		)

	def _message(self, icon, html: str) -> None:
		'''Show one modal message box with the shared title and rich-text body.'''
		box = QMessageBox(self)
		box.setIcon(icon)
		box.setWindowTitle("Recoil scripts")
		box.setTextFormat(Qt.RichText)
		box.setText(html)
		box.exec()


__all__ = ["NoRecoilPanel"]
