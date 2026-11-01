"""
Central keybind registry panel.
"""

from PySide6.QtWidgets import QLabel, QWidget

from cs2esp.risk import bind_risk
from cs2esp.ui.qt.panels.base import PanelBase
from cs2esp.ui.qt.widgets import FeatureCard, KeybindRow


class KeybindsPanel(PanelBase):
	"""Centralised key-binding editor."""
	def __init__(self, config, parent: QWidget = None) -> None:
		super().__init__(config, parent)

		self.h1("Keybinds")
		self.intro(
			"Set the shortcuts that control the app and its live features. Rebinding listens for ten seconds. ESC cancels and <b>Clear</b> removes a binding."
		)

		app_card = FeatureCard(
			"App controls", risk=bind_risk('keybinds', lambda: self.config),
			help_text=(
				"Click <b>Rebind</b>, hold any modifiers and press the key. "
				"The current-binding cell previews the combination as it is "
				"captured. Confirm with <b>OK</b>; Cancel, <code>Esc</code> or "
				"the ten-second timeout restores the previous binding. "
				"<code>Esc</code> is reserved and cannot be assigned.<br><br>"
				"The master toggle is bound at startup, so restart after "
				"changing it. Menu and exit bindings are polled live."
			),
		)
		self.add_widget(app_card)
		general_config = self.config.general
		for label, attr in [("Toggle menu", "toggle_menu_key"), ("Master on/off", "master_toggle_key"), ("Exit app", "exit_key")]:
			app_card.addBodyWidget(KeybindRow(label, general_config, attr))

		feat_card = FeatureCard(
			"Feature hotkeys", risk=bind_risk('keybinds', lambda: self.config),
			help_text=(
				"Every feature can expose two independent slots:<br><br>"
				"<b>Hold</b> runs only while the key is down; <b>Toggle</b> "
				"flips the feature on or off with one press.<br><br>"
				"Either or both can be bound; an empty cell means "
				"unbound. Hold and toggle bindings can coexist on the same "
				"feature without conflict."
			),
		)
		self.add_widget(feat_card)

		self._sub_h(feat_card, "Combat")
		self._add_pair(feat_card, "Triggerbot", self.config.triggerbot)
		self._add_pair(feat_card, "Aimbot", self.config.aimbot)
		self._add_pair(feat_card, "Rapid-fire", self.config.rapidfire)
		self._add_pair(feat_card, "Auto-pistol", self.config.autopistol)
		self._add_pair(feat_card, "Recoil control", self.config.recoil_control)
		self._add_pair(feat_card, "BHop", self.config.bhop)

		self._sub_h(feat_card, "Visuals")
		feat_card.addBodyWidget(KeybindRow("Wallhack \u00b7 toggle", self.config.wallhack, "hotkey_toggle"))
		feat_card.addBodyWidget(KeybindRow("Names (name + distance) \u00b7 toggle", self.config.visuals.name, "hotkey_toggle"))
		feat_card.addBodyWidget(KeybindRow("Snapline \u00b7 toggle", self.config.visuals.snapline, "hotkey_toggle"))
		feat_card.addBodyWidget(KeybindRow("Crosshair \u00b7 toggle", self.config.visuals.crosshair, "hotkey_toggle"))
		feat_card.addBodyWidget(KeybindRow("FPS counter \u00b7 toggle", self.config.fps_counter, "hotkey_toggle"))
		feat_card.addBodyWidget(KeybindRow("Radar \u00b7 toggle", self.config.radar, "hotkey_toggle"))
		feat_card.addBodyWidget(KeybindRow("Awareness · toggle", self.config.awareness, "hotkey_toggle"))

		self.end()

	def _sub_h(self, card: FeatureCard, name: str) -> None:
		'''Separate related bindings inside the shared feature card.'''
		heading = QLabel(name, card)
		heading.setObjectName("KeybindGroupHeader")
		card.addBodyWidget(heading)

	def _add_pair(self, card: FeatureCard, label: str, section) -> None:
		'''Add a hold + toggle KeybindRow pair for section.'''
		card.addBodyWidget(KeybindRow(f"{label} · hold", section, "hotkey"))
		card.addBodyWidget(KeybindRow(f"{label} · toggle", section, "hotkey_toggle"))


__all__ = ["KeybindsPanel"]
