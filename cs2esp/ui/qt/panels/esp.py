"""
ESP overlay controls and feature-specific styling.
"""

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
	QButtonGroup,
	QDoubleSpinBox,
	QFrame,
	QHBoxLayout,
	QLabel,
	QLineEdit,
	QPushButton,
	QRadioButton,
	QSizePolicy,
	QSlider,
	QVBoxLayout,
	QWidget,
)

from cs2esp.risk import bind_risk, capability_for_setting
from cs2esp.ui.qt.icons import HAS_ICONS, icon
from cs2esp.ui.qt.panels.base import PanelBase
from cs2esp.ui.qt.widgets.labeled import SoundRow
from cs2esp.ui.qt.widgets.choice import _ChevronCombo
from cs2esp.ui.qt.widgets import ChoiceRow, ColorButton, ComboRow, FeatureCard, KeybindRow, LabeledRow, LabeledSlider, ToggleSwitch


_HUD_COMPONENT_KEYS = {"show_hotkeys": "hotkeys", "show_status_pills": "status_pills", "show_spectators": "spectators", "show_c4_timer": "c4_capsule"}


def _live_toggle(section, attr: str, checked: bool, on_change=None) -> ToggleSwitch:
	'''Create a toggle bound to one config attribute and tagged for live refresh.'''
	switch = ToggleSwitch()
	switch.setChecked(bool(checked))
	switch._live_section = section
	switch._live_attr = attr
	if on_change is None:
		switch.toggled.connect(lambda value: setattr(section, attr, bool(value)))
	else:
		switch.toggled.connect(lambda value: on_change(bool(value)))
	return switch


class _HeadOutlineAdvanced(QFrame):
	"""Collapsible geometry controls for the skeleton head outline."""
	def __init__(self, config, parent: QWidget = None) -> None:
		super().__init__(parent)
		self.setObjectName("HeadOutlineAdvanced")
		self.setFrameShape(QFrame.NoFrame)
		self._skeleton_config = config.visuals.skeleton
		self._expanded = False

		outer = QVBoxLayout(self)
		outer.setContentsMargins(0, 6, 0, 0)
		outer.setSpacing(8)

		header = QWidget(self)
		header.setObjectName("HeadAdvHeader")
		hdr = QHBoxLayout(header)
		hdr.setContentsMargins(0, 0, 0, 0)
		hdr.setSpacing(8)
		self._chevron = QPushButton("\u25b8 Head outline geometry", header)
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

		skeleton_config = self._skeleton_config
		is_auto = bool(getattr(skeleton_config, "head_outline_offset_right_auto", True))

		mode_title = QLabel("Offset mode", self._body)
		mode_title.setObjectName("HeadAdvModeTitle")
		mode_title.setProperty("muted", "true")
		body_lay.addWidget(mode_title)

		self._mode_group = QButtonGroup(self._body)
		self._mode_group.setExclusive(True)
		self._rb_auto = QRadioButton("Automatic — follows the view angle using one magnitude", self._body)
		self._rb_anchor = QRadioButton("Manual anchor — shifts from the screen or target axis", self._body)
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
			"Magnitude",
			lo=-3.0,
			hi=3.0,
			value=float(getattr(skeleton_config, "head_outline_offset_right_magnitude", 1.0)),
			decimals=2,
			step=0.05,
			help_text=("Automatic offset range across view angles. 1.00 is the calibrated baseline."),
		)
		head_magnitude.valueChanged.connect(lambda v: setattr(skeleton_config, "head_outline_offset_right_magnitude", float(v)))
		auto_lay.addWidget(head_magnitude)
		body_lay.addWidget(self._auto_section)

		self._anchor_section = QWidget(self._body)
		self._anchor_section.setObjectName("HeadAdvModeSection")
		self._anchor_section.setProperty("mode", "manual")
		anchor_lay = QVBoxLayout(self._anchor_section)
		anchor_lay.setContentsMargins(16, 0, 0, 0)
		anchor_lay.setSpacing(6)
		anchor_sw = _live_toggle(skeleton_config, "head_outline_anchor_to_screen", getattr(skeleton_config, "head_outline_anchor_to_screen", False))
		anchor_lay.addWidget(LabeledRow("Anchor to screen", anchor_sw, help_text=("On shifts along screen-right. Off uses the target's backward body axis.")))
		head_off_right = LabeledSlider(
			"Horizontal offset",
			lo=-3.0,
			hi=3.0,
			value=float(getattr(skeleton_config, "head_outline_offset_right", 0.0)),
			decimals=2,
			step=0.05,
			help_text="Manual shift along the selected anchor axis.",
		)
		head_off_right.valueChanged.connect(lambda v: setattr(skeleton_config, "head_outline_offset_right", float(v)))
		anchor_lay.addWidget(head_off_right)
		body_lay.addWidget(self._anchor_section)

		head_w_mul = LabeledSlider(
			"Width scale",
			lo=0.2,
			hi=3.0,
			value=float(getattr(skeleton_config, "head_outline_width_mul", 1.0)),
			decimals=2,
			step=0.05,
			help_text=("Horizontal radius multiplier. 1.0 keeps the base shape."),
		)
		head_w_mul.valueChanged.connect(lambda v: setattr(skeleton_config, "head_outline_width_mul", float(v)))
		body_lay.addWidget(head_w_mul)

		head_h_mul = LabeledSlider(
			"Height scale",
			lo=0.2,
			hi=3.0,
			value=float(getattr(skeleton_config, "head_outline_height_mul", 1.0)),
			decimals=2,
			step=0.05,
			help_text=("Vertical radius multiplier. Lower values flatten the ring."),
		)
		head_h_mul.valueChanged.connect(lambda v: setattr(skeleton_config, "head_outline_height_mul", float(v)))
		body_lay.addWidget(head_h_mul)

		head_rot = LabeledSlider(
			"Rotation",
			lo=-180.0,
			hi=180.0,
			value=float(getattr(skeleton_config, "head_outline_rotation_deg", 0.0)),
			decimals=0,
			step=1.0,
			help_text=("Counter-clockwise ellipse rotation in degrees."),
		)
		head_rot.valueChanged.connect(lambda v: setattr(skeleton_config, "head_outline_rotation_deg", float(v)))
		body_lay.addWidget(head_rot)

		head_off_up = LabeledSlider(
			"Vertical offset",
			lo=-12.0,
			hi=12.0,
			value=float(getattr(skeleton_config, "head_outline_offset_up", 0.0)),
			decimals=1,
			step=0.2,
			help_text=("Shifts the ring in world units. +4 to +6 usually centres it on the face."),
		)
		head_off_up.valueChanged.connect(lambda v: setattr(skeleton_config, "head_outline_offset_up", float(v)))
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
			setattr(self._skeleton_config, "head_outline_offset_right_auto", bool(is_auto))
		except Exception:
			pass
		self._refresh_mode_sections()

	def _refresh_mode_sections(self) -> None:
		is_auto = self._rb_auto.isChecked()
		self._auto_section.setVisible(is_auto)
		self._anchor_section.setVisible(not is_auto)


