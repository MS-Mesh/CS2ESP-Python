"""
Opt-in process concealment primitives used by the production app.
"""

from cs2esp.anti_detect.module_spoof import spoof_python_module_name, is_supported as module_spoof_supported
from cs2esp.anti_detect.read_obfuscator import install_global as install_read_obfuscator
from cs2esp.anti_detect.window_class import masquerade_qt_window, is_supported as window_class_supported


__all__ = ["spoof_python_module_name", "module_spoof_supported", "install_read_obfuscator", "masquerade_qt_window", "window_class_supported"]
