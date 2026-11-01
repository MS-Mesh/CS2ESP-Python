"""
Awareness panel for the peripheral-vision threat vignette.
"""

from PySide6.QtWidgets import QWidget

from cs2esp.risk import bind_risk
from cs2esp.ui.qt.panels.base import PanelBase
from cs2esp.ui.qt.widgets import ColorButton, FeatureCard, KeybindRow, LabeledRow, LabeledSlider, ToggleSwitch


class AwarenessPanel(PanelBase):
	"""Awareness / threat-vignette configuration."""
	RESET_SCOPES = {"Threat vignette": ("awareness",)}

	def __init__(self, config, parent: QWidget = None) -> None:
		super().__init__(config, parent)
		awareness_config = self.config.awareness

		self.h1("Awareness")
		self.intro("Surface threats outside your immediate focus with a subtle edge pulse. Intensity follows distance and how directly an enemy is looking at you.")

		card = self.add_widget(
			FeatureCard(
				"Threat vignette", risk=bind_risk('awareness', lambda: self.config),
				help_text=(
					"Pulses a coloured vignette at the screen edges when "
					"an enemy's view cone intersects your position. "
					"<i>Cone (degrees)</i> sets how tightly the enemy "
					"has to be looking at you for the pulse to trigger. "
					"<i>Rear-only</i> mode is the classic CS2 awareness "
					"use case: only show pulses for threats that aren't "
					"already in your forward FOV."
				),
				toggle_target=(awareness_config, "enabled"),
			)
		)

		for label, attr, minimum, maximum, step, decimals, help_text in [
			("Cone (degrees)", "cone_degrees", 1.0, 30.0, 0.5, 1, "Half-angle of the enemy's view cone we treat as a threat."),
			("Max distance (units)", "max_distance", 500.0, 8000.0, 50.0, 0, "Beyond this distance the pulse fades to 0 even if the enemy is looking at you."),
			("Fade speed", "fade_speed", 0.5, 12.0, 0.1, 1, "Higher = pulse decays faster after the threat breaks."),
			("Edge thickness (px)", "edge_thickness", 20.0, 400.0, 5.0, 0, "Vignette band width along each screen edge."),
		]:
			slider = LabeledSlider(label, lo=minimum, hi=maximum, value=float(getattr(awareness_config, attr)), decimals=decimals, step=step, help_text=help_text)
			slider.valueChanged.connect(lambda v, x=attr: setattr(awareness_config, x, float(v)))
			card.addBodyWidget(slider)
		card.addBodyWidget(ColorButton("Threat colour", awareness_config, "color", with_alpha=True))

		rear_sw = ToggleSwitch()
		rear_sw.setChecked(bool(getattr(awareness_config, "rear_only", True)))
		rear_sw.toggled.connect(lambda c: setattr(awareness_config, "rear_only", bool(c)))
		card.addBodyWidget(
			LabeledRow(
				"Rear-only threats",
				rear_sw,
				help_text=(
					"Only pulse for threats outside our forward field of "
					"view; useful because you can already see anyone in "
					"front of you. The slider below sets that forward "
					"FOV; threats inside it are suppressed."
				),
			)
		)
		rear_fov = LabeledSlider("Forward FOV (degrees)", lo=60.0, hi=180.0, value=float(getattr(awareness_config, "rear_fov_deg", 100.0)), decimals=0, step=1.0)
		rear_fov.valueChanged.connect(lambda v: setattr(awareness_config, "rear_fov_deg", float(v)))
		card.addBodyWidget(rear_fov)
		rear_sw.toggled.connect(lambda c: rear_fov.setVisible(bool(c)))
		rear_fov.setVisible(bool(getattr(awareness_config, "rear_only", True)))
		card.addBodyWidget(KeybindRow("Awareness · toggle", awareness_config, "hotkey_toggle"))

		self.end()


__all__ = ["AwarenessPanel"]
