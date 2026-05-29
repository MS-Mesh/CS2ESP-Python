"""Override for the bundled PyInstaller PyOpenGL hook.

Lives in build/_hooks/ which build.py registers as the FIRST hookspath,
so PyInstaller's hook cache (highest-priority wins) uses THIS hook for
the ``OpenGL`` module instead of the contrib one.

Why: the contrib hook does ``collect_data_files('OpenGL')`` which sweeps
in PyOpenGL's legacy Windows fallback DLLs
(``OpenGL/DLLS/gle*.vc9.dll`` + ``freeglut*.vc9.dll``). Those VC9 DLLs
depend on ``MSVCR90.dll`` (the VS2008 CRT) which we never ship, so the
binary-dependency scanner emits a "Library not found: MSVCR90.dll"
warning for each during Analysis. We render through the system
``opengl32.dll`` via WGL and never touch PyOpenGL's bundled GLUT/GLE, so
the whole ``DLLS/`` folder is dead weight. Excluding it here stops the
DLLs from ever entering Analysis, which kills the warning chain at the
source instead of only pruning them from the final bundle afterwards.
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
    # Same as the contrib hook MINUS the bundled VC9 GLUT/GLE DLLs.
    datas = collect_data_files("OpenGL", excludes=["**/DLLS/**"])
