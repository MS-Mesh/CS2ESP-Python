"""Radar panel for the corner mini-map."""

from PySide6.QtWidgets import QWidget

from cs2esp.ui.qt.panels.base import PanelBase
from cs2esp.ui.qt.widgets import (
    ChoiceRow, ColorButton, FeatureCard, KeybindRow, LabeledRow,
    LabeledSlider, ToggleSwitch,
)


class RadarPanel(PanelBase):
    """Radar configuration panel."""

    def __init__(self, config, parent: QWidget = None) -> None:
        super().__init__(config, parent)
        r = self.config.radar

        self.h1("Radar")
        self.intro(
            "Track nearby players on a compact, view-relative map. Radar is "
            "drawn entirely in the overlay and never writes to game memory."
        )

        display_card = self.add_widget(FeatureCard(
            "Radar display",
            help_text=(
                "Choose the map's shape, corner and geometry. Set "
                "<i>Screen-relative size</i> above zero to scale with the "
                "display, or leave it at zero to use the pixel size."
            ),
            toggle_target=(r, "enabled"),
        ))

        display_card.addBodyWidget(ChoiceRow(
            "Shape", r, "shape",
            [("round", "Round"),
             ("square", "Square")],
            default="round",
        ))
        display_card.addBodyWidget(ChoiceRow(
            "Anchor", r, "anchor",
            [("top_left", "Top-left"),
             ("top_right", "Top-right"),
             ("bottom_left", "Bottom-left"),
             ("bottom_right", "Bottom-right")],
            default="top_left",
        ))
        for label, attr, lo, hi, step, dec in [
            ("Screen-relative size (0 = pixels)", "size_fraction",
             0.0, 0.50, 0.01, 2),
            ("Pixel size", "size",
             80.0, 600.0, 5.0, 0),
            ("Range (units)", "range",
             500.0, 10000.0, 50.0, 0),
            ("Opacity", "opacity",
             0.10, 1.0, 0.05, 2),
        ]:
            sl = LabeledSlider(label, lo=lo, hi=hi,
                               value=float(getattr(r, attr)),
                               decimals=dec, step=step)
            sl.valueChanged.connect(
                lambda v, a=attr: setattr(r, a, float(v)))
            display_card.addBodyWidget(sl)

        team_sw = ToggleSwitch()
        team_sw.setChecked(bool(getattr(r, "show_teammates", False)))
        team_sw.toggled.connect(
            lambda c: setattr(r, "show_teammates", bool(c)))
        display_card.addBodyWidget(LabeledRow(
            "Show teammates", team_sw,
            help_text=("Render allies on the radar with the teammate "
                       "colour. Off = enemies only (less screen noise)."),
        ))

        display_card.addBodyWidget(KeybindRow(
            "Radar · toggle", r, "hotkey_toggle"
        ))

        colours_card = self.add_widget(FeatureCard(
            "Marker colours",
            help_text=(
                "Set colours for player markers, the local-player marker and "
                "the radar surface. Teammate colour appears only while allies "
                "are enabled above."
            ),
        ))
        colours_card.addBodyWidget(ColorButton(
            "Enemy colour", r, "enemy_color", with_alpha=True
        ))
        teammate_color_btn = ColorButton("Teammate colour",
                                          r, "teammate_color",
                                          with_alpha=True)
        colours_card.addBodyWidget(teammate_color_btn)
        team_sw.toggled.connect(
            lambda c: teammate_color_btn.setVisible(bool(c)))
        teammate_color_btn.setVisible(
            bool(getattr(r, "show_teammates", False)))
        colours_card.addBodyWidget(ColorButton(
            "Background", r, "background_color", with_alpha=True
        ))
        colours_card.addBodyWidget(ColorButton(
            "Border", r, "border_color", with_alpha=True
        ))
        colours_card.addBodyWidget(ColorButton(
            "Local player", r, "local_color", with_alpha=True
        ))
        self.end()


__all__ = ["RadarPanel"]
