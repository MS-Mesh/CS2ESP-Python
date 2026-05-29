"""Settings panels, one per sidebar entry."""

from cs2esp.ui.qt.panels.anti_detection import AntiDetectionPanel
from cs2esp.ui.qt.panels.awareness import AwarenessPanel
from cs2esp.ui.qt.panels.base import PanelBase
from cs2esp.ui.qt.panels.combat import CombatPanel
from cs2esp.ui.qt.panels.esp import ESPPanel
from cs2esp.ui.qt.panels.keybinds import KeybindsPanel
from cs2esp.ui.qt.panels.norecoil import NoRecoilPanel
from cs2esp.ui.qt.panels.radar import RadarPanel
from cs2esp.ui.qt.panels.settings import SettingsPanel

__all__ = [
    "AntiDetectionPanel",
    "AwarenessPanel",
    "CombatPanel",
    "ESPPanel",
    "KeybindsPanel",
    "NoRecoilPanel",
    "PanelBase",
    "RadarPanel",
    "SettingsPanel",
]
