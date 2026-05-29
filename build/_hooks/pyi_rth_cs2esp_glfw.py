"""Use imgui_bundle's GLFW DLL for the Python glfw wrapper."""

import os
import sys

bundle_root = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
os.environ.setdefault(
    "PYGLFW_LIBRARY", os.path.join(bundle_root, "imgui_bundle", "glfw3.dll")
)
