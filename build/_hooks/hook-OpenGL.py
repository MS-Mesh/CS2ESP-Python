"""First-hookspath override for the bundled PyOpenGL hook: build/_hooks/ wins over the contrib hook.
The contrib collect_data_files sweep brings in VC9 GLUT/GLE DLLs that need the never-shipped MSVCR90.dll; excluding them before Analysis kills the warning chain.
"""

from PyInstaller.compat import is_win, is_darwin
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

if is_win:
	hiddenimports = ["OpenGL.platform.win32"]
elif is_darwin:
	hiddenimports = ["OpenGL.platform.darwin"]
else:
	hiddenimports = ["OpenGL.platform.glx"]

hiddenimports += collect_submodules("OpenGL.arrays")

if is_win:
	# Exclude unused VC9 GLUT/GLE binaries before dependency scanning
	datas = collect_data_files("OpenGL", excludes=["**/DLLS/**"])
