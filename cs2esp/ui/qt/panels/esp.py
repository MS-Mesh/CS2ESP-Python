"""ESP overlay controls and feature-specific styling."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QRadioButton,
    QVBoxLayout, QWidget,
)

from cs2esp.ui.qt.panels.base import PanelBase
from cs2esp.ui.qt.widgets import (
    ChoiceRow, ColorButton, FeatureCard, KeybindRow, LabeledRow,
    LabeledSlider, ToggleSwitch,
)


class _HeadOutlineAdvanced(QFrame):
    """Collapsible geometry controls for the skeleton head outline."""

    def __init__(self, config, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.setObjectName("HeadOutlineAdvanced")
        self.setFrameShape(QFrame.NoFrame)
        self._s = config.visuals.skeleton
        self._expanded = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 6, 0, 0)
        outer.setSpacing(8)

        header = QWidget(self)
        header.setObjectName("HeadAdvHeader")
        hdr = QHBoxLayout(header)
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(8)
        self._chevron = QPushButton(
            "\u25b8 Head outline geometry",
            header,
        )
        self._chevron.setObjectName("HeadAdvChevron")
        self._chevron.setFocusPolicy(Qt.NoFocus)
        self._chevron.setCursor(Qt.PointingHandCursor)
        self._chevron.setCheckable(True)
        self._chevron.toggled.connect(self._on_expand_toggled)
        hdr.addWidget(self._chevron, 1, Qt.AlignVCenter)
        outer.addWidget(header)

        self._body = QWidget(self)
        self._body.setObjectName("HeadAdvBody")
        body_lay = QVBoxLayout(self._body)
        body_lay.setContentsMargins(0, 4, 0, 0)
        body_lay.setSpacing(8)

        s = self._s
        is_auto = bool(getattr(s, "head_outline_offset_right_auto", True))

        mode_title = QLabel("Offset mode", self._body)
        mode_title.setObjectName("HeadAdvModeTitle")
        mode_title.setProperty("muted", "true")
        body_lay.addWidget(mode_title)

        self._mode_group = QButtonGroup(self._body)
        self._mode_group.setExclusive(True)
        self._rb_auto = QRadioButton(
            "Automatic — follows the view angle using one magnitude",
            self._body,
        )
        self._rb_anchor = QRadioButton(
            "Manual anchor — shifts from the screen or target axis",
            self._body,
        )
        self._rb_auto.setObjectName("HeadAdvModeOption")
        self._rb_anchor.setObjectName("HeadAdvModeOption")
        self._rb_auto.setProperty("mode", "automatic")
        self._rb_anchor.setProperty("mode", "manual")
        self._rb_auto.setChecked(is_auto)
        self._rb_anchor.setChecked(not is_auto)
        self._mode_group.addButton(self._rb_auto)
        self._mode_group.addButton(self._rb_anchor)
        body_lay.addWidget(self._rb_auto)
        body_lay.addWidget(self._rb_anchor)
        self._rb_auto.toggled.connect(self._on_mode_toggled)

        self._auto_section = QWidget(self._body)
        self._auto_section.setObjectName("HeadAdvModeSection")
        self._auto_section.setProperty("mode", "automatic")
        auto_lay = QVBoxLayout(self._auto_section)
        auto_lay.setContentsMargins(16, 0, 0, 0)
        auto_lay.setSpacing(6)
        head_magnitude = LabeledSlider(
            "Magnitude", lo=-3.0, hi=3.0,
            value=float(getattr(s, "head_outline_offset_right_magnitude", 1.0)),
            decimals=2, step=0.05,
            help_text=(
                "Automatic offset range across view angles. "
                "1.00 is the calibrated baseline."
            ),
        )
        head_magnitude.valueChanged.connect(
            lambda v: setattr(s, "head_outline_offset_right_magnitude", float(v)))
        auto_lay.addWidget(head_magnitude)
        body_lay.addWidget(self._auto_section)

        self._anchor_section = QWidget(self._body)
        self._anchor_section.setObjectName("HeadAdvModeSection")
        self._anchor_section.setProperty("mode", "manual")
        anchor_lay = QVBoxLayout(self._anchor_section)
        anchor_lay.setContentsMargins(16, 0, 0, 0)
        anchor_lay.setSpacing(6)
        anchor_sw = ToggleSwitch()
        anchor_sw.setChecked(bool(getattr(s, "head_outline_anchor_to_screen", False)))
        anchor_sw._live_section = s  # type: ignore[attr-defined]
        anchor_sw._live_attr = "head_outline_anchor_to_screen"  # type: ignore[attr-defined]
        anchor_sw.toggled.connect(
            lambda c: setattr(s, "head_outline_anchor_to_screen", bool(c)))
        anchor_lay.addWidget(LabeledRow(
            "Anchor to screen", anchor_sw,
            help_text=(
                "On shifts along screen-right. Off uses the target's "
                "backward body axis."
            ),
        ))
        head_off_right = LabeledSlider(
            "Horizontal offset", lo=-3.0, hi=3.0,
            value=float(getattr(s, "head_outline_offset_right", 0.0)),
            decimals=2, step=0.05,
            help_text="Manual shift along the selected anchor axis.",
        )
        head_off_right.valueChanged.connect(
            lambda v: setattr(s, "head_outline_offset_right", float(v)))
        anchor_lay.addWidget(head_off_right)
        body_lay.addWidget(self._anchor_section)

        head_w_mul = LabeledSlider(
            "Width scale", lo=0.2, hi=3.0,
            value=float(getattr(s, "head_outline_width_mul", 1.0)),
            decimals=2, step=0.05,
            help_text=(
                "Horizontal radius multiplier. 1.0 keeps the base shape."
            ),
        )
        head_w_mul.valueChanged.connect(
            lambda v: setattr(s, "head_outline_width_mul", float(v)))
        body_lay.addWidget(head_w_mul)

        head_h_mul = LabeledSlider(
            "Height scale", lo=0.2, hi=3.0,
            value=float(getattr(s, "head_outline_height_mul", 1.0)),
            decimals=2, step=0.05,
            help_text=(
                "Vertical radius multiplier. Lower values flatten the ring."
            ),
        )
        head_h_mul.valueChanged.connect(
            lambda v: setattr(s, "head_outline_height_mul", float(v)))
        body_lay.addWidget(head_h_mul)

        head_rot = LabeledSlider(
            "Rotation", lo=-180.0, hi=180.0,
            value=float(getattr(s, "head_outline_rotation_deg", 0.0)),
            decimals=0, step=1.0,
            help_text=(
                "Counter-clockwise ellipse rotation in degrees."
            ),
        )
        head_rot.valueChanged.connect(
            lambda v: setattr(s, "head_outline_rotation_deg", float(v)))
        body_lay.addWidget(head_rot)

        head_off_up = LabeledSlider(
            "Vertical offset", lo=-12.0, hi=12.0,
            value=float(getattr(s, "head_outline_offset_up", 0.0)),
            decimals=1, step=0.2,
            help_text=(
                "Shifts the ring in world units. +4 to +6 usually "
                "centres it on the face."
            ),
        )
        head_off_up.valueChanged.connect(
            lambda v: setattr(s, "head_outline_offset_up", float(v)))
        body_lay.addWidget(head_off_up)

        self._body.setVisible(False)
        outer.addWidget(self._body)

        self._refresh_mode_sections()

    def _on_expand_toggled(self, on: bool) -> None:
        self._expanded = bool(on)
        self._body.setVisible(self._expanded)
        glyph = "\u25be" if self._expanded else "\u25b8"
        self._chevron.setText(f"{glyph} Head outline geometry")

    def _on_mode_toggled(self, _on: bool) -> None:
        is_auto = self._rb_auto.isChecked()
        try:
            setattr(self._s, "head_outline_offset_right_auto", bool(is_auto))
        except Exception:
            pass
        self._refresh_mode_sections()

    def _refresh_mode_sections(self) -> None:
        is_auto = self._rb_auto.isChecked()
        self._auto_section.setVisible(is_auto)
        self._anchor_section.setVisible(not is_auto)


class ESPPanel(PanelBase):
    """Player-overlay and HUD settings."""

    def __init__(self, config, parent: QWidget = None) -> None:
        super().__init__(config, parent)

        v = self.config.visuals
        glow_config = self.config.glow

        self.h1("ESP")
        self.intro(
            "Compose player overlays and lightweight HUD elements. All "
            "visuals are <b>read-only</b> except <b>Glow</b>, which writes "
            "to game memory and carries the higher risk."
        )

        self.h2("Player overlays")
        wh = self.config.wallhack
        wh_card = self.add_widget(FeatureCard(
            "Wallhack master",
            help_text=(
                "Turns every player visual off together while preserving "
                "the mix below. Each primitive reveals its own styling "
                "when enabled; the inline hotkey controls this same master."
            ),
            toggle_target=(wh, "enabled"),
        ))

        self._wh_box_sw = ToggleSwitch()
        self._wh_box_sw.setChecked(bool(v.box.enabled))
        self._wh_box_sw._live_section = v.box  # type: ignore[attr-defined]
        self._wh_box_sw._live_attr = "enabled"  # type: ignore[attr-defined]
        self._wh_box_sw.toggled.connect(
            lambda c: self._on_wh_toggle("box", c))
        wh_card.addBodyWidget(LabeledRow(
            "Box outline", self._wh_box_sw,
            help_text=(
                "Draws a fitted player box. Geometry, fill and colour "
                "controls appear below when enabled."
            ),
        ))

        self._wh_skel_sw = ToggleSwitch()
        self._wh_skel_sw.setChecked(bool(v.skeleton.enabled))
        self._wh_skel_sw._live_section = v.skeleton  # type: ignore[attr-defined]
        self._wh_skel_sw._live_attr = "enabled"  # type: ignore[attr-defined]
        self._wh_skel_sw.toggled.connect(
            lambda c: self._on_wh_toggle("skeleton", c))
        wh_card.addBodyWidget(LabeledRow(
            "Skeleton wireframe", self._wh_skel_sw,
            help_text=(
                "Projects the bone matrix and connects each segment into "
                "a lightweight wireframe."
            ),
        ))

        self._wh_glow_sw = ToggleSwitch()
        self._wh_glow_sw.setChecked(bool(glow_config.enabled))
        self._wh_glow_sw._live_section = glow_config  # type: ignore[attr-defined]
        self._wh_glow_sw._live_attr = "enabled"  # type: ignore[attr-defined]
        self._wh_glow_sw.toggled.connect(
            lambda c: self._on_wh_toggle("glow", c))
        wh_card.addBodyWidget(LabeledRow(
            "Glow", self._wh_glow_sw,
            risk="high_faceit",
            help_text=(
                "Writes the engine glow state in <code>client.dll</code> so "
                "targets remain outlined through walls. Kernel-level "
                "anti-cheat may detect this write. <b>No documented bans "
                "from this app.</b>"
            ),
        ))

        chams_config = v.chams
        self._wh_chams_sw = ToggleSwitch()
        self._wh_chams_sw.setChecked(bool(getattr(chams_config, "enabled", False)))
        self._wh_chams_sw._live_section = chams_config  # type: ignore[attr-defined]
        self._wh_chams_sw._live_attr = "enabled"  # type: ignore[attr-defined]
        self._wh_chams_sw.toggled.connect(
            lambda c: self._on_wh_toggle("chams", c))
        wh_card.addBodyWidget(LabeledRow(
            "Chams", self._wh_chams_sw,
            risk="medium_faceit_only",
            help_text=(
                "Renders a solid 3D body in the OpenGL overlay without memory "
                "writes. Overlay-window heuristics remain a detection vector; "
                "capture protection hides recordings, not the physical display."
            ),
        ))

        self._box_card = self.add_widget(FeatureCard(
            "Box geometry",
            help_text=(
                "Controls the fitted outline, fill and colours. Team mode "
                "selects CT or T colour from each player's team."
            ),
        ))
        b = v.box
        self._box_card.addBodyWidget(ChoiceRow(
            "Sizing", b, "box_mode",
            [("bones", "Bones (accurate)"),
             ("bounds", "Bounds (hull)"),
             ("fixed", "Fixed")],
            default="bones",
        ))
        self._box_card.addBodyWidget(ChoiceRow(
            "Style", b, "style",
            [("outline", "Outline"),
             ("filled", "Filled"),
             ("corners", "Corners")],
            default="outline",
        ))
        thickness_sl = LabeledSlider(
            "Thickness", lo=0.5, hi=4.0, value=float(b.thickness),
            decimals=1, step=0.1,
            help_text="Pixel thickness of the outline. 1.0 is the default.",
        )
        thickness_sl.valueChanged.connect(
            lambda v: setattr(b, "thickness", float(v)))
        self._box_card.addBodyWidget(thickness_sl)
        box_color_btn = ColorButton("Box colour", b, "color",
                                     with_alpha=True)
        self._box_card.addBodyWidget(box_color_btn)

        team_sw = ToggleSwitch()
        team_sw.setChecked(bool(getattr(b, "team_colored", False)))
        team_sw.toggled.connect(
            lambda c: setattr(b, "team_colored", bool(c)))
        self._box_card.addBodyWidget(LabeledRow(
            "Team-coloured (CT / T)", team_sw,
            help_text=("Use CT-blue / T-red instead of the single "
                       "colour above."),
        ))
        box_ct_btn = ColorButton("CT colour", b, "ct_color", with_alpha=True)
        box_t_btn = ColorButton("T colour", b, "t_color", with_alpha=True)
        self._box_card.addBodyWidget(box_ct_btn)
        self._box_card.addBodyWidget(box_t_btn)

        def _sync_box_team(on: bool) -> None:
            box_color_btn.setVisible(not on)
            box_ct_btn.setVisible(on)
            box_t_btn.setVisible(on)

        team_sw.toggled.connect(_sync_box_team)
        _sync_box_team(bool(getattr(b, "team_colored", False)))

        self._skel_card = self.add_widget(FeatureCard(
            "Skeleton geometry",
            help_text="Controls wireframe weight, colour and head marker.",
        ))
        s = v.skeleton
        skel_thick = LabeledSlider(
            "Line thickness", lo=0.5, hi=3.0, value=float(s.thickness),
            decimals=1, step=0.1,
            help_text="Pixel thickness of the bone segments.",
        )
        skel_thick.valueChanged.connect(
            lambda v: setattr(s, "thickness", float(v)))
        self._skel_card.addBodyWidget(skel_thick)
        self._skel_card.addBodyWidget(ColorButton("Skeleton colour",
                                                  s, "color",
                                                  with_alpha=True))

        head_outline_sw = ToggleSwitch()
        head_outline_sw.setChecked(bool(getattr(s, "head_outline_enabled", False)))
        head_outline_sw._live_section = s  # type: ignore[attr-defined]
        head_outline_sw._live_attr = "head_outline_enabled"  # type: ignore[attr-defined]
        head_outline_sw.toggled.connect(
            lambda c: setattr(s, "head_outline_enabled", bool(c)))
        self._skel_card.addBodyWidget(LabeledRow(
            "Head outline", head_outline_sw,
            help_text=(
                "Draws a projected ellipse around the head bone. It follows "
                "the player axes and scales naturally with distance."
            ),
        ))
        head_radius = LabeledSlider(
            "Head outline radius (units)", lo=4.0, hi=20.0,
            value=float(getattr(s, "head_outline_radius_units", 9.0)),
            decimals=1, step=0.5,
            help_text=(
                "World-space ring radius in CS2 units. 9 is a close fit on "
                "the standard player model."
            ),
        )
        head_radius.valueChanged.connect(
            lambda v: setattr(s, "head_outline_radius_units", float(v)))
        self._skel_card.addBodyWidget(head_radius)
        head_thick = LabeledSlider(
            "Head outline thickness", lo=0.5, hi=4.0,
            value=float(getattr(s, "head_outline_thickness_px", 0.5)),
            decimals=1, step=0.1,
            help_text="Pixel thickness of the head-outline ring.",
        )
        head_thick.valueChanged.connect(
            lambda v: setattr(s, "head_outline_thickness_px", float(v)))
        self._skel_card.addBodyWidget(head_thick)

        head_outline_color_btn = ColorButton(
            "Head outline colour", s, "head_outline_color",
            with_alpha=True)
        self._skel_card.addBodyWidget(head_outline_color_btn)

        head_outline_adv = _HeadOutlineAdvanced(self.config)
        self._skel_card.addBodyWidget(head_outline_adv)

        def _sync_head_outline(on: bool) -> None:
            head_radius.setVisible(on)
            head_thick.setVisible(on)
            head_outline_color_btn.setVisible(on)
            head_outline_adv.setVisible(on)

        head_outline_sw.toggled.connect(_sync_head_outline)
        _sync_head_outline(bool(getattr(s, "head_outline_enabled", False)))

        self._glow_card = self.add_widget(FeatureCard(
            "Engine glow",
            risk="high",
            help_text=(
                "Writes the glow-object state in <code>client.dll</code>. "
                "Kernel-mode anti-cheat can fingerprint cross-process writes, "
                "so this has more exposure than Box, Skeleton or Chams. "
                "<b>No documented bans from this implementation.</b>"
            ),
        ))

        type_wrap = QWidget(self._glow_card)
        type_wrap.setObjectName("GlowTypeGroup")
        type_lay = QVBoxLayout(type_wrap)
        type_lay.setContentsMargins(0, 0, 0, 0)
        type_lay.setSpacing(4)
        type_title = QLabel("Type", type_wrap)
        type_title.setObjectName("GlowTypeTitle")
        type_title.setProperty("muted", "true")
        type_lay.addWidget(type_title)
        self._glow_type_group = QButtonGroup(type_wrap)
        self._glow_type_group.setExclusive(True)
        cur_type = int(getattr(glow_config, "type", 2))
        if cur_type not in (1, 2):
            cur_type = 2
            try:
                glow_config.type = 2
            except Exception:
                pass
        glow_types = (
            (1, "Visible only",
             "Limits glow to targets projected on-screen."),
            (2, "Always (through walls)",
             "Highlights every alive target regardless of occlusion."),
        )
        for value, label, blurb in glow_types:
            rb = QRadioButton(f"{label} — {blurb}", type_wrap)
            rb.setObjectName("GlowTypeOption")
            rb.setProperty("glow_type_value", value)
            rb.setChecked(value == cur_type)
            rb.toggled.connect(self._on_glow_type_toggled)
            self._glow_type_group.addButton(rb)
            type_lay.addWidget(rb)
        self._glow_card.addBodyWidget(type_wrap)

        glow_op = LabeledSlider(
            "Opacity", lo=0.0, hi=1.0,
            value=float(max(0.0, min(1.0, float(glow_config.opacity)))),
            decimals=2, step=0.05,
            help_text="Glow strength from invisible (0) to full (1).",
        )
        glow_op.valueChanged.connect(
            lambda v: setattr(glow_config, "opacity", float(max(0.0, min(1.0, v)))))
        self._glow_card.addBodyWidget(glow_op)

        glow_rad = LabeledSlider(
            "Distance fade (units)", lo=0.0, hi=4000.0,
            value=float(getattr(glow_config, "radius", 0.0)),
            decimals=0, step=50.0,
            help_text=(
                "Maximum range in game units. 0 keeps glow active at any "
                "distance; 1500 is roughly ten metres."
            ),
        )
        glow_rad.valueChanged.connect(
            lambda v: setattr(glow_config, "radius", float(v)))
        self._glow_card.addBodyWidget(glow_rad)

        glow_fov = LabeledSlider(
            "FOV angle (deg)", lo=0.0, hi=180.0,
            value=float(getattr(glow_config, "fov_deg", 0.0)),
            decimals=0, step=5.0,
            help_text=(
                "Half-angle around the view direction. <b>0 disables the "
                "filter</b>; 45 creates a 90-degree forward cone."
            ),
        )
        glow_fov.valueChanged.connect(
            lambda v: setattr(glow_config, "fov_deg", float(v)))
        self._glow_card.addBodyWidget(glow_fov)

        glow_xhair = LabeledSlider(
            "Near crosshair (px)", lo=0.0, hi=1200.0,
            value=float(getattr(glow_config, "crosshair_radius_px", 0.0)),
            decimals=0, step=20.0,
            help_text=(
                "Screen-space filter around the crosshair. <b>0 disables "
                "the filter</b>; targets outside the radius enter miss grace."
            ),
        )
        glow_xhair.valueChanged.connect(
            lambda v: setattr(glow_config, "crosshair_radius_px", float(v)))
        self._glow_card.addBodyWidget(glow_xhair)

        self._glow_card.addBodyWidget(ColorButton("CT colour", glow_config, "ct_color",
                                                  with_alpha=True))
        self._glow_card.addBodyWidget(ColorButton("T colour", glow_config, "t_color",
                                                  with_alpha=True))
        glow_grace = LabeledSlider(
            "Miss-grace frames", lo=1, hi=120,
            value=int(getattr(glow_config, "miss_grace_frames", 30)),
            decimals=0,
            help_text=(
                "Frames a target stays glowing after leaving a filter. "
                "30 is about half a second at 60 fps; 1 fades immediately."
            ),
        )
        glow_grace.valueChanged.connect(
            lambda v: setattr(glow_config, "miss_grace_frames", int(v)))
        self._glow_card.addBodyWidget(glow_grace)

        self._chams_card = self.add_widget(FeatureCard(
            "3D chams mesh",
            risk="safe",
            help_text=(
                "A hardware-accelerated rigid-body mesh projected from CS2's "
                "view matrix. Each primitive follows its player bone; this is "
                "true 3D overlay geometry, not a 2D silhouette."
            ),
        ))

        chams_tris = LabeledSlider(
            "Quality (target tris/player)", lo=200, hi=10000,
            value=int(getattr(chams_config, "tri_count", 2000)),
            decimals=0, step=100,
            help_text=(
                "Target triangles per player, snapped to a renderer quality "
                "bucket. 200–500 is faceted, 1000–2000 is smooth and 4000+ "
                "trades more GPU work for finer geometry."
            ),
        )
        chams_tris.valueChanged.connect(
            lambda v, config=chams_config: setattr(config, "tri_count", int(v)))
        self._chams_card.addBodyWidget(chams_tris)

        chams_op = LabeledSlider(
            "Opacity (%)", lo=0.0, hi=1.0,
            value=float(getattr(chams_config, "opacity", 0.85)),
            decimals=2, step=0.01,
            help_text=(
                "Team-colour alpha multiplier. Lower values let the skeleton "
                "remain visible through the mesh."
            ),
        )
        chams_op.valueChanged.connect(
            lambda v, config=chams_config: setattr(config, "opacity", float(v)))
        self._chams_card.addBodyWidget(chams_op)

        chams_rim = LabeledSlider(
            "Rim shading", lo=0.0, hi=4.0,
            value=float(getattr(chams_config, "rim_strength", 1.0)),
            decimals=2, step=0.05,
            help_text=(
                "Per-vertex light strength. 0 is flat, 1 is balanced and "
                "2–4 produces a stronger, cell-shaded rim."
            ),
        )
        chams_rim.valueChanged.connect(
            lambda v, config=chams_config: setattr(config, "rim_strength", float(v)))
        self._chams_card.addBodyWidget(chams_rim)

        self._chams_card.addBodyWidget(ColorButton(
            "CT colour", chams_config, "ct_color", with_alpha=True,
        ))
        self._chams_card.addBodyWidget(ColorButton(
            "T colour", chams_config, "t_color", with_alpha=True,
        ))

        self._refresh_wh_visibility()

        wh_card.addBodyWidget(KeybindRow(
            "Wallhack · toggle",
            self.config.wallhack, "hotkey_toggle",
        ))

        n = v.name
        d = v.distance
        text_card = self.add_widget(FeatureCard(
            "Player labels",
            help_text=(
                "Name and distance share the header switch; health keeps its "
                "own display mode. All labels require Wallhack."
            ),
            toggle_target=(n, "enabled"),
        ))
        labels_header = text_card.headerToggle()
        if labels_header is not None:
            labels_header.toggled.connect(
                lambda c, _d=d: setattr(_d, "enabled", bool(c))
            )
        text_card.addBodyWidget(ColorButton("Name colour", n, "color",
                                            with_alpha=True))
        text_card.addBodyWidget(ColorButton("Distance colour", d, "color",
                                            with_alpha=True))

        h = v.health
        health_mode_row = ChoiceRow(
            "Health mode", h, "mode",
            [("off", "Off"),
             ("bar", "Bar"),
             ("percent", "Percent"),
             ("both", "Both")],
            default="bar",
        )
        text_card.addBodyWidget(health_mode_row)
        health_bar_widgets = []
        for label, attr, lo, hi, step in [
            ("Bar width (px)", "bar_width", 1.0, 20.0, 0.5),
            ("Bar offset (px)", "bar_offset", 0.0, 30.0, 0.5),
        ]:
            slider = LabeledSlider(label, lo=lo, hi=hi,
                                   value=float(getattr(h, attr)),
                                   decimals=1, step=step)
            slider.valueChanged.connect(
                lambda v, a=attr: setattr(h, a, float(v)))
            text_card.addBodyWidget(slider)
            health_bar_widgets.append(slider)
        health_color_widgets = []
        for label, attr in [
            ("Health \u2014 high", "high_color"),
            ("Health \u2014 mid", "mid_color"),
            ("Health \u2014 low", "low_color"),
        ]:
            color_button = ColorButton(label, h, attr, with_alpha=True)
            text_card.addBodyWidget(color_button)
            health_color_widgets.append(color_button)

        def _sync_health_mode(mode: str) -> None:
            show_bar = mode in ("bar", "both")
            show_col = mode != "off"
            for w in health_bar_widgets:
                w.setVisible(show_bar)
            for w in health_color_widgets:
                w.setVisible(show_col)

        health_mode_row.changed.connect(_sync_health_mode)
        _sync_health_mode(health_mode_row.value())
        text_card.addBodyWidget(KeybindRow(
            "Labels (name + distance) \u00b7 toggle",
            self.config.visuals.name, "hotkey_toggle",
        ))

        snapline_config = v.snapline
        snap_card = self.add_widget(FeatureCard(
            "Threat lines",
            help_text=(
                "Connects the bottom-centre of the display to each target's "
                "feet for quick directional awareness. Requires Wallhack."
            ),
            toggle_target=(snapline_config, "enabled"),
        ))
        snap_card.addBodyWidget(ColorButton("Snapline colour", snapline_config, "color",
                                            with_alpha=True))
        snap_card.addBodyWidget(KeybindRow(
            "Snapline \u00b7 toggle",
            self.config.visuals.snapline, "hotkey_toggle",
        ))
        self._text_card = text_card
        self._snap_card = snap_card

        wh_card.setGroupRole("parent")
        self._box_card.setGroupRole("child")
        self._skel_card.setGroupRole("child")
        self._glow_card.setGroupRole("child")
        self._chams_card.setGroupRole("child")
        self._text_card.setGroupRole("child")
        self._snap_card.setGroupRole("child")

        self.h2("Aiming aid")
        crosshair_config = v.crosshair
        xhair_card = self.add_widget(FeatureCard(
            "Overlay crosshair",
            help_text=(
                "Draws a static centre marker independently of CS2's native "
                "crosshair."
            ),
            toggle_target=(crosshair_config, "enabled"),
        ))
        for label, attr, lo, hi, step in [
            ("Size (px)", "size", 2.0, 40.0, 0.5),
            ("Gap (px)", "gap", 0.0, 20.0, 0.5),
            ("Thickness (px)", "thickness", 1.0, 4.0, 0.1),
        ]:
            slider = LabeledSlider(label, lo=lo, hi=hi,
                                   value=float(getattr(crosshair_config, attr)),
                                   decimals=1, step=step)
            slider.valueChanged.connect(
                lambda v, a=attr: setattr(crosshair_config, a, float(v)))
            xhair_card.addBodyWidget(slider)
        xhair_card.addBodyWidget(ColorButton("Crosshair colour", crosshair_config, "color",
                                             with_alpha=True))
        xhair_card.addBodyWidget(KeybindRow(
            "Crosshair \u00b7 toggle",
            self.config.visuals.crosshair, "hotkey_toggle",
        ))

        self.h2("Performance HUD")
        self._build_fps_counter_card()

        self.end()

    def _build_fps_counter_card(self) -> None:
        """Build the telemetry master and its four style cards."""
        fc = self.config.fps_counter

        master = self.add_widget(FeatureCard(
            "Telemetry display",
            help_text=(
                "Shows any mix of FPS, network latency and frame-time jitter. "
                "All lines share one position, margin and optional backdrop."
            ),
            toggle_target=(fc, "enabled"),
        ))
        master.addBodyWidget(ChoiceRow(
            "Position", fc, "position",
            [("top_left", "Top-left"),
             ("top_right", "Top-right"),
             ("bottom_left", "Bottom-left"),
             ("bottom_right", "Bottom-right")],
            default="top_left",
        ))
        for label, attr in [("Margin X (px)", "margin_x"),
                            ("Margin Y (px)", "margin_y")]:
            slider = LabeledSlider(
                label, lo=0, hi=60, value=int(getattr(fc, attr, 12)),
                decimals=0,
                help_text="Distance from the selected display corner.",
            )
            slider.valueChanged.connect(
                lambda v, a=attr: setattr(fc, a, int(v)))
            master.addBodyWidget(slider)

        for attr, label, default, help_text in [
            ("show_fps", "Show FPS", True,
             "Primary bold readout. If hidden, the first remaining "
             "metric inherits its emphasis."),
            ("show_ping", "Show ping", False,
             "ICMP latency to Cloudflare's 1.1.1.1, polled every two "
             "seconds. Hidden outside a match; this is not server RTT."),
            ("show_jitter", "Show render jitter", False,
             "Deviation across the last 16 frame times. <b>±1–2 ms</b> "
             "is healthy; <b>±8–20 ms</b> suggests a stall."),
        ]:
            switch = ToggleSwitch()
            switch.setChecked(bool(getattr(fc, attr, default)))
            switch._live_section = fc  # type: ignore[attr-defined]
            switch._live_attr = attr  # type: ignore[attr-defined]
            switch.toggled.connect(
                lambda c, a=attr: self._on_fps_line_toggle(a, bool(c)))
            master.addBodyWidget(LabeledRow(label, switch, help_text=help_text))

        master.addBodyWidget(KeybindRow(
            "FPS counter \u00b7 toggle",
            fc, "hotkey_toggle",
        ))

        self._fps_box_card = self.add_widget(FeatureCard(
            "Shared backdrop",
            help_text=(
                "Wraps all visible statistics in one rounded background for "
                "contrast against the game."
            ),
        ))
        box_sw = ToggleSwitch()
        box_sw.setChecked(bool(getattr(fc, "box_enabled", False)))
        box_sw._live_section = fc  # type: ignore[attr-defined]
        box_sw._live_attr = "box_enabled"  # type: ignore[attr-defined]
        box_sw.toggled.connect(
            lambda c: setattr(fc, "box_enabled", bool(c)))
        self._fps_box_card.addBodyWidget(LabeledRow(
            "Show box", box_sw))
        box_color_btn = ColorButton(
            "Box colour", fc, "box_color", with_alpha=True)
        self._fps_box_card.addBodyWidget(box_color_btn)
        box_alpha_sl = LabeledSlider(
            "Box opacity", lo=0.0, hi=1.0,
            value=float(getattr(fc, "box_alpha", 1.0)),
            decimals=2, step=0.05,
        )
        box_alpha_sl.valueChanged.connect(
            lambda v: setattr(fc, "box_alpha", float(v)))
        self._fps_box_card.addBodyWidget(box_alpha_sl)
        box_pad_sl = LabeledSlider(
            "Box padding (px)", lo=0, hi=24,
            value=int(getattr(fc, "box_padding", 14)),
            decimals=0,
        )
        box_pad_sl.valueChanged.connect(
            lambda v: setattr(fc, "box_padding", int(v)))
        self._fps_box_card.addBodyWidget(box_pad_sl)

        def _sync_fps_box(on: bool) -> None:
            box_color_btn.setVisible(on)
            box_alpha_sl.setVisible(on)
            box_pad_sl.setVisible(on)

        box_sw.toggled.connect(_sync_fps_box)
        _sync_fps_box(bool(getattr(fc, "box_enabled", False)))

        self._fps_fps_card = self.add_widget(FeatureCard(
            "FPS typography",
            help_text=(
                "Styles the primary frame-rate line. It becomes the bold "
                "readout when visible."
            ),
        ))
        font_sl = LabeledSlider(
            "Font size", lo=8, hi=48,
            value=int(getattr(fc, "font_size", 22)),
            decimals=0,
        )
        font_sl.valueChanged.connect(
            lambda v: setattr(fc, "font_size", int(v)))
        self._fps_fps_card.addBodyWidget(font_sl)
        self._fps_fps_card.addBodyWidget(ColorButton(
            "Text colour", fc, "text_color", with_alpha=True))
        text_alpha = LabeledSlider(
            "Text opacity", lo=0.1, hi=1.0,
            value=float(getattr(fc, "text_alpha", 1.0)),
            decimals=2, step=0.05,
        )
        text_alpha.valueChanged.connect(
            lambda v: setattr(fc, "text_alpha", float(v)))
        self._fps_fps_card.addBodyWidget(text_alpha)

        self._fps_ping_card = self.add_widget(FeatureCard(
            "Latency typography",
            help_text="Styles the ICMP round-trip readout polled from 1.1.1.1.",
        ))
        ping_font = LabeledSlider(
            "Font size", lo=8, hi=36,
            value=int(getattr(fc, "ping_font_size", 16)),
            decimals=0,
        )
        ping_font.valueChanged.connect(
            lambda v: setattr(fc, "ping_font_size", int(v)))
        self._fps_ping_card.addBodyWidget(ping_font)
        self._fps_ping_card.addBodyWidget(ColorButton(
            "Text colour", fc, "ping_color", with_alpha=True))
        ping_alpha = LabeledSlider(
            "Text opacity", lo=0.1, hi=1.0,
            value=float(getattr(fc, "ping_alpha", 1.0)),
            decimals=2, step=0.05,
        )
        ping_alpha.valueChanged.connect(
            lambda v: setattr(fc, "ping_alpha", float(v)))
        self._fps_ping_card.addBodyWidget(ping_alpha)

        self._fps_jitter_card = self.add_widget(FeatureCard(
            "Jitter typography",
            help_text=(
                "Styles the ±ms frame-time deviation readout so render "
                "instability stays easy to spot."
            ),
        ))
        jitter_font = LabeledSlider(
            "Font size", lo=8, hi=36,
            value=int(getattr(fc, "jitter_font_size", 16)),
            decimals=0,
        )
        jitter_font.valueChanged.connect(
            lambda v: setattr(fc, "jitter_font_size", int(v)))
        self._fps_jitter_card.addBodyWidget(jitter_font)
        self._fps_jitter_card.addBodyWidget(ColorButton(
            "Text colour", fc, "jitter_color", with_alpha=True))
        jitter_alpha = LabeledSlider(
            "Text opacity", lo=0.1, hi=1.0,
            value=float(getattr(fc, "jitter_alpha", 1.0)),
            decimals=2, step=0.05,
        )
        jitter_alpha.valueChanged.connect(
            lambda v: setattr(fc, "jitter_alpha", float(v)))
        self._fps_jitter_card.addBodyWidget(jitter_alpha)

        self._refresh_fps_visibility()

        master.setGroupRole("parent")
        self._fps_fps_card.setGroupRole("child")
        self._fps_ping_card.setGroupRole("child")
        self._fps_jitter_card.setGroupRole("child")
        self._fps_box_card.setGroupRole("child")

    def _on_fps_line_toggle(self, attr: str, checked: bool) -> None:
        """Write a telemetry-line toggle and refresh its style card."""
        try:
            setattr(self.config.fps_counter, attr, bool(checked))
        except Exception as e:
            print(f"[esp] set fps_counter.{attr}={checked!r} failed: {e!r}")
        try:
            self.config.save()
        except Exception as e:
            print(f"[esp] explicit save after FPS toggle failed: {e!r}")
        self._refresh_fps_visibility()

    def _refresh_fps_visibility(self) -> None:
        """Match telemetry style-card visibility to its line toggles."""
        fc = self.config.fps_counter
        try:
            self._fps_fps_card.setVisible(bool(getattr(fc, "show_fps", True)))
            self._fps_ping_card.setVisible(bool(getattr(fc, "show_ping", False)))
            self._fps_jitter_card.setVisible(
                bool(getattr(fc, "show_jitter", False)))
        except Exception:
            pass

    def _on_wh_toggle(self, which: str, checked: bool) -> None:
        section = {
            "box": self.config.visuals.box,
            "skeleton": self.config.visuals.skeleton,
            "glow": self.config.glow,
            "chams": self.config.visuals.chams,
        }[which]
        try:
            section.enabled = bool(checked)
        except Exception as e:
            print(f"[esp] set {which}.enabled={checked!r} failed: {e!r}")
        try:
            self.config.save()
        except Exception as e:
            print(f"[esp] explicit save after WH toggle failed: {e!r}")
        self._refresh_wh_visibility()

    def _on_glow_type_toggled(self, checked: bool) -> None:
        """Persist the selected glow type from its checked radio."""
        if not checked:
            return
        sender = self.sender()
        value = sender.property("glow_type_value")
        if value is None:
            return
        try:
            self.config.glow.type = int(value)
        except Exception as e:
            print(f"[esp] set glow.type={value!r} failed: {e!r}")
            return
        try:
            self.config.save()
        except Exception as e:
            print(f"[esp] explicit save after glow.type pick failed: {e!r}")

    def _refresh_wh_visibility(self) -> None:
        """Show style cards only for enabled player-overlay primitives."""
        self._box_card.setVisible(bool(self.config.visuals.box.enabled))
        self._skel_card.setVisible(bool(self.config.visuals.skeleton.enabled))
        self._glow_card.setVisible(bool(self.config.glow.enabled))
        try:
            chams_on = bool(getattr(self.config.visuals.chams, "enabled", False))
            self._chams_card.setVisible(chams_on)
        except AttributeError:
            self._chams_card.setVisible(False)


__all__ = ["ESPPanel"]