class ESPPanel(PanelBase):
	"""Player-overlay and HUD settings."""
	RESET_SCOPES = {
		"Box geometry": ("visuals.box",), "Skeleton geometry": ("visuals.skeleton",), "Engine glow": ("glow",), "Chams": ("visuals.chams",),
		"Player labels": ("visuals.name", "visuals.distance", "visuals.health"), "Threat lines": ("visuals.snapline",),
		"Overlay crosshair": ("visuals.crosshair",), "Telemetry watermark": ("fps_counter",), "Sound Effects": ("event_sounds",),
		"HUD & Event Suite": (
			"event_visuals.theme", "event_visuals.custom_color", "event_visuals.corner_radius", "event_visuals.bloom_intensity",
			"event_visuals.shadow_intensity", "event_visuals.icon_outline_width", "event_visuals.icon_accent_color", "event_visuals.icon_text_color",
		),
	}

	def hud_component_target(self, widget_id: str) -> QWidget | None:
		'''Return the existing row for a selectable HUD component, not a second editor.'''
		return getattr(self, "_hud_targets", {}).get(widget_id)

	def select_hud_component(self, widget_id: str) -> bool:
		'''Expand the card containing this component's controls while retaining the original widgets and callbacks.'''
		target = self.hud_component_target(widget_id)
		if target is None:
			return False
		self._hud_component = widget_id
		parent = target.parentWidget()
		while parent is not None and parent is not self:
			if isinstance(parent, FeatureCard):
				parent.setCollapsed(False, animate=False)
			parent = parent.parentWidget()
		target.setProperty("hudComponent", widget_id)
		return True

	def __init__(self, config, parent: QWidget = None) -> None:
		super().__init__(config, parent)

		self._hud_component: str = ""
		self._hud_targets: dict[str, QWidget] = {}
		visuals_config = self.config.visuals
		glow_config = self.config.glow

		self.h1("ESP")
		self.intro(
			"Player ESP uses externally read game state. Native Glow also writes game memory; both Chams modes draw in this app's overlay. "
			"Badges show qualitative VAC estimates; FACEIT, confidence and sources are in help."
		)

		self.h2("Player overlays")
		wallhack_config = self.config.wallhack
		wh_card = self.add_widget(
			FeatureCard(
				"Wallhack master",
				risk=bind_risk('wallhack', lambda: self.config),
				help_text=(
					"Turns every player visual off together while preserving "
					"the mix below. Each primitive reveals its own styling "
					"when enabled; the inline hotkey controls this same master."
				),
				toggle_target=(wallhack_config, "enabled"),
			)
		)

		self._wh_box_sw = _live_toggle(
			visuals_config.box, "enabled", visuals_config.box.enabled, on_change=lambda checked: self._on_wh_toggle("box", checked)
		)
		wh_card.addBodyWidget(
			LabeledRow("Box outline", self._wh_box_sw, help_text=("Draws a fitted player box. Geometry, fill and colour controls appear below when enabled."))
		)

		self._wh_skel_sw = _live_toggle(
			visuals_config.skeleton, "enabled", visuals_config.skeleton.enabled, on_change=lambda checked: self._on_wh_toggle("skeleton", checked)
		)
		wh_card.addBodyWidget(
			LabeledRow("Skeleton wireframe", self._wh_skel_sw, help_text=("Projects the bone matrix and connects each segment into a lightweight wireframe."))
		)

		self._wh_glow_sw = _live_toggle(
			glow_config, "enabled", glow_config.enabled, on_change=lambda checked: self._on_wh_toggle("glow", checked)
		)
		wh_card.addBodyWidget(
			LabeledRow(
				"Glow",
				self._wh_glow_sw,
				risk=bind_risk('glow', lambda: self.config),
				help_text=(
					"Writes pawn <code>CGlowProperty</code> color, type, timers and highlight flags. Normal scheduling has a write guard; "
					"cleanup can still write. Detector outcomes are not publicly verifiable."
				),
			)
		)

		chams_config = visuals_config.chams
		self._wh_chams_sw = _live_toggle(
			chams_config, "enabled", getattr(chams_config, "enabled", False), on_change=lambda checked: self._on_wh_toggle("chams", checked)
		)
		wh_card.addBodyWidget(
			LabeledRow(
				"Chams",
				self._wh_chams_sw,
				risk=bind_risk('chams', lambda: self.config),
				help_text=(
					'Draws external bone poses as exact VPK models or legacy primitives in the app overlay. Neither mode writes game memory. '
					'Capture exclusion does not certify this information feature or prevent every capture path.'
				),
			)
		)

		self._box_card = self.add_widget(
			FeatureCard(
				"Box geometry",
				risk=bind_risk('box', lambda: self.config),
				help_text=("Controls the fitted outline, fill and colours. Team mode selects CT or T colour from each player's team."),
			)
		)
		box_config = visuals_config.box
		self._box_card.addBodyWidget(
			ChoiceRow("Sizing", box_config, "box_mode", [("bones", "Bones (accurate)"), ("bounds", "Bounds (hull)"), ("fixed", "Fixed")], default="bones")
		)
		self._box_card.addBodyWidget(ChoiceRow("Style", box_config, "style", [("outline", "Outline"), ("filled", "Filled"), ("corners", "Corners")], default="outline"))
		thickness_sl = LabeledSlider(
			"Thickness", lo=0.5, hi=4.0, value=float(box_config.thickness), decimals=1, step=0.1, help_text="Pixel thickness of the outline. 1.0 is the default."
		)
		thickness_sl.valueChanged.connect(lambda v: setattr(box_config, "thickness", float(v)))
		self._box_card.addBodyWidget(thickness_sl)
		box_color_btn = ColorButton("Box colour", box_config, "color", with_alpha=True)
		self._box_card.addBodyWidget(box_color_btn)

		team_sw = ToggleSwitch()
		team_sw.setChecked(bool(getattr(box_config, "team_colored", False)))
		team_sw.toggled.connect(lambda c: setattr(box_config, "team_colored", bool(c)))
		self._box_card.addBodyWidget(LabeledRow("Team-coloured (CT / T)", team_sw, help_text=("Use CT-blue / T-red instead of the single colour above.")))
		box_ct_btn = ColorButton("CT colour", box_config, "ct_color", with_alpha=True)
		box_t_btn = ColorButton("T colour", box_config, "t_color", with_alpha=True)
		self._box_card.addBodyWidget(box_ct_btn)
		self._box_card.addBodyWidget(box_t_btn)

		def _sync_box_team(on: bool) -> None:
			box_color_btn.setVisible(not on)
			box_ct_btn.setVisible(on)
			box_t_btn.setVisible(on)

		team_sw.toggled.connect(_sync_box_team)
		_sync_box_team(bool(getattr(box_config, "team_colored", False)))

		self._skel_card = self.add_widget(
			FeatureCard("Skeleton geometry", risk=bind_risk('skeleton', lambda: self.config), help_text="Controls wireframe weight, colour and head marker.")
		)
		skeleton_config = visuals_config.skeleton
		skel_thick = LabeledSlider(
			"Line thickness", lo=0.5, hi=3.0, value=float(skeleton_config.thickness), decimals=1, step=0.1, help_text="Pixel thickness of the bone segments."
		)
		skel_thick.valueChanged.connect(lambda v: setattr(skeleton_config, "thickness", float(v)))
		self._skel_card.addBodyWidget(skel_thick)
		self._skel_card.addBodyWidget(ColorButton("Skeleton colour", skeleton_config, "color", with_alpha=True))

		head_outline_sw = _live_toggle(skeleton_config, "head_outline_enabled", getattr(skeleton_config, "head_outline_enabled", False))
		self._skel_card.addBodyWidget(
			LabeledRow(
				"Head outline",
				head_outline_sw,
				help_text=("Draws a projected ellipse around the head bone. It follows the player axes and scales naturally with distance."),
			)
		)
		head_radius = LabeledSlider(
			"Head outline radius (units)",
			lo=4.0,
			hi=20.0,
			value=float(getattr(skeleton_config, "head_outline_radius_units", 9.0)),
			decimals=1,
			step=0.5,
			help_text=("World-space ring radius in CS2 units. 9 is a close fit on the standard player model."),
		)
		head_radius.valueChanged.connect(lambda v: setattr(skeleton_config, "head_outline_radius_units", float(v)))
		self._skel_card.addBodyWidget(head_radius)
		head_thick = LabeledSlider(
			"Head outline thickness",
			lo=0.5,
			hi=4.0,
			value=float(getattr(skeleton_config, "head_outline_thickness_px", 0.5)),
			decimals=1,
			step=0.1,
			help_text="Pixel thickness of the head-outline ring.",
		)
		head_thick.valueChanged.connect(lambda v: setattr(skeleton_config, "head_outline_thickness_px", float(v)))
		self._skel_card.addBodyWidget(head_thick)

		head_outline_color_btn = ColorButton("Head outline colour", skeleton_config, "head_outline_color", with_alpha=True)
		self._skel_card.addBodyWidget(head_outline_color_btn)

		head_outline_adv = _HeadOutlineAdvanced(self.config)
		self._skel_card.addBodyWidget(head_outline_adv)

		def _sync_head_outline(on: bool) -> None:
			head_radius.setVisible(on)
			head_thick.setVisible(on)
			head_outline_color_btn.setVisible(on)
			head_outline_adv.setVisible(on)

		head_outline_sw.toggled.connect(_sync_head_outline)
		_sync_head_outline(bool(getattr(skeleton_config, "head_outline_enabled", False)))

		self._glow_card = self.add_widget(
			FeatureCard(
				"Engine glow",
				risk=bind_risk('glow', lambda: self.config),
				help_text=(
					"Writes the pawn's native glow state in <code>client.dll</code>. This is distinct from overlay-only Box, Skeleton and Chams. "
					"Memory-write behavior is known; a comparative ban probability is not."
				),
			)
		)

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
			(1, "Visible only", "Limits glow to targets projected on-screen."),
			(2, "Always (through walls)", "Highlights every alive target regardless of occlusion."),
		)
		for value, label, blurb in glow_types:
			glow_radio = QRadioButton(f"{label} — {blurb}", type_wrap)
			glow_radio.setObjectName("GlowTypeOption")
			glow_radio.setProperty("glow_type_value", value)
			glow_radio.setChecked(value == cur_type)
			glow_radio.toggled.connect(self._on_glow_type_toggled)
			self._glow_type_group.addButton(glow_radio)
			type_lay.addWidget(glow_radio)
		self._glow_card.addBodyWidget(type_wrap)

		glow_op = LabeledSlider(
			"Opacity",
			lo=0.0,
			hi=1.0,
			value=float(max(0.0, min(1.0, float(glow_config.opacity)))),
			decimals=2,
			step=0.05,
			help_text="Glow strength from invisible (0) to full (1).",
		)
		glow_op.valueChanged.connect(lambda v: setattr(glow_config, "opacity", float(max(0.0, min(1.0, v)))))
		self._glow_card.addBodyWidget(glow_op)

		glow_rad = LabeledSlider(
			"Distance fade (units)",
			lo=0.0,
			hi=4000.0,
			value=float(getattr(glow_config, "radius", 0.0)),
			decimals=0,
			step=50.0,
			help_text=("Maximum range in game units. 0 keeps glow active at any distance; 1500 is roughly ten metres."),
		)
		glow_rad.valueChanged.connect(lambda v: setattr(glow_config, "radius", float(v)))
		self._glow_card.addBodyWidget(glow_rad)

		glow_fov = LabeledSlider(
			"FOV angle (deg)",
			lo=0.0,
			hi=180.0,
			value=float(getattr(glow_config, "fov_deg", 0.0)),
			decimals=0,
			step=5.0,
			help_text=("Half-angle around the view direction. <b>0 disables the filter</b>; 45 creates a 90-degree forward cone."),
		)
		glow_fov.valueChanged.connect(lambda v: setattr(glow_config, "fov_deg", float(v)))
		self._glow_card.addBodyWidget(glow_fov)

		glow_xhair = LabeledSlider(
			"Near crosshair (px)",
			lo=0.0,
			hi=1200.0,
			value=float(getattr(glow_config, "crosshair_radius_px", 0.0)),
			decimals=0,
			step=20.0,
			help_text=("Screen-space filter around the crosshair. <b>0 disables the filter</b>; targets outside the radius enter miss grace."),
		)
		glow_xhair.valueChanged.connect(lambda v: setattr(glow_config, "crosshair_radius_px", float(v)))
		self._glow_card.addBodyWidget(glow_xhair)

		self._glow_card.addBodyWidget(ColorButton("CT colour", glow_config, "ct_color", with_alpha=True))
		self._glow_card.addBodyWidget(ColorButton("T colour", glow_config, "t_color", with_alpha=True))
		glow_grace = LabeledSlider(
			"Miss-grace frames",
			lo=1,
			hi=120,
			value=int(getattr(glow_config, "miss_grace_frames", 30)),
			decimals=0,
			help_text=("Frames a target stays glowing after leaving a filter. 30 is about half a second at 60 fps; 1 fades immediately."),
		)
		glow_grace.valueChanged.connect(lambda v: setattr(glow_config, "miss_grace_frames", int(v)))
		self._glow_card.addBodyWidget(glow_grace)

		self._chams_card = self.add_widget(
			FeatureCard(
				"Chams",
				risk=bind_risk('chams', lambda: self.config),
				help_text=(
					'Exact models reads local VPK agent meshes and external bone poses; Legacy builds bone-following primitives. Both draw in '
					'this app overlay without game-memory writes. No rendering mode establishes detector coverage.'
				),
			)
		)

		chams_mode = ChoiceRow("Mode", chams_config, "mode", [("vpk", "Exact models (VPK)"), ("legacy", "Legacy chams")], default="vpk")
		self._chams_card.addBodyWidget(chams_mode)

		chams_tris = LabeledSlider(
			"Quality (target tris/player)",
			lo=200,
			hi=10000,
			value=int(getattr(chams_config, "tri_count", 2000)),
			decimals=0,
			step=100,
			help_text=(
				"Target triangles per player, snapped to a renderer quality "
				"bucket. 200–500 is faceted, 1000–2000 is smooth and 4000+ "
				"trades more GPU work for finer geometry."
			),
		)
		chams_tris.valueChanged.connect(lambda v, config=chams_config: setattr(config, "tri_count", int(v)))
		self._chams_card.addBodyWidget(chams_tris)

		chams_op = LabeledSlider(
			"Opacity",
			lo=0.0,
			hi=1.0,
			value=float(getattr(chams_config, "opacity", 0.85)),
			decimals=2,
			step=0.01,
			help_text=("Team-colour alpha multiplier. Lower values let the skeleton remain visible through the mesh."),
		)
		chams_op.valueChanged.connect(lambda v, config=chams_config: setattr(config, "opacity", float(v)))
		self._chams_card.addBodyWidget(chams_op)

		chams_rim = LabeledSlider(
			"Rim shading",
			lo=0.0,
			hi=4.0,
			value=float(getattr(chams_config, "rim_strength", 1.0)),
			decimals=2,
			step=0.05,
			help_text=("Per-vertex light strength. 0 is flat, 1 is balanced and 2–4 produces a stronger, cell-shaded rim."),
		)
		chams_rim.valueChanged.connect(lambda v, config=chams_config: setattr(config, "rim_strength", float(v)))
		self._chams_card.addBodyWidget(chams_rim)

		self._chams_card.addBodyWidget(ColorButton("CT colour", chams_config, "ct_color", with_alpha=True))
		self._chams_card.addBodyWidget(ColorButton("T colour", chams_config, "t_color", with_alpha=True))

		# Triangle budget only shapes the legacy primitive mesh
		def _sync_chams_mode(mode: str) -> None:
			chams_tris.setVisible(mode == "legacy")

		chams_mode.changed.connect(_sync_chams_mode)
		_sync_chams_mode(chams_mode.value())

		self._refresh_wh_visibility()

		wh_card.addBodyWidget(KeybindRow("Wallhack · toggle", self.config.wallhack, "hotkey_toggle"))

		name_config = visuals_config.name
		distance_config = visuals_config.distance
		text_card = self.add_widget(
			FeatureCard(
				"Player labels",
				risk=bind_risk('labels_group', lambda: self.config),
				help_text=("Name and distance share the header switch; health keeps its own display mode. All labels require Wallhack."),
				toggle_target=(name_config, "enabled"),
			)
		)
		labels_header = text_card.headerToggle()
		if labels_header is not None:
			labels_header.toggled.connect(lambda c, _d=distance_config: setattr(_d, "enabled", bool(c)))
		text_card.addBodyWidget(ColorButton("Name colour", name_config, "color", with_alpha=True))
		weapon_toggle = ToggleSwitch()
		weapon_toggle.setChecked(bool(getattr(name_config, "weapon_enabled", False)))
		weapon_toggle.toggled.connect(lambda checked: setattr(name_config, "weapon_enabled", bool(checked)))
		weapon_row = LabeledRow("Active weapon icon", weapon_toggle,
			help_text="Show the weapon currently held beside the enemy name. Updates when they switch weapons; follows the Player labels switch.")
		weapon_size = LabeledSlider("Weapon icon width (px)", lo=16.0, hi=64.0,
			value=float(getattr(name_config, "weapon_size", 32.0)), decimals=0, step=1.0)
		weapon_size.valueChanged.connect(lambda value: setattr(name_config, "weapon_size", float(value)))
		text_card.addBodyWidget(ColorButton("Distance colour", distance_config, "color", with_alpha=True))

		health_config = visuals_config.health
		health_mode_row = ChoiceRow("Health mode", health_config, "mode", [("off", "Off"), ("bar", "Bar"), ("percent", "Percent"), ("both", "Both")], default="bar")
		text_card.addBodyWidget(health_mode_row)
		health_bar_widgets = []
		for label, attr, minimum, maximum, step in [("Bar width (px)", "bar_width", 1.0, 20.0, 0.5), ("Bar offset (px)", "bar_offset", 0.0, 30.0, 0.5)]:
			slider = LabeledSlider(label, lo=minimum, hi=maximum, value=float(getattr(health_config, attr)), decimals=1, step=step)
			slider.valueChanged.connect(lambda v, a=attr: setattr(health_config, a, float(v)))
			text_card.addBodyWidget(slider)
			health_bar_widgets.append(slider)
		health_color_widgets = []
		for label, attr in [("Health \u2014 high", "high_color"), ("Health \u2014 mid", "mid_color"), ("Health \u2014 low", "low_color")]:
			color_button = ColorButton(label, health_config, attr, with_alpha=True)
			text_card.addBodyWidget(color_button)
			health_color_widgets.append(color_button)

		def _sync_health_mode(mode: str) -> None:
			show_bar = mode in ("bar", "both")
			show_col = mode != "off"
			for widget in health_bar_widgets:
				widget.setVisible(show_bar)
			for widget in health_color_widgets:
				widget.setVisible(show_col)

		health_mode_row.changed.connect(_sync_health_mode)
		_sync_health_mode(health_mode_row.value())
		text_card.addBodyWidget(weapon_row)
		text_card.addBodyWidget(weapon_size)
		text_card.addBodyWidget(KeybindRow("Labels (name + distance) \u00b7 toggle", self.config.visuals.name, "hotkey_toggle"))

		snapline_config = visuals_config.snapline
		snap_card = self.add_widget(
			FeatureCard(
				"Threat lines",
				risk=bind_risk('snapline', lambda: self.config),
				help_text=("Connects the bottom-centre of the display to each target's feet for quick directional awareness. Requires Wallhack."),
				toggle_target=(snapline_config, "enabled"),
			)
		)
		snap_color_btn = ColorButton("Snapline colour", snapline_config, "color", with_alpha=True)
		snap_card.addBodyWidget(snap_color_btn)
		try:
			snap_rgba = list(snapline_config.color)
			snap_alpha_value = float(snap_rgba[3]) if len(snap_rgba) >= 4 else 1.0
		except (TypeError, ValueError):
			snap_alpha_value = 1.0
		snap_alpha = LabeledSlider(
			"Snapline alpha",
			lo=0.0,
			hi=1.0,
			value=max(0.0, min(1.0, snap_alpha_value)),
			decimals=2,
			step=0.05,
			help_text="Line opacity from invisible (0) to solid (1).",
		)

		def _set_snapline_alpha(value):
			rgba = list(getattr(snapline_config, "color", (1.0, 1.0, 1.0, 1.0)))
			while len(rgba) < 4:
				rgba.append(1.0)
			rgba[3] = max(0.0, min(1.0, float(value)))
			snapline_config.color = tuple(rgba[:4])
			snap_color_btn.refresh()

		snap_alpha.valueChanged.connect(_set_snapline_alpha)
		snap_color_btn.changed.connect(lambda rgba: snap_alpha.setValue(rgba[3] if len(rgba) >= 4 else 1.0))
		snap_card.addBodyWidget(snap_alpha)
		snap_card.addBodyWidget(KeybindRow("Snapline \u00b7 toggle", self.config.visuals.snapline, "hotkey_toggle"))
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
		crosshair_config = visuals_config.crosshair
		xhair_card = self.add_widget(
			FeatureCard(
				"Overlay crosshair",
				risk=bind_risk('crosshair', lambda: self.config),
				help_text=("Draws a static centre marker independently of CS2's native crosshair."),
				toggle_target=(crosshair_config, "enabled"),
			)
		)
		for label, attr, minimum, maximum, step in [
			("Size (px)", "size", 2.0, 40.0, 0.5),
			("Gap (px)", "gap", 0.0, 20.0, 0.5),
			("Thickness (px)", "thickness", 1.0, 4.0, 0.1),
		]:
			slider = LabeledSlider(label, lo=minimum, hi=maximum, value=float(getattr(crosshair_config, attr)), decimals=1, step=step)
			slider.valueChanged.connect(lambda v, a=attr: setattr(crosshair_config, a, float(v)))
			xhair_card.addBodyWidget(slider)
		xhair_card.addBodyWidget(ColorButton("Crosshair colour", crosshair_config, "color", with_alpha=True))
		xhair_card.addBodyWidget(KeybindRow("Crosshair \u00b7 toggle", self.config.visuals.crosshair, "hotkey_toggle"))

		self.h2("Performance HUD")
		self._build_fps_counter_card()

		self.h2("HUD & Event Visuals")
		self._build_event_visuals_card()

		self.h2("Sound Effects")
		snd_card = FeatureCard(
			"Sound Effects",
			risk=bind_risk('event_sounds', lambda: self.config),
			help_text="Short app-style chimes for overlay events.",
			toggle_target=(getattr(self.config, "event_sounds", None), "enabled"),
		)
		self.add_widget(snd_card)
		if getattr(self.config, "event_sounds", None) is not None:
			_snd = self.config.event_sounds
			_vol = LabeledSlider(
				"Overall Volume", lo=0.0, hi=1.0, value=float(getattr(_snd, "volume", 0.7)), decimals=2, step=0.05, help_text="Loudness for every event cue."
			)
			_vol.valueChanged.connect(lambda v: setattr(_snd, "volume", float(v)))
			snd_card.addBodyWidget(_vol)

			from cs2esp.features.event_sounds import EventSounds, available_sound_files

			_files = available_sound_files()
			_options = [("", "None (muted)")] + [(name, name) for name in _files]
			_default_order = ["hit", "kill", "one_tap", "headshot_hit", "headshot_kill", "miss", "bomb", "spectator", "system", "toggle"]
			_labels = {
				"hit": "Hit (body)",
				"headshot_hit": "Headshot hit",
				"kill": "Kill (body)",
				"one_tap": "One-tap kill",
				"headshot_kill": "Headshot kill",
				"miss": "Miss",
				"bomb": "Bomb",
				"spectator": "Spectator",
				"system": "System",
				"toggle": "Hotkey toggle",
			}
			try:
				_saved_order = list(getattr(_snd, "ui_order", []) or [])
			except (TypeError, ValueError):
				_saved_order = []
			_order = []
			for family in _saved_order:
				if family in _labels and family not in _order:
					_order.append(family)
			for family in _default_order:
				if family not in _order:
					_order.append(family)

			_missing = {str(getattr(_snd, f"{fam}_file", "") or "").strip() for fam in _default_order} - set(_files) - {""}
			for name in sorted(_missing):
				_options.append((name, f"{name} (missing)"))

			try:
				_preview = self._sound_preview
			except AttributeError:
				_preview = EventSounds(self.config)
				self._sound_preview = _preview

			_play_icon = icon("mdi.play", color="#DCE7F3", color_disabled="#59616D") if HAS_ICONS else None
			_stop_icon = icon("mdi.stop", color="#FF8B8B", color_disabled="#59616D") if HAS_ICONS else None
			_up_icon = icon("mdi.chevron-up", color="#DCE7F3") if HAS_ICONS else None
			_down_icon = icon("mdi.chevron-down", color="#DCE7F3") if HAS_ICONS else None
			_preview_state: dict[str, object] = {"active": None, "token": 0}

			def _set_glyph(button: QPushButton, graphic, fallback: str) -> None:
				if graphic is not None:
					button.setIcon(graphic)
					button.setText("")
				else:
					button.setText(fallback)

			def _reset_preview_visuals() -> None:
				for play_btn in _preview_buttons:
					_set_glyph(play_btn, _play_icon, "▶")
					play_btn.setProperty("playing", False)
					play_btn.setToolTip("Play the selected sound")
				_preview_state["active"] = None

			def _toggle_preview(family: str, play_btn: QPushButton) -> None:
				# One button starts and stops the preview
				if _preview_state.get("active") is play_btn:
					_preview.stop_preview()
					_reset_preview_visuals()
					return
				_preview.stop_preview()
				_reset_preview_visuals()
				duration_s = _preview.preview(family)
				if duration_s <= 0.0:
					return
				_set_glyph(play_btn, _stop_icon, "■")
				play_btn.setProperty("playing", True)
				play_btn.setToolTip("Stop the preview")
				_preview_state["active"] = play_btn
				token = int(_preview_state["token"]) + 1
				_preview_state["token"] = token

				def _expire() -> None:
					if _preview_state["token"] == token:
						_reset_preview_visuals()

				QTimer.singleShot(int(duration_s * 1000) + 60, self, _expire)

			_preview_buttons: list[QPushButton] = []

			for _fam in _order:
				_label_text = _labels[_fam]
				_row = SoundRow()
				_lay = _row.controls_layout
				_lay.setContentsMargins(0, 3, 0, 3)
				_lay.setSpacing(7)

				_name = QLabel(_label_text, _row)
				_name.setMinimumWidth(96)
				_combo = ComboRow("", _snd, f"{_fam}_file", _options, default="")
				_combo.setMinimumWidth(150)
				_combo.setMaximumWidth(200)

				_play = QPushButton(_row)
				_play.setObjectName("SoundPreview")
				_play.setFixedSize(30, 26)
				_play.setIconSize(QSize(14, 14))
				_set_glyph(_play, _play_icon, "▶")
				_play.setToolTip("Play the selected sound")
				_play.clicked.connect(lambda _=False, fam=_fam, btn=_play: _toggle_preview(fam, btn))
				_preview_buttons.append(_play)

				_toggle = ToggleSwitch()
				_toggle.setChecked(bool(getattr(_snd, f"{_fam}_enabled", True)))
				_toggle.setToolTip("Enable this event sound")
				_toggle.toggled.connect(lambda checked, fam=_fam: setattr(_snd, f"{fam}_enabled", bool(checked)))

				_vol_value = float(getattr(_snd, f"{_fam}_volume", 1.0))
				_slider = QSlider(Qt.Horizontal, _row)
				_slider.setRange(0, 100)
				_slider.setMinimumWidth(130)
				_slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
				_slider.setValue(int(round(_vol_value * 100)))
				_spin = QDoubleSpinBox(_row)
				_spin.setObjectName("SliderSpinBox")
				_spin.setRange(0.0, 1.0)
				_spin.setDecimals(2)
				_spin.setSingleStep(0.05)
				_spin.setFixedWidth(72)
				_spin.setValue(_vol_value)
				_steps = QWidget(_row)
				_steps.setObjectName("SliderStepButtons")
				_steps.setFixedWidth(20)
				_steps_lay = QVBoxLayout(_steps)
				_steps_lay.setContentsMargins(0, 0, 0, 0)
				_steps_lay.setSpacing(2)
				for _graphic, _fallback, _verb, _slot in ((_up_icon, "\u25b2", "Increase", _spin.stepUp), (_down_icon, "\u25bc", "Decrease", _spin.stepDown)):
					_step_btn = QPushButton(_steps)
					_step_btn.setObjectName("SpinArrow")
					_step_btn.setFixedSize(20, 13)
					_step_btn.setIconSize(QSize(13, 13))
					_step_btn.setFocusPolicy(Qt.NoFocus)
					_step_btn.setCursor(Qt.PointingHandCursor)
					_step_btn.setAccessibleName(f"{_verb} {_label_text} volume")
					_set_glyph(_step_btn, _graphic, _fallback)
					_step_btn.clicked.connect(_slot)
					_steps_lay.addWidget(_step_btn)
				_slider.valueChanged.connect(
					lambda value, fam=_fam, spin=_spin: (
						setattr(_snd, f"{fam}_volume", value / 100.0),
						spin.blockSignals(True),
						spin.setValue(value / 100.0),
						spin.blockSignals(False),
					)
				)
				_spin.valueChanged.connect(
					lambda value, fam=_fam, slider=_slider: (
						setattr(_snd, f"{fam}_volume", float(value)),
						slider.blockSignals(True),
						slider.setValue(int(round(float(value) * 100))),
						slider.blockSignals(False),
					)
				)

				_lay.addWidget(_name)
				_lay.addWidget(_toggle)
				_lay.addWidget(_combo, 0)
				_lay.addWidget(_play)
				_lay.addWidget(_slider, 1)
				_lay.addWidget(_spin)
				_lay.addWidget(_steps)
				snd_card.addBodyWidget(_row)

			self._sound_preview_reset = _reset_preview_visuals

		self.end()

	def _build_fps_counter_card(self) -> None:
		'''Build the watermark / telemetry card from the keys the HUD reads.'''
		fps_config = self.config.fps_counter

		master = self.add_widget(
			FeatureCard(
				"Telemetry watermark",
				risk=bind_risk('telemetry_local_fields', lambda: self.config),
				help_text=("A row of pills pinned to the top-right. Drag it in HUD Edit Mode to move it; resizing the frame scales the text."),
				toggle_target=(fps_config, "enabled"),
			)
		)

		for attr, label, default, help_text in [
			(
				"show_fps",
				"Game FPS",
				True,
				"Displayed game frames when PresentMon is available; otherwise engine frames, marked 'eng'. Unavailable values are shown as --.",
			),
			(
				"show_overlay_fps",
				"Overlay loop FPS",
				False,
				"Iterations per second of the overlay loop, not necessarily the number of overlay frames actually displayed.",
			),
			("show_ping", "Game ping", True, "The local player's game-server ping from the scoreboard, when available."),
			(
				"show_latency",
				"Game present delay",
				True,
				"Game Present-to-display time from PresentMon. Shows -- when no valid display sample is available; not input-to-photon latency.",
			),
			("show_cpu", "CPU load", True, "Whole-system Windows Processor Utility, including frequency changes. Falls back to CPU busy time if unavailable."),
			("show_ram", "RAM usage", True, "Physical memory in use / usable total, including Windows and all applications. Values use 1024-based units."),
			(
				"show_jitter",
				"Overlay frame jitter",
				False,
				"Standard deviation of overlay frame intervals, not game/network jitter. Higher values indicate less consistent frame pacing.",
			),
			("show_time", "Clock", False, "Local time, HH:MM."),
			("show_brand", "Brand pill", False, "Shows the text below as the first pill."),
		]:
			switch = _live_toggle(fps_config, attr, getattr(fps_config, attr, default))
			row = LabeledRow(label, switch, help_text=help_text)
			master.addBodyWidget(row)
			if attr == "show_fps":
				self._hud_targets["fps_counter"] = row

		brand = QLineEdit(str(getattr(fps_config, "brand_text", "") or "msmesh"))
		brand.setMaxLength(24)
		brand.editingFinished.connect(lambda: setattr(fps_config, "brand_text", brand.text().strip() or "msmesh"))
		master.addBodyWidget(LabeledRow("Brand text", brand))

		size_sl = LabeledSlider("Text size (px)", lo=10.0, hi=24.0, value=float(getattr(fps_config, "hud_font_size", 17.0) or 17.0), decimals=0, step=1.0)
		size_sl.valueChanged.connect(lambda v: setattr(fps_config, "hud_font_size", float(v)))
		master.addBodyWidget(size_sl)

		radius_sl = LabeledSlider("Corner radius (px)", lo=0.0, hi=16.0, value=float(getattr(fps_config, "corner_radius", 8.0) or 8.0), decimals=0, step=1.0)
		radius_sl.valueChanged.connect(lambda v: setattr(fps_config, "corner_radius", float(v)))
		master.addBodyWidget(radius_sl)

		master.addBodyWidget(KeybindRow("FPS counter · toggle", fps_config, "hotkey_toggle"))

	def _build_event_visuals_card(self) -> None:
		'''Build the HUD widgets card: theme, icon chrome, edit mode, visibility.'''
		event_visuals = getattr(self.config, "event_visuals", None)
		if event_visuals is None:
			return

		from cs2esp.visuals.icons import EVENT_STYLE_DEFAULTS, GLYPH_CHOICES, resolve_hud_theme

		presentation_risk = bind_risk("hud_style", lambda: self.config)

		def _pair(first: QWidget, second: QWidget) -> QWidget:
			holder = QWidget()
			row = QHBoxLayout(holder)
			row.setContentsMargins(0, 0, 0, 0)
			row.setSpacing(6)
			row.addWidget(first)
			row.addWidget(second)
			return holder

		master = self.add_widget(
			FeatureCard(
				"HUD & Event Suite",
				risk=bind_risk('hud_style', lambda: self.config),
				help_text=(
					"Floating glass HUD widgets: active combat event feed, C4 bomb timer capsule, "
					"spectator list, active hotkeys, and status indicator pills. "
					"All modules can be interactively repositioned and resized directly on screen."
				),
				toggle_target=(event_visuals, "enabled"),
			)
		)

		theme_row = ChoiceRow(
			"HUD Theme (Background)",
			event_visuals,
			"theme",
			[
				("auto", "Auto (Match Menu)"),
				("midnight", "Midnight (Obsidian Glass)"),
				("slate", "Slate (Graphite Glass)"),
				("daylight", "Daylight (Crystal Frosted)"),
			],
			default="auto",
		)
		master.addBodyWidget(theme_row)

		custom_btn = ColorButton("Icon and highlight", event_visuals, "custom_color", with_alpha=True, auto_color=lambda: resolve_hud_theme(self.config).accent)
		custom_clear = QPushButton("Theme")
		custom_clear.setToolTip("Reset to the active theme colour")
		custom_clear.clicked.connect(lambda: self._reset_icon_color(event_visuals, "custom_color", custom_btn))
		master.addBodyWidget(
			LabeledRow(
				"Icon & Highlight Color",
				_pair(custom_btn, custom_clear),
				help_text="Global accent for event badges and the HUD palette. Theme = follow the active palette.",
			)
		)

		radius_sl = LabeledSlider(
			"Corner Rounding (px)",
			lo=0,
			hi=16,
			value=float(getattr(event_visuals, "corner_radius", 6.0)),
			decimals=0,
			step=1.0,
			help_text="Uniform corner curvature applied across all HUD capsules and widgets.",
		)
		radius_sl.valueChanged.connect(lambda v: setattr(event_visuals, "corner_radius", float(v)))
		master.addBodyWidget(radius_sl)

		bloom_sl = LabeledSlider(
			"Icon Glow Intensity",
			lo=0.0,
			hi=1.0,
			value=float(getattr(event_visuals, "bloom_intensity", 0.35)),
			decimals=2,
			step=0.05,
			help_text="Soft glow around every HUD icon outline. 0 disables it.",
		)
		bloom_sl.valueChanged.connect(lambda v: setattr(event_visuals, "bloom_intensity", float(v)))
		master.addBodyWidget(bloom_sl)

		shadow_sl = LabeledSlider(
			"Ambient Shadow Depth",
			lo=0.0,
			hi=1.0,
			value=float(getattr(event_visuals, "shadow_intensity", 0.55)),
			decimals=2,
			step=0.05,
			help_text="Soft multi-layer drop shadow for visual elevation above game scenery.",
		)
		shadow_sl.valueChanged.connect(lambda v: setattr(event_visuals, "shadow_intensity", float(v)))
		master.addBodyWidget(shadow_sl)

		outline_sl = LabeledSlider(
			"Icon Outline Width (px)",
			lo=0.0,
			hi=3.0,
			value=float(getattr(event_visuals, "icon_outline_width", 1.5)),
			decimals=1,
			step=0.1,
			help_text="Thickness of the glowing outline around every HUD icon pill.",
		)
		outline_sl.valueChanged.connect(lambda v: setattr(event_visuals, "icon_outline_width", float(v)))
		master.addBodyWidget(outline_sl)

		accent_btn = ColorButton("Outline and glow", event_visuals, "icon_accent_color", with_alpha=True, auto_color=lambda: resolve_hud_theme(self.config).accent)
		accent_btn.setToolTip("Outline and glow colour for every HUD icon. Empty follows the theme.")
		accent_clear = QPushButton("Theme")
		accent_clear.setToolTip("Reset to the active theme colour")
		accent_clear.clicked.connect(lambda: self._reset_icon_color(event_visuals, "icon_accent_color", accent_btn))
		master.addBodyWidget(
			LabeledRow(
				"Outline & Glow Colour",
				_pair(accent_btn, accent_clear),
				help_text="One colour for the outline and its glow across all HUD icons. Theme = follow the active palette.",
			)
		)

		text_btn = ColorButton("Text and icons", event_visuals, "icon_text_color", with_alpha=True, auto_color=lambda: resolve_hud_theme(self.config).text)
		text_btn.setToolTip("Text and glyph colour for every HUD icon. Empty keeps each widget's palette.")
		text_clear = QPushButton("Theme")
		text_clear.setToolTip("Reset to each widget's themed colours")
		text_clear.clicked.connect(lambda: self._reset_icon_color(event_visuals, "icon_text_color", text_btn))
		master.addBodyWidget(
			LabeledRow(
				"Text & Icon Colour", _pair(text_btn, text_clear), help_text="Unified text/glyph colour across the HUD. Theme = keep the current per-widget palette."
			)
		)

		theme_row.changed.connect(lambda _value: (custom_btn.refresh(), accent_btn.refresh(), text_btn.refresh()))

		btn_edit = QPushButton("Enter HUD Edit Mode")
		btn_edit.setObjectName("HudEditModeBtn")
		btn_edit.setMinimumSize(280, 44)
		btn_edit.setMaximumWidth(420)
		btn_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
		btn_edit.setFocusPolicy(Qt.NoFocus)
		btn_edit.setCursor(Qt.PointingHandCursor)
		btn_edit.setToolTip("Dim+blur the game and unlock all HUD widgets for dragging/resizing. Grips resize, Snap rounds to 8 px (Ctrl bypasses), ESC/Done exits.")
		btn_edit.clicked.connect(lambda: self._on_event_vis_toggle("edit_mode", True))
		edit_actions = QWidget()
		edit_layout = QHBoxLayout(edit_actions)
		edit_layout.setContentsMargins(0, 8, 0, 0)
		edit_layout.addStretch(1)
		edit_layout.addWidget(btn_edit, 1)
		edit_layout.addStretch(1)
		master.addBodyWidget(edit_actions)

		sw_lock = ToggleSwitch()
		sw_lock.setChecked(bool(getattr(event_visuals, "edit_lock", True)))
		sw_lock.toggled.connect(lambda c: self._on_event_vis_toggle("edit_lock", bool(c)))
		master.addBodyWidget(
			LabeledRow(
				"Widget editing only in Edit Mode",
				sw_lock,
				help_text="ON: edit only in HUD Edit Mode. OFF: hover with a visible cursor to show grips; hold the left mouse button to move or resize.",
			)
		)

		for attr, label, help_text in [
			("show_hotkeys", "Hotkeys card", "Every feature with a bind, lit while held or toggled on."),
			("show_status_pills", "Status pills", "One pill per enabled feature, bottom-left."),
			("show_spectators", "Spectator list", "Players currently watching you."),
			("show_c4_timer", "C4 timer", "Bomb site, defuse state and lethal damage at your position."),
		]:
			switch = ToggleSwitch()
			switch.setChecked(bool(getattr(event_visuals, attr, True)))
			switch.toggled.connect(lambda c, a=attr: self._on_event_vis_toggle(a, bool(c)))
			capability = capability_for_setting(f"event_visuals.{attr}")
			row = LabeledRow(label, switch, help_text=help_text, risk=bind_risk(capability, lambda: self.config))
			master.addBodyWidget(row)
			self._hud_targets[_HUD_COMPONENT_KEYS[attr]] = row

		events_card = self.add_widget(
			FeatureCard(
				"Event feed", risk=bind_risk('event_feed', lambda: self.config), help_text='Hit, kill, miss, bomb and system cards, anchored above the bottom-left HUD.'
			)
		)
		page_events = QWidget()
		lay_events = QVBoxLayout(page_events)
		lay_events.setContentsMargins(0, 0, 0, 0)
		lay_events.setSpacing(6)
		max_events_sl = LabeledSlider(
			"Max Active Event Pills",
			lo=1,
			hi=10,
			value=int(getattr(event_visuals, "max_events", 6)),
			decimals=0,
			step=1.0,
			help_text="Maximum number of simultaneous event notifications displayed in the feed.", risk=presentation_risk,
		)
		max_events_sl.valueChanged.connect(lambda v: setattr(event_visuals, "max_events", int(v)))
		lay_events.addWidget(max_events_sl)
		self._hud_targets["event_feed"] = max_events_sl

		ev_dur_sl = LabeledSlider(
			"Event Duration (s)",
			lo=1.0,
			hi=10.0,
			value=float(getattr(event_visuals, "event_duration_sec", 3.5)),
			decimals=1,
			step=0.5,
			help_text="Time each combat event pill remains visible on screen.", risk=presentation_risk,
		)
		ev_dur_sl.valueChanged.connect(lambda v: setattr(event_visuals, "event_duration_sec", float(v)))
		lay_events.addWidget(ev_dur_sl)

		ev_font_sl = LabeledSlider("Event Font Size (px)", lo=10.0, hi=20.0, value=float(getattr(event_visuals, "font_size", 14.0)), decimals=0, step=1.0)
		ev_font_sl.valueChanged.connect(lambda v: setattr(event_visuals, "font_size", float(v)))
		lay_events.addWidget(ev_font_sl)

		from cs2esp.visuals.weapon_icons import glyph_label

		lay_events.addWidget(QLabel("Per-Event Cards"))
		self._ev_style_keys = list(EVENT_STYLE_DEFAULTS.keys())
		self.RESET_SCOPES = dict(type(self).RESET_SCOPES)
		self.RESET_SCOPES["Event feed"] = (
			"event_visuals.max_events", "event_visuals.event_duration_sec", "event_visuals.font_size",
		) + tuple(f"event_visuals.ev_{key}_{field}" for key in self._ev_style_keys for field in ("glyph", "label", "color"))
		ev_pick = _ChevronCombo()
		ev_pick.setMinimumContentsLength(20)
		ev_pick.setSizeAdjustPolicy(_ChevronCombo.AdjustToMinimumContentsLengthWithIcon)
		ev_pick.addItems(self._ev_style_keys)
		lay_events.addWidget(LabeledRow("Event Type", ev_pick, help_text="Choose which event card to customize.", risk=presentation_risk))

		ev_on = ToggleSwitch()
		event_enabled_row = LabeledRow("Show This Event", ev_on)
		lay_events.addWidget(event_enabled_row)

		ev_glyph = _ChevronCombo()
		ev_glyph.setMinimumContentsLength(20)
		ev_glyph.setSizeAdjustPolicy(_ChevronCombo.AdjustToMinimumContentsLengthWithIcon)
		for glyph in GLYPH_CHOICES:
			ev_glyph.addItem(glyph_label(glyph), glyph)
		lay_events.addWidget(LabeledRow("Icon", ev_glyph, help_text="Outcome badge; the recorded weapon adds its own silhouette automatically.", risk=presentation_risk))

		ev_label = QLineEdit()
		ev_label.setPlaceholderText("default verb")
		lay_events.addWidget(LabeledRow("Custom Label", ev_label, risk=presentation_risk,
			help_text="Replaces the leading verb on the card (Hit/Missed/…). Empty keeps the default."))

		color_host = QVBoxLayout()
		color_host.setContentsMargins(0, 0, 0, 0)
		color_host_w = QWidget()
		color_host_w.setLayout(color_host)
		lay_events.addWidget(color_host_w)

		def _ev_key() -> str:
			return self._ev_style_keys[ev_pick.currentIndex()]

		def _ev_load() -> None:
			key = _ev_key()
			capability = capability_for_setting(f"event_visuals.ev_{key}_enabled")
			event_enabled_row.setRisk(bind_risk(capability, lambda: self.config), "Display this event type in the feed.")
			ev_on.blockSignals(True)
			ev_on.setChecked(bool(getattr(event_visuals, f"ev_{key}_enabled", True)))
			ev_on.blockSignals(False)
			ev_glyph.blockSignals(True)
			cur_glyph = str(getattr(event_visuals, f"ev_{key}_glyph", "") or EVENT_STYLE_DEFAULTS[key])
			index = ev_glyph.findData(cur_glyph)
			ev_glyph.setCurrentIndex(max(0, index))
			ev_glyph.blockSignals(False)
			ev_label.blockSignals(True)
			ev_label.setText(str(getattr(event_visuals, f"ev_{key}_label", "") or ""))
			ev_label.blockSignals(False)
			while color_host.count():
				item = color_host.takeAt(0)
				widget = item.widget()
				if widget is not None:
					widget.deleteLater()
			btn = ColorButton("Icon Colour", event_visuals, f"ev_{key}_color", with_alpha=True)
			clear = QPushButton("Theme")
			clear.setToolTip("Reset to the theme accent colour")

			def _clear(k=key, b=btn):
				setattr(event_visuals, f"ev_{k}_color", [])
				b._swatch_signature = None
				b._refresh_swatch()

			clear.clicked.connect(_clear)
			holder = QWidget()
			color_layout = QHBoxLayout(holder)
			color_layout.setContentsMargins(0, 0, 0, 0)
			color_layout.addWidget(btn)
			color_layout.addWidget(clear)
			color_host.addWidget(LabeledRow("Icon Colour", holder))

		ev_pick.currentIndexChanged.connect(lambda _i: _ev_load())
		ev_on.toggled.connect(lambda c: setattr(event_visuals, f"ev_{_ev_key()}_enabled", bool(c)))
		ev_glyph.currentIndexChanged.connect(lambda _index: setattr(event_visuals, f"ev_{_ev_key()}_glyph", str(ev_glyph.currentData())))
		ev_label.editingFinished.connect(lambda: setattr(event_visuals, f"ev_{_ev_key()}_label", ev_label.text().strip()))
		_ev_load()
		events_card.addBodyWidget(page_events)
		master.setGroupRole("parent")
		events_card.setGroupRole("child")

	def _on_event_vis_toggle(self, attr: str, checked: bool) -> None:
		'''Write an event-visual toggle and save config.'''
		event_visuals = getattr(self.config, "event_visuals", None)
		if event_visuals is None:
			return
		try:
			setattr(event_visuals, attr, bool(checked))
		except Exception as e:
			print(f"[esp] set event_visuals.{attr}={checked!r} failed: {e!r}")
		try:
			self.config.save()
		except Exception as e:
			print(f"[esp] explicit save after event_visuals toggle failed: {e!r}")

	def _reset_icon_color(self, section, attr: str, button: ColorButton) -> None:
		'''Reset a HUD icon colour to "follow theme" and persist immediately.'''
		try:
			setattr(section, attr, [])
			self.config.save()
		except Exception as e:
			print(f"[esp] reset {attr} failed: {e!r}")
		refresh = getattr(button, "refresh", None)
		if callable(refresh):
			refresh()

	def _on_wh_toggle(self, which: str, checked: bool) -> None:
		section = {"box": self.config.visuals.box, "skeleton": self.config.visuals.skeleton, "glow": self.config.glow, "chams": self.config.visuals.chams}[which]
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
		'''Persist the selected glow type from its checked radio.'''
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
		'''Show style cards only for enabled player-overlay primitives.'''
		self._box_card.setVisible(bool(self.config.visuals.box.enabled))
		self._skel_card.setVisible(bool(self.config.visuals.skeleton.enabled))
		self._glow_card.setVisible(bool(self.config.glow.enabled))
		try:
			chams_on = bool(getattr(self.config.visuals.chams, "enabled", False))
			self._chams_card.setVisible(chams_on)
		except AttributeError:
			self._chams_card.setVisible(False)


__all__ = ["ESPPanel"]
