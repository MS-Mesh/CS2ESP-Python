"""Semantic palettes, responsive metrics, and QSS for the Qt menu."""

from dataclasses import dataclass
from string import Template


# Kept public for the help popup's runtime font measurement.
DISPLAY_PX = 23
H1_PX = 27
H2_PX = 15
BODY_PX = 13
CAPTION_PX = 11


PALETTES: dict[str, dict[str, str]] = {
    "midnight": {
        "bg_app": "#0c0f14",
        "bg_sidebar": "#10141b",
        "surface_1": "#151a22",
        "surface_2": "#1b212b",
        "surface_hi": "#252d38",
        "accent": "#83a9c0",
        "accent_hi": "#aac5d5",
        "accent_dim": "#58798d",
        "accent_soft": "rgba(131, 169, 192, 32)",
        "safe": "#79b39a",
        "safe_soft": "rgba(121, 179, 154, 28)",
        "warn": "#d0a064",
        "warn_soft": "rgba(208, 160, 100, 28)",
        "danger": "#d27676",
        "danger_soft": "rgba(210, 118, 118, 28)",
        "status_ok": "#79b39a",
        "status_bad": "#d27676",
        "status_info": "#8eafc2",
        "theme_dot": "#83a9c0",
        "text": "#eef1f4",
        "text_dim": "#a5adb9",
        "text_mute": "#7b8695",
        "border": "#27303b",
        "divider": "#1e252e",
        "shadow": "#050608",
    },
    "slate": {
        "bg_app": "#12161b",
        "bg_sidebar": "#161b22",
        "surface_1": "#1a2028",
        "surface_2": "#222a34",
        "surface_hi": "#2c3541",
        "accent": "#8eb1c5",
        "accent_hi": "#b4ccda",
        "accent_dim": "#627f91",
        "accent_soft": "rgba(142, 177, 197, 34)",
        "safe": "#82b69e",
        "safe_soft": "rgba(130, 182, 158, 28)",
        "warn": "#d5a469",
        "warn_soft": "rgba(213, 164, 105, 28)",
        "danger": "#d67b7b",
        "danger_soft": "rgba(214, 123, 123, 28)",
        "status_ok": "#82b69e",
        "status_bad": "#d67b7b",
        "status_info": "#91b2c5",
        "theme_dot": "#8eb1c5",
        "text": "#f0f2f5",
        "text_dim": "#aeb5bf",
        "text_mute": "#7f8a98",
        "border": "#2d3641",
        "divider": "#232a33",
        "shadow": "#090b0e",
    },
    "daylight": {
        "bg_app": "#f3f1ec",
        "bg_sidebar": "#ece9e2",
        "surface_1": "#fbfaf7",
        "surface_2": "#f0ede7",
        "surface_hi": "#e4e0d8",
        "accent": "#426d86",
        "accent_hi": "#31566d",
        "accent_dim": "#7897a9",
        "accent_soft": "rgba(66, 109, 134, 24)",
        "safe": "#397c61",
        "safe_soft": "rgba(57, 124, 97, 22)",
        "warn": "#9a682e",
        "warn_soft": "rgba(154, 104, 46, 22)",
        "danger": "#ad4f4f",
        "danger_soft": "rgba(173, 79, 79, 22)",
        "status_ok": "#397c61",
        "status_bad": "#ad4f4f",
        "status_info": "#426d86",
        "theme_dot": "#426d86",
        "text": "#1b2229",
        "text_dim": "#56616c",
        "text_mute": "#626d78",
        "border": "#d6d1c8",
        "divider": "#e3dfd7",
        "shadow": "#b8b2a8",
    },
}


@dataclass(frozen=True)
class UIMetrics:
    """Geometry that follows accessibility scale without becoming unwieldy."""

    scale: float
    density: float
    titlebar_height: int
    sidebar_width: int
    footer_height: int
    nav_icon_size: int
    header_button_height: int
    window_button_width: int
    content_max_width: int
    resize_margin: int


