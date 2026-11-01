"""
Radar panel for the corner mini-map.
"""

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLabel, QWidget

from cs2esp.risk import bind_risk
from cs2esp.ui.qt.panels.base import PanelBase
from cs2esp.ui.qt.widgets import ChoiceRow, ColorButton, FeatureCard, KeybindRow, LabeledRow, LabeledSlider, ToggleSwitch
from cs2esp.visuals.radar import radar_size


class RadarPanel(PanelBase):
	"""Radar configuration panel."""
	RESET_SCOPES = {"Radar display": ("radar",)}

	def __init__(self, config, parent: QWidget = None) -> None:
		super().__init__(config, parent)
		radar_config = self.config.radar

		self.h1("Radar")
		self.intro("Track nearby players on a compact, view-relative map. Radar is drawn entirely in the overlay and never writes to game memory.")

		display_card = self.add_widget(
			FeatureCard(
				"Radar display", risk=bind_risk('radar', lambda: self.config),
				help_text=(
					"Choose the map's shape, corner and geometry. Set "
					"<i>Scale with screen height</i> uses an explicit percentage of the display height. "
					"Changing sizing mode preserves the radar's current diameter on this display. "
					"Marker colours control player markers, the local-player marker and the radar surface. "
					"Teammate colour appears only while allies are enabled."
				),
				toggle_target=(radar_config, "enabled"),
			)
		)

		display_card.addBodyWidget(ChoiceRow("Shape", radar_config, "shape", [("round", "Round"), ("square", "Square")], default="round"))
		display_card.addBodyWidget(
			ChoiceRow(
				"Anchor",
				radar_config,
				"anchor",
				[("top_left", "Top-left"), ("top_right", "Top-right"), ("bottom_left", "Bottom-left"), ("bottom_right", "Bottom-right")],
				default="top_left",
			)
		)
		self._relative_size_switch = ToggleSwitch(checked=float(getattr(radar_config, "size_fraction", 0.0) or 0.0) > 0.0)
		display_card.addBodyWidget(LabeledRow("Scale with screen height", self._relative_size_switch,
			help_text="Keep a consistent proportion on different displays. Switching modes keeps the current on-screen diameter."))
		self._pixel_size = LabeledSlider("Pixel size", lo=80.0, hi=2160.0, value=float(radar_config.size), decimals=0, step=5.0)
		self._pixel_size.valueChanged.connect(lambda value: setattr(radar_config, "size", float(value)))
		display_card.addBodyWidget(self._pixel_size)
		fraction = float(getattr(radar_config, "size_fraction", 0.0) or 0.0)
		percent = (fraction or radar_size(radar_config, self._screen_height()) / self._screen_height()) * 100.0
		self._relative_size = LabeledSlider("Screen height (%)", lo=1.0, hi=100.0, value=percent, decimals=2, step=1.0)
		self._relative_size.valueChanged.connect(lambda value: setattr(radar_config, "size_fraction", float(value) / 100.0))
		display_card.addBodyWidget(self._relative_size)
		self._pixel_size.setVisible(not self._relative_size_switch.isChecked())
		self._relative_size.setVisible(self._relative_size_switch.isChecked())
		self._relative_size_switch.toggled.connect(self._set_size_mode)

		for label, attr, minimum, maximum, step, decimals in [
			("Range (units)", "range", 500.0, 10000.0, 50.0, 0),
			("Opacity", "opacity", 0.10, 1.0, 0.05, 2),
		]:
			slider = LabeledSlider(label, lo=minimum, hi=maximum, value=float(getattr(radar_config, attr)), decimals=decimals, step=step)
			slider.valueChanged.connect(lambda v, a=attr: setattr(radar_config, a, float(v)))
			display_card.addBodyWidget(slider)

		team_sw = ToggleSwitch()
		team_sw.setChecked(bool(getattr(radar_config, "show_teammates", False)))
		team_sw.toggled.connect(lambda c: setattr(radar_config, "show_teammates", bool(c)))
		display_card.addBodyWidget(
			LabeledRow("Show teammates", team_sw, help_text=("Render allies on the radar with the teammate colour. Off = enemies only (less screen noise)."))
		)

		colours_heading = QLabel("Marker colours")
		colours_heading.setObjectName("H2")
		display_card.addBodyWidget(colours_heading)
		display_card.addBodyWidget(ColorButton("Enemy colour", radar_config, "enemy_color", with_alpha=True))
		teammate_color_btn = ColorButton("Teammate colour", radar_config, "teammate_color", with_alpha=True)
		display_card.addBodyWidget(teammate_color_btn)
		team_sw.toggled.connect(lambda c: teammate_color_btn.setVisible(bool(c)))
		teammate_color_btn.setVisible(bool(getattr(radar_config, "show_teammates", False)))
		display_card.addBodyWidget(ColorButton("Background", radar_config, "background_color", with_alpha=True))
		display_card.addBodyWidget(ColorButton("Border", radar_config, "border_color", with_alpha=True))
		display_card.addBodyWidget(ColorButton("Local player", radar_config, "local_color", with_alpha=True))
		display_card.addBodyWidget(KeybindRow("Radar · toggle", radar_config, "hotkey_toggle"))
		self.end()

	def _screen_height(self) -> int:
		'''Match the physical display height used by the overlay, not the menu's logical size.'''
		screen_picker = getattr(self.window(), "_target_screen", None)
		screen = screen_picker() if callable(screen_picker) else QGuiApplication.primaryScreen()
		return max(1, round(screen.geometry().height() * screen.devicePixelRatio())) if screen is not None else 1080

	def _set_size_mode(self, relative: bool) -> None:
		'''Convert the current diameter rather than jumping to a tiny first fractional step.'''
		radar_config = self.config.radar
		height = self._screen_height()
		size = radar_size(radar_config, height)
		if relative:
			fraction = size / height
			self._relative_size.setValue(fraction * 100.0)
			radar_config.size_fraction = fraction
		else:
			self._pixel_size.setValue(size)
			radar_config.size = size
			radar_config.size_fraction = 0.0
		self._pixel_size.setVisible(not relative)
		self._relative_size.setVisible(relative)


__all__ = ["RadarPanel"]