def metrics(scale: float = 1.0) -> UIMetrics:
    """Return shell geometry for the configured UI scale."""
    scale = min(2.5, max(0.5, float(scale or 1.0)))
    density = min(1.55, max(0.82, 0.72 + scale * 0.30))

    def dp(value: int) -> int:
        return max(1, int(round(value * density)))

    high_scale_sidebar_extra = max(
        0, int(round((scale - 1.5) * 28))
    )
    return UIMetrics(
        scale=scale,
        density=density,
        titlebar_height=dp(60),
        sidebar_width=dp(232) + high_scale_sidebar_extra,
        footer_height=dp(40),
        nav_icon_size=dp(18),
        header_button_height=dp(32),
        window_button_width=dp(42),
        content_max_width=dp(1080),
        resize_margin=dp(6),
    )


def stylesheet(theme_name: str, scale: float = 1.0) -> str:
    """Render the application stylesheet for a named palette and scale."""
    scale = min(2.5, max(0.5, float(scale or 1.0)))
    palette = PALETTES.get(theme_name, PALETTES["slate"])

    def px(value: float) -> int:
        return max(1, int(round(value * scale)))

    tokens = {
        **palette,
        "display_px": px(DISPLAY_PX),
        "h1_px": px(H1_PX),
        "h2_px": px(H2_PX),
        "body_px": px(BODY_PX),
        "caption_px": px(CAPTION_PX),
        "nav_px": px(13),
        "status_px": px(12),
        "control_radius": max(5, px(7)),
        "card_radius": max(8, px(11)),
    }
    return _QSS.substitute(tokens)


_QSS = Template(r"""
* {
    font-family: "Space Grotesk", "Segoe UI Variable", "Segoe UI", sans-serif;
    color: $text;
    selection-background-color: $accent;
    selection-color: $bg_app;
}

QWidget {
    background: transparent;
    font-size: ${body_px}px;
}

QMainWindow, #RootWindow, #RootContainer {
    background: $bg_app;
}

/* Window chrome */
#TitleBar {
    background: $bg_sidebar;
    border-bottom: 1px solid $border;
}

#BrandMark {
    background: $accent_soft;
    border: 1px solid $accent_dim;
    border-radius: 9px;
    color: $accent_hi;
    font-size: ${h2_px}px;
    font-weight: 700;
    qproperty-alignment: AlignCenter;
}

#TitleBarLogo {
    color: $text;
    font-size: ${display_px}px;
    font-weight: 700;
    letter-spacing: -0.4px;
}

#TitleBarSubtitle {
    color: $text_mute;
    font-size: ${caption_px}px;
    letter-spacing: 0.2px;
}

#ThemePillBtn, #MasterPillBtn, #SaveBtn {
    background: $surface_1;
    border: 1px solid $border;
    border-radius: $control_radius;
    color: $text_dim;
    font-size: ${caption_px}px;
    font-weight: 600;
    padding: 0 12px;
}

#ThemePillBtn:hover, #SaveBtn:hover {
    background: $surface_2;
    border-color: $accent_dim;
    color: $text;
}

#ThemePillBtn:focus, #MasterPillBtn:focus, #SaveBtn:focus {
    border-color: $accent;
}

#MasterPillBtn[active="true"] {
    background: $safe_soft;
    border-color: $safe;
    color: $safe;
}

#MasterPillBtn[active="false"] {
    background: $danger_soft;
    border-color: $danger;
    color: $danger;
}

#MasterPillBtn:hover {
    background: $surface_hi;
}

#SaveBtn[saved="true"] {
    background: $safe_soft;
    border-color: $safe;
    color: $safe;
}

#WindowControlBtn {
    background: transparent;
    border: none;
    border-radius: 0;
    color: $text_mute;
    padding: 0;
}

#WindowControlBtn:hover {
    background: $surface_2;
    color: $text;
}

#WindowControlBtn[role="close"]:hover {
    background: $danger;
    color: white;
}

/* Sidebar and status */
#Sidebar {
    background: $bg_sidebar;
    border-right: 1px solid $border;
}

#SidebarStatusCard {
    background: $surface_1;
    border: 1px solid $border;
    border-radius: $card_radius;
}

#SidebarStatusTitle {
    color: $text;
    font-size: ${body_px}px;
    font-weight: 600;
}

#StatusDot[active="true"] {
    background: $status_ok;
    border-radius: 99px;
}

#StatusDot[active="false"] {
    background: $status_bad;
    border-radius: 99px;
}

#StatusAttached, #StatusDetached {
    color: $text_dim;
    font-size: ${status_px}px;
    font-weight: 500;
}

#StatusFps {
    color: $status_info;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: ${status_px}px;
    font-weight: 600;
}

#SidebarLabel {
    color: $text_mute;
    font-size: ${caption_px}px;
    font-weight: 600;
    letter-spacing: 0.6px;
    padding: 8px 12px 6px 12px;
}

QListWidget#SidebarList {
    background: transparent;
    border: none;
    outline: none;
    padding: 2px 8px;
}

QListWidget#SidebarList::item {
    background: transparent;
    border: 1px solid transparent;
    border-radius: $control_radius;
    color: $text_dim;
    font-size: ${nav_px}px;
    margin: 2px 0;
    padding: 10px 12px;
}

QListWidget#SidebarList::item:hover {
    background: $surface_1;
    color: $text;
}

QListWidget#SidebarList::item:selected {
    background: $accent_soft;
    border-color: $accent_dim;
    color: $accent_hi;
}

/* Content and type */
#ContentScroll {
    background: $bg_app;
    border: none;
}

QScrollBar:vertical {
    background: transparent;
    margin: 6px 3px;
    width: 10px;
}

QScrollBar::handle:vertical {
    background: $surface_hi;
    border-radius: 4px;
    min-height: 28px;
}

QScrollBar::handle:vertical:hover {
    background: $accent_dim;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
    height: 0;
}

QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 3px 6px;
}

QScrollBar::handle:horizontal {
    background: $surface_hi;
    border-radius: 4px;
    min-width: 28px;
}

QScrollBar::handle:horizontal:hover {
    background: $accent_dim;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
    width: 0;
}

QLabel#H1 {
    color: $text;
    font-size: ${h1_px}px;
    font-weight: 700;
    letter-spacing: -0.5px;
    padding: 2px 0 4px 0;
}

QLabel#H2 {
    color: $text;
    font-size: ${h2_px}px;
    font-weight: 650;
    letter-spacing: -0.1px;
    padding: 12px 0 2px 0;
}

QLabel#PanelIntro {
    color: $text_dim;
    font-size: ${body_px}px;
    padding: 0 0 12px 0;
}

QLabel#SectionIntro {
    color: $text_mute;
    font-size: ${body_px}px;
    padding: 0 0 6px 0;
}

QLabel#Body, QLabel[muted="true"] {
    color: $text_dim;
}

QLabel#Caption {
    color: $text_mute;
    font-size: ${caption_px}px;
}

/* Cards */
#FeatureCard {
    background: $surface_1;
    border: 1px solid $border;
    border-radius: $card_radius;
    margin: 4px 0;
}

#FeatureCard:hover {
    border-color: $surface_hi;
}

#FeatureCardHeader {
    background: transparent;
    border: none;
}

#FeatureCardTitle {
    color: $text;
    font-size: ${h2_px}px;
    font-weight: 600;
}

#FeatureCardAccent {
    background: $accent;
    border-radius: 2px;
}

#FeatureCardAccent[risk="medium"] {
    background: $warn;
}

#FeatureCardAccent[risk="high"] {
    background: $danger;
}

#RiskBadge {
    border: 1px solid transparent;
    border-radius: $control_radius;
    font-size: ${caption_px}px;
    font-weight: 700;
    padding: 2px 6px;
}

#RiskBadge[risk="medium"] {
    background: $warn_soft;
    border-color: $warn;
    color: $warn;
}

#RiskBadge[risk="high"] {
    background: $danger_soft;
    border-color: $danger;
    color: $danger;
}

#FeatureCardBody {
    background: transparent;
}

QToolButton#FeatureCardCollapse {
    background: transparent;
    border: none;
    border-radius: 5px;
    color: $text_mute;
    min-height: 24px;
    min-width: 24px;
    padding: 0;
}

QToolButton#FeatureCardCollapse:hover,
QToolButton#FeatureCardCollapse:focus {
    background: $surface_2;
    color: $text;
}

#FeatureCard[collapsed="true"] {
    border-color: $divider;
}

#FeatureCard[groupRole="child"] {
    background: $surface_2;
    margin-left: 14px;
}

#FeatureCard[groupRole="parent"] {
    margin-bottom: 0;
}

#FeatureSubFrame, #HeadOutlineAdvanced, #DistanceBandsEditor,
#TriggerModeFrame, #ScriptGuide {
    background: $surface_2;
    border: 1px solid $divider;
    border-radius: $control_radius;
}

#ScriptGuide {
    color: $text_dim;
    padding: 12px;
}

#ScriptActions {
    background: transparent;
}

#FeatureWarning {
    background: $warn_soft;
    border-left: 2px solid $warn;
    color: $text_dim;
    padding: 8px 10px;
}

#FeatureWarningTitle {
    color: $warn;
    font-weight: 650;
}

#FeatureWarningText {
    color: $text_dim;
}

/* Help */
#HelpIcon, #HelpIconInline {
    background: transparent;
    border: 1px solid $border;
    color: $text_mute;
    font-size: ${caption_px}px;
    font-weight: 600;
    padding: 0;
}

#HelpIcon {
    border-radius: 10px;
    min-height: 20px;
    max-height: 20px;
    min-width: 20px;
    max-width: 20px;
}

#HelpIconInline {
    border-radius: 8px;
    min-height: 16px;
    max-height: 16px;
    min-width: 16px;
    max-width: 16px;
}

#HelpIcon:hover, #HelpIconInline:hover {
    background: $accent_soft;
    border-color: $accent_dim;
    color: $accent_hi;
}

#HelpIcon[risk="medium"] {
    border-color: $warn;
    color: $warn;
}

#HelpIcon[risk="high"] {
    border-color: $danger;
    color: $danger;
}

#PinnedTooltip {
    background: $surface_1;
    border: 1px solid $accent_dim;
    border-radius: $control_radius;
}

#PinnedTooltip[mode="hover"] {
    border-color: $border;
}

#PinnedBody {
    color: $text;
}

#TooltipRisk {
    color: $text_dim;
    font-size: ${body_px}px;
    font-weight: 650;
}

#TooltipRisk[risk="medium"] {
    color: $warn;
}

#TooltipRisk[risk="high"] {
    color: $danger;
}

#TooltipRiskDetail {
    color: $text_mute;
    font-size: ${caption_px}px;
}

#PinnedClose {
    background: transparent;
    border: none;
    color: $text_mute;
    padding: 0;
}

#PinnedClose:hover {
    color: $danger;
}

QToolTip {
    background: $surface_1;
    border: 1px solid $border;
    border-radius: $control_radius;
    color: $text;
    font-size: ${body_px}px;
    padding: 8px 11px;
    opacity: 250;
}

/* Form primitives */
QLabel {
    background: transparent;
    color: $text;
}

QPushButton {
    background: $surface_2;
    border: 1px solid $border;
    border-radius: $control_radius;
    color: $text;
    font-size: ${body_px}px;
    font-weight: 500;
    padding: 7px 13px;
}

QPushButton:hover {
    background: $surface_hi;
    border-color: $accent_dim;
}

QPushButton:focus {
    border-color: $accent;
}

QPushButton:pressed {
    background: $accent_soft;
    border-color: $accent;
}

QPushButton:disabled {
    background: $surface_1;
    border-color: $divider;
    color: $text_mute;
}

QPushButton[role="primary"], #PrimaryActionBtn {
    background: $accent;
    border-color: $accent;
    color: $bg_app;
    font-weight: 650;
}

QPushButton[role="primary"]:hover, #PrimaryActionBtn:hover {
    background: $accent_hi;
    border-color: $accent_hi;
}

QPushButton[role="danger"] {
    background: transparent;
    border-color: $danger;
    color: $danger;
}

QPushButton[role="danger"]:hover {
    background: $danger_soft;
}

QPushButton[role="ghost"], #SecondaryActionBtn, #DiagRefreshBtn {
    background: transparent;
    border-color: $border;
    color: $text_dim;
}

QPushButton[role="ghost"]:hover, #SecondaryActionBtn:hover,
#DiagRefreshBtn:hover {
    background: $surface_2;
    border-color: $accent_dim;
    color: $text;
}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: $surface_2;
    border: 1px solid $border;
    border-radius: $control_radius;
    color: $text;
    font-size: ${body_px}px;
    min-height: 20px;
    padding: 6px 10px;
}

QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {
    border-color: $accent_dim;
}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: $accent;
}

QComboBox:disabled, QLineEdit:disabled,
QSpinBox:disabled, QDoubleSpinBox:disabled {
    background: $surface_1;
    border-color: $divider;
    color: $text_mute;
}

QComboBox::drop-down {
    border: none;
    width: 24px;
}

QComboBox::down-arrow {
    image: none;
}

QComboBox QAbstractItemView {
    background: $surface_1;
    border: 1px solid $border;
    color: $text;
    outline: none;
    padding: 4px;
    selection-background-color: $accent_soft;
    selection-color: $accent_hi;
}

QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button,
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow,
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    border: none;
    height: 0;
    width: 0;
}

QSlider::groove:horizontal {
    background: $surface_hi;
    border-radius: 2px;
    height: 4px;
}

QSlider::sub-page:horizontal {
    background: $accent;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    background: $text;
    border: 2px solid $accent;
    border-radius: 8px;
    height: 12px;
    margin: -5px 0;
    width: 12px;
}

QSlider::handle:horizontal:hover {
    background: $accent_hi;
    border-color: $accent_hi;
}

QRadioButton {
    color: $text_dim;
    spacing: 8px;
}

QRadioButton:hover, QRadioButton:checked {
    color: $text;
}

QRadioButton:focus {
    color: $accent_hi;
}

QRadioButton:disabled {
    color: $text_mute;
}

QRadioButton::indicator {
    background: $surface_2;
    border: 2px solid $border;
    border-radius: 8px;
    height: 14px;
    width: 14px;
}

QRadioButton::indicator:hover {
    border-color: $accent_dim;
}

QRadioButton::indicator:checked {
    background: $accent;
    border: 4px solid $surface_1;
}

QRadioButton::indicator:disabled {
    background: $surface_1;
    border-color: $divider;
}

QGroupBox {
    border: none;
    margin: 0;
    padding: 0;
}

QGroupBox::title {
    color: transparent;
}

/* Setting rows and selectors */
#LabeledRow, #ChoiceRow, #ComboRow, #KeybindRow {
    background: transparent;
}

#SettingLabel, #ChoiceLabel, #KeybindLabel {
    color: $text_dim;
}

#ChoiceRow QRadioButton, QRadioButton#ChoiceOption {
    padding: 4px 8px;
}

#ThemeOptions {
    background: transparent;
}

#ThemeOptionsLabel, #GlowTypeTitle, #HeadAdvModeTitle {
    color: $text_mute;
    font-size: ${caption_px}px;
    font-weight: 600;
}

QRadioButton#ChoiceOption:checked {
    color: $accent_hi;
}

QComboBox#ChoiceCombo[customChevron="true"]::down-arrow {
    image: none;
    height: 0;
    width: 0;
}

QComboBox#ChoiceCombo:hover, QComboBox#ChoiceCombo:focus {
    border-color: $accent;
}

#SegmentedSelector {
    background: $surface_2;
    border: 1px solid $border;
    border-radius: $control_radius;
}

#SegmentedButton {
    background: transparent;
    border: none;
    border-radius: $control_radius;
    color: $text_dim;
    padding: 6px 13px;
}

#SegmentedButton:hover {
    background: $surface_hi;
    color: $text;
}

#SegmentedButton[role="active"], #SegmentedButton[selected="true"] {
    background: $accent_soft;
    color: $accent_hi;
    font-weight: 600;
}

#ColorButton {
    background: $surface_2;
    border: 1px solid $border;
    border-radius: $control_radius;
    color: $text;
    padding: 5px 10px;
    text-align: left;
}

#ColorButton:hover {
    background: $surface_hi;
    border-color: $accent_dim;
}

#SpinArrow {
    background: $surface_2;
    border: 1px solid $border;
    border-radius: 3px;
    padding: 0;
}

#SpinArrow:hover {
    background: $surface_hi;
    border-color: $accent_dim;
}

ToggleSwitch {
    min-height: 24px;
    max-height: 24px;
    min-width: 44px;
    max-width: 44px;
}

/* Keybinds */
#KeybindCurrent {
    background: $surface_2;
    border: 1px solid $border;
    border-radius: $control_radius;
    color: $text_mute;
    font-family: "Cascadia Mono", "Consolas", monospace;
    padding: 5px 9px;
}

#KeybindCurrent[state="bound"] {
    color: $accent_hi;
}

#KeybindCurrent[state="capturing"] {
    background: $warn_soft;
    border-color: $warn;
    color: $warn;
}

#KeybindRebind, #KeybindClear {
    padding: 5px 10px;
}

#KeybindRebind[role="commit"] {
    background: $safe_soft;
    border-color: $safe;
    color: $safe;
    font-weight: 650;
}

#KeybindClear[role="cancel"] {
    background: $danger_soft;
    border-color: $danger;
    color: $danger;
}

#KeybindGroupHeader {
    color: $text_mute;
    font-size: ${caption_px}px;
    font-weight: 650;
    padding: 10px 0 4px 0;
}

/* Distance bands and advanced blocks */
#BandsHeader, #HeadAdvHeader {
    background: transparent;
}

#HeadAdvModeSection, #GlowTypeGroup, #TriggerModePanel {
    background: transparent;
}

#HeadAdvModeOption:checked, #GlowTypeOption:checked {
    color: $accent_hi;
}

#BandsListHeader {
    color: $text_mute;
    font-size: ${caption_px}px;
}

#BandsDescription {
    color: $text_mute;
    font-size: ${caption_px}px;
}

#BandsChevron, #HeadAdvChevron {
    background: transparent;
    border: none;
    color: $text_dim;
    font-weight: 600;
    padding: 5px 0;
    text-align: left;
}

#BandsChevron:hover, #BandsChevron:checked,
#HeadAdvChevron:hover, #HeadAdvChevron:checked {
    color: $accent_hi;
}

#BandsReset, #BandsRemove {
    background: transparent;
    color: $text_mute;
    padding: 5px 9px;
}

#BandsReset:hover {
    color: $text;
}

#BandsRemove:hover {
    background: $danger_soft;
    border-color: $danger;
    color: $danger;
}

#BandsAdd {
    background: transparent;
    border: 1px dashed $border;
    color: $text_dim;
}

#BandsAdd:hover {
    background: $accent_soft;
    border-color: $accent_dim;
    color: $accent_hi;
}

#BandRow {
    background: $surface_1;
    border: 1px solid $divider;
    border-radius: $control_radius;
}

#BandRowTitle {
    color: $text;
    font-weight: 600;
}

#BandRowHeader, #BandMaxDistanceRow, #BandMaxDistanceControl {
    background: transparent;
}

#BandRowName {
    color: $text_dim;
}

#BandRowRange {
    color: $text_mute;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: ${caption_px}px;
}

#BandMaxDistanceControl[infinite="true"] {
    background: $accent_soft;
    border-radius: $control_radius;
}

#BandInfinityBtn {
    background: $surface_2;
    border: 1px solid $border;
    color: $text_mute;
    padding: 0;
}

#BandInfinityBtn:checked {
    background: $accent_soft;
    border-color: $accent;
    color: $accent_hi;
}

/* Diagnostics */
#DiagReadout, #HealthRows {
    background: $surface_2;
    border: 1px solid $divider;
    border-radius: $control_radius;
    color: $text_dim;
    font-family: "Cascadia Mono", "Consolas", monospace;
    padding: 10px;
}

#HealthSummary {
    color: $text;
    font-weight: 600;
    padding: 4px 0;
}

/* Footer */
#Footer {
    background: $bg_sidebar;
    border-top: 1px solid $border;
}

#FooterPill {
    background: transparent;
}

#FooterKey {
    background: $surface_2;
    border: 1px solid $border;
    border-radius: 4px;
    color: $text_dim;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: ${caption_px}px;
    padding: 2px 7px;
}

#FooterText {
    color: $text_mute;
    font-size: ${caption_px}px;
}
""")
