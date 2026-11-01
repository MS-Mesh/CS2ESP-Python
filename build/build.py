"""
PyInstaller build driver for CS2 ESP: DEV onedir and APP onefile releases from one runtime entry point.
It renders a generated spec, trims optional plugin bundles, preserves user-edited config across rebuilds, and takes the mode from the CLI or CS2_BUILD_MODE.
"""

import importlib.util
import subprocess
import sys
import tomllib
from os import environ, fdopen, fsync, pathsep
from pathlib import Path
from shutil import copy2, copytree, move, rmtree, which
from tempfile import mkdtemp, mkstemp

BUILD_MODE: str = "APP"
BUILD_MODE_ENV = "CS2_BUILD_MODE"
EXE_NAME: str = "cs2esp"

ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = Path(__file__).resolve().parent
WORK_DIR = BUILD_DIR / "_work"
SPEC_DIR = BUILD_DIR / "_spec"
HOOKS_DIR = BUILD_DIR / "_hooks"
DEV_DIR = BUILD_DIR / "DEV"
APP_DIR = BUILD_DIR / "APP"
APP_STAGE_DIR = BUILD_DIR / "_app_stage"

MAIN_PY = ROOT / "main.py"
CONFIG_SRC = ROOT / "config"
QT_ASSETS_SRC = ROOT / "cs2esp" / "ui" / "qt" / "assets"
VPKMESH_DLL = ROOT / "cs2esp" / "assets" / "bin" / "vpkmesh.dll"
OVERLAYPERF_DLL = ROOT / "cs2esp" / "assets" / "bin" / "overlayperf.dll"
INPUTGUARD_DLL = ROOT / "cs2esp" / "assets" / "bin" / "inputguard.dll"
PRESENTMON_EXE = ROOT / "cs2esp" / "assets" / "bin" / "presentmon.exe"
PRESENTMON_LICENSES = ROOT / "cs2esp" / "assets" / "licenses" / "presentmon"
RESET_DEFAULTS = ROOT / "cs2esp" / "assets" / "reset_defaults.json"
SOUNDS_SRC = ROOT / "cs2esp" / "sounds"
OVERLAY_FONTS = ROOT / "cs2esp" / "assets" / "fonts"
QT_AWESOME_FONT_PREFIX = "materialdesignicons5-"


ALL_EXCLUDES: list[str] = [
	"tkinter", "unittest", "pydoc", "doctest",
	"pytest", "lib2to3", "tzdata",
	# The optional CFFI bridge serves Vulkan interop, not this OpenGL runtime
	"setuptools", "pkg_resources", "distutils",
	"_distutils_hack", "cffi", "_cffi_backend", "pycparser",
	"IPython", "PIL", "matplotlib", "pandas", "pydantic",
	"pydantic_core", "pygame", "pyglet", "sdl2", "sdl3",
	"dearpygui", "gradio", "nicegui", "bokeh", "plotly",
	"dash", "shiny", "textual", "wgpu",
	"PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
	"PySide6.QtSvg", "PySide6.QtVirtualKeyboard",
	"imgui_bundle.python_backends.pygame_backend",
	"imgui_bundle.python_backends.pyglet_backend",
	"imgui_bundle.python_backends.sdl2_backend",
	"imgui_bundle.python_backends.sdl3_backend",
	# Exclude unused Qt shims so their backing DLLs are not bundled
	"PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
	"PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
	"PySide6.QtWebSockets", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
	"PySide6.QtQml", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
	"PySide6.QtDesigner", "PySide6.QtCharts", "PySide6.QtMultimedia",
	"PySide6.QtMultimediaWidgets", "PySide6.QtNetwork", "PySide6.QtSql",
	"PySide6.Qt3DCore", "PySide6.QtDataVisualization", "PySide6.QtTest",
	"PySide6.QtSensors", "PySide6.QtPositioning", "PySide6.QtBluetooth",
	# Requests omits encodings whose optional decoder is unavailable
	"brotli", "_brotli", "brotlicffi", "zstandard", "_zstd",
	"compression.zstd",
	"requests", "urllib3", "certifi", "charset_normalizer", "idna",
	# Offset downloads use stdlib TLS; cryptography is unused, but libcrypto/libssl must remain
	"cryptography",
]

if sys.version_info >= (3, 11):
	ALL_EXCLUDES.append("tomli")

SUBMODULE_DROP_PATTERNS: dict[str, list[str]] = {
	"qtawesome": [".icon_browser", ".tests"],
	"imgui_bundle": [
		".demos_python", ".sandbox.", ".demo_",
		".imgui_pydantic",
		".python_backends.python_backends_disabled",
		".python_backends.pygame_backend",
		".python_backends.pyglet_backend",
		".python_backends.sdl2_backend",
		".python_backends.sdl3_backend",
	],
	"keyboard": [
		".__main__", "._keyboard_tests", "._mouse", ".mouse",
		"._darwin", "._nix", "._winmouse",
	],
	"OpenGL": [".gles", ".egl", ".tk", ".glut", ".gle"],
}

BINARY_DROP_PATTERNS: list[str] = [
	"freeglut32.vc9.dll", "freeglut64.vc9.dll",
	"gle32.vc9.dll", "gle64.vc9.dll",
	"qsqlibase.dll", "qsqlmimer.dll",
	"qsqloci.dll", "qsqlpsql.dll",
	"qtquickshapesdesignhelpersplugin.dll",
	"qt6webengine", "qt6webchannel", "qt6websockets", "qt6webview",
	"qt6quick", "qt6qml", "qt6pdf", "qt6designer", "qt6charts",
	"qt6datavisualization", "qt6multimedia", "qt6spatialaudio",
	"qt6sql", "qt6test", "qt6network", "qt6sensors", "qt6positioning",
	"qt63d", "qt6shadertools", "qt6bluetooth", "qt6nfc", "qt6serialport",
	"qt6opengl.dll", "qt6svg.dll", "qt6virtualkeyboard.dll",
	"avcodec-", "avformat-", "avutil-", "swscale-",
	"swresample-", "qt6labs",
	# Exclude ImGui documentation, examples and editor assets from runtime data
	"imgui_bundle/assets/", "imgui_bundle/demos_assets/",
	"imgui_bundle/demos_cpp/", "imgui_bundle/demos_python/",
	"imgui_bundle/python_backends/examples/",
	"imgui_bundle/python_backends/examples_disabled/",
	# In-memory pixmaps need only the Windows platform plugin; built-in UI text is English
	"pyside6/translations/", "pyside6/plugins/generic/",
	"pyside6/plugins/iconengines/", "pyside6/plugins/imageformats/",
	"pyside6/plugins/platforminputcontexts/",
	"pyside6/plugins/platforms/qdirect2d.dll",
	"pyside6/plugins/platforms/qminimal.dll",
	"pyside6/plugins/platforms/qoffscreen.dll",
	# Package documentation and excluded-module metadata are not runtime dependencies
	".dist-info/", "imgui_bundle/readme_", "imgui_bundle/license",
	"imgui_bundle/python_backends/readme",
	"imgui_bundle/python_backends/license",
]

BINARY_DROP_PATHS: set[str] = {
	# Share the required GLFW DLL instead of bundling pyGLFW copies tied to MSVCR120
	"glfw3.dll", "glfw/glfw3.dll", "glfw/msvcr120.dll",
}

DATA_DROP_SUFFIXES: tuple[str, ...] = (".py", ".pyi", ".lib")

CONFIG_RUNTIME_NAMES = frozenset({"instance.json", "status.json", "offset_cmd.json", "offset_ack.json"})
CONFIG_RUNTIME_DIRS = frozenset({"control", "softgl", "model_cache", "logs"})


def _ignore_runtime_config(directory: str, names: list[str]) -> set[str]:
	"""Filter first-build seed files without touching an existing DEV config."""
	if Path(directory).resolve() != CONFIG_SRC.resolve():
		return set()
	return {name for name in names if name.lower() in CONFIG_RUNTIME_NAMES | CONFIG_RUNTIME_DIRS
		or (name.startswith(".") and name.lower().endswith(".tmp"))}


GLFW_RUNTIME_HOOK = '''"""Use imgui_bundle's GLFW DLL for the Python glfw wrapper."""

import os
import sys

bundle_root = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
os.environ.setdefault(
    "PYGLFW_LIBRARY", os.path.join(bundle_root, "imgui_bundle", "glfw3.dll")
)
'''


def _print_usage() -> None:
	print('Usage: python build/build.py [--dev|--app] [--sync-config]')
	print(f'       Or set {BUILD_MODE_ENV} to DEV or APP in your shell, then run python build/build.py')
	print('       --sync-config backs up existing build settings and copies the current source default.toml')


def _clean(*dirs: Path) -> None:
	for target_dir in dirs:
		if target_dir.exists():
			rmtree(target_dir, ignore_errors=True)


def _write_spec(spec_path: Path, *, onefile: bool, console: bool) -> None:
	"""Render a PyInstaller spec file with custom binary filtering."""
	drop_patterns_lit = ", ".join(repr(pattern.lower()) for pattern in BINARY_DROP_PATTERNS)
	drop_paths_lit = ", ".join(repr(path.lower()) for path in sorted(BINARY_DROP_PATHS))
	drop_suffixes_lit = ", ".join(repr(suffix.lower()) for suffix in DATA_DROP_SUFFIXES)
	config_runtime_names_lit = repr(sorted(CONFIG_RUNTIME_NAMES))
	config_runtime_dirs_lit = repr(sorted(CONFIG_RUNTIME_DIRS))
	qta_font_prefix_lit = repr(QT_AWESOME_FONT_PREFIX.lower())
	excludes_lit = ", ".join(repr(module) for module in ALL_EXCLUDES)
	submodule_drop_lit = repr(SUBMODULE_DROP_PATTERNS)

	main_py = MAIN_PY.as_posix()
	config_src = CONFIG_SRC.as_posix()
	qt_assets_src = QT_ASSETS_SRC.as_posix()
	reset_defaults = RESET_DEFAULTS.as_posix()
	sounds_src = SOUNDS_SRC.as_posix()
	vpkmesh_dll = VPKMESH_DLL.as_posix()
	vpkmesh_exists = VPKMESH_DLL.exists()
	overlayperf_dll = OVERLAYPERF_DLL.as_posix()
	overlayperf_exists = OVERLAYPERF_DLL.exists()
	inputguard_dll = INPUTGUARD_DLL.as_posix()
	inputguard_exists = INPUTGUARD_DLL.exists()
	presentmon_exe = PRESENTMON_EXE.as_posix()
	presentmon_exists = PRESENTMON_EXE.exists()
	presentmon_licenses = PRESENTMON_LICENSES.as_posix()
	licenses_exist = PRESENTMON_LICENSES.is_dir()
	overlay_fonts = OVERLAY_FONTS.as_posix()
	fonts_exist = OVERLAY_FONTS.is_dir()
	pyside = importlib.util.find_spec("PySide6")
	software_gl = Path(pyside.origin).parent / "opengl32sw.dll" if pyside and pyside.origin else None
	software_gl_exists = bool(software_gl and software_gl.is_file())
	software_gl_path = software_gl.as_posix() if software_gl else ""
	hooks_dir = HOOKS_DIR.as_posix()
	runtime_hook_path = HOOKS_DIR / "pyi_rth_cs2esp_glfw.py"
	runtime_hook_path.parent.mkdir(parents=True, exist_ok=True)
	runtime_hook_path.write_text(GLFW_RUNTIME_HOOK, encoding="utf-8")
	runtime_hook = runtime_hook_path.as_posix()

	spec = f'''
# Auto-generated by build/build.py - do not edit by hand; regenerate it by re-running 'python build/build.py'.
from pathlib import Path
from PyInstaller.archive.writers import ZlibArchiveWriter
from PyInstaller.utils.hooks import collect_all

ZlibArchiveWriter._COMPRESSION_LEVEL = 9

DROP_PATTERNS = [{drop_patterns_lit}]
DROP_PATHS = {{{drop_paths_lit}}}
DROP_SUFFIXES = ({drop_suffixes_lit},)
CONFIG_RUNTIME_NAMES = {config_runtime_names_lit}
CONFIG_RUNTIME_DIRS = {config_runtime_dirs_lit}
QTA_FONT_PREFIX = {qta_font_prefix_lit}
SUBMODULE_DROP_PATTERNS = {submodule_drop_lit}
CONFIG_SOURCE = Path(r"{config_src}")


def _config_seed_entries():
    """Do not even scan transient IPC or the locally generated software GL copy."""
    entries = []
    for item in sorted(CONFIG_SOURCE.iterdir()):
        name = item.name.lower()
        if (name in CONFIG_RUNTIME_NAMES or name in CONFIG_RUNTIME_DIRS
                or (name.startswith(".") and name.endswith(".tmp"))):
            continue
        destination = "config/" + item.name if item.is_dir() else "config"
        entries.append((str(item), destination))
    return entries


def _make_submodule_filter(package):
    patterns = [pattern.lower() for pattern in SUBMODULE_DROP_PATTERNS.get(package, [])]
    if not patterns:
        return lambda name: True

    def _keep(name):
        lowered_name = name.lower()
        return not any(pattern in lowered_name for pattern in patterns)

    return _keep


def _drop_unwanted(items):
    kept = []
    dropped = 0
    for entry in items:
        dest = entry[0].replace("\\\\", "/").lower()
        basename = dest.rsplit("/", 1)[-1]
        unused_qta_font = (
            "qtawesome/fonts/" in dest
            and not basename.startswith(QTA_FONT_PREFIX)
        )
        config_relative = dest.removeprefix("config/") if dest.startswith("config/") else None
        runtime_config = config_relative is not None and (
            config_relative in CONFIG_RUNTIME_NAMES
            or any(config_relative.startswith(directory + "/") for directory in CONFIG_RUNTIME_DIRS)
            or ("/" not in config_relative and config_relative.startswith(".") and config_relative.endswith(".tmp"))
        )
        if (
            dest in DROP_PATHS
            or any(p in dest for p in DROP_PATTERNS)
            or dest.endswith(DROP_SUFFIXES)
            or unused_qta_font
            or runtime_config
        ):
            dropped += 1
            continue
        kept.append(entry)
    if dropped:
        print(f"[spec] dropped {{dropped}} unwanted binary entries")
    return kept


datas = []
binaries = []
hiddenimports = [
    "tomli_w",
    "qtawesome",
    # Native nanobind code imports NumPy during the first texture upload, so static analysis cannot discover it.
    "numpy",
    "imgui_bundle.python_backends.glfw_backend",
    "cs2esp.ui.qt.menu_app",
    "cs2esp.ui.qt.main_window",
    "cs2esp.diagnostics",
    "OpenGL.GL",
    "OpenGL.GL.shaders",
    "OpenGL.arrays",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
]
for package in ("pymem", "imgui_bundle", "glfw", "keyboard", "qtawesome"):
    package_datas, package_binaries, package_hidden = collect_all(
        package,
        filter_submodules=_make_submodule_filter(package),
        on_error="ignore",
    )
    datas += _drop_unwanted(package_datas)
    binaries += _drop_unwanted(package_binaries)
    hiddenimports += package_hidden

datas += [
    (r"{qt_assets_src}", "cs2esp/ui/qt/assets"),
    (r"{reset_defaults}", "cs2esp/assets"),
]
datas += _config_seed_entries()
datas += [(str(path), "cs2esp/sounds") for path in sorted(Path(r"{sounds_src}").glob("*.wav")) if path.is_file()]
if {vpkmesh_exists}:
    binaries += [(r"{vpkmesh_dll}", "cs2esp/assets/bin")]
if {overlayperf_exists}:
    binaries += [(r"{overlayperf_dll}", "cs2esp/assets/bin")]
if {inputguard_exists}:
    binaries += [(r"{inputguard_dll}", "cs2esp/assets/bin")]
if {presentmon_exists}:
    # Keep the signed standalone executable byte-for-byte; do not process it as a PyInstaller binary.
    datas += [(r"{presentmon_exe}", "cs2esp/assets/bin")]
if {software_gl_exists}:
    binaries += [(r"{software_gl_path}", "PySide6")]
if {licenses_exist}:
    datas += [(r"{presentmon_licenses}", "cs2esp/assets/licenses/presentmon")]
if {fonts_exist}:
    datas += [(r"{overlay_fonts}", "cs2esp/assets/fonts")]


a = Analysis(
    [r"{main_py}"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[r"{hooks_dir}"],
    hooksconfig={{}},
    runtime_hooks=[r"{runtime_hook}"],
    excludes=[{excludes_lit}],
    noarchive=False,
    optimize=2,
)

a.binaries = _drop_unwanted(a.binaries)
a.datas = _drop_unwanted(a.datas)

pyz = PYZ(a.pure, a.zipped_data)
'''

	if onefile:
		spec += f'''
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name={EXE_NAME!r},
    icon=r"{qt_assets_src}/app.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console={console},
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
'''
	else:
		spec += f'''
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name={EXE_NAME!r},
    icon=r"{qt_assets_src}/app.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console={console},
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory=".",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name={EXE_NAME!r},
)
'''

	spec_path.parent.mkdir(parents=True, exist_ok=True)
	spec_path.write_text(spec, encoding="utf-8")


def _run_spec(spec_path: Path, dist_dir: Path) -> int:
	"""Invoke PyInstaller against the generated spec."""
	command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
		"--workpath", str(WORK_DIR), "--distpath", str(dist_dir), str(spec_path)]
	upx_dir = environ.get("CS2_UPX_DIR")
	if not upx_dir and (BUILD_DIR / "_tools" / "upx.exe").is_file():
		upx_dir = str(BUILD_DIR / "_tools")
	if not upx_dir:
		upx_path = which("upx")
		upx_dir = str(Path(upx_path).parent) if upx_path else None
	if upx_dir:
		command.extend(["--upx-dir", upx_dir])
	environment = dict(environ)
	if upx_dir:
		environment.setdefault("UPX", "--best")
	if sys.platform == "win32":
		# Resolve dependencies from Python and Windows, not unrelated SDK directories inherited from the shell
		windows_dir = Path(environ["SystemRoot"])
		search_dirs = (Path(sys.executable).parent, Path(sys.base_prefix), Path(sys.base_prefix) / "DLLs", windows_dir / "System32", windows_dir)
		environment["PATH"] = pathsep.join(str(path) for path in search_dirs if path.is_dir())
	return subprocess.call(command, cwd=str(ROOT), env=environment)


def _flatten_pyinstaller_output(parent: Path) -> bool:
	"""Move PyInstaller's nested onedir output up to the build folder."""
	nested = parent / EXE_NAME
	if not nested.is_dir():
		return False
	collision_stash: Path | None = None
	entries = list(nested.iterdir())
	for entry in entries:
		target = parent / entry.name
		if target.resolve() == nested.resolve():
			if collision_stash is None:
				collision_stash = Path(mkdtemp(prefix="cs2esp-flatten-", dir=str(parent)))
			move(str(entry), str(collision_stash / entry.name))
			continue
		if target.exists():
			if target.is_dir():
				rmtree(target)
			else:
				target.unlink()
		move(str(entry), str(target))
	try:
		nested.rmdir()
	except OSError:
		if collision_stash is not None:
			for entry in collision_stash.iterdir():
				move(str(entry), str(nested / entry.name))
			rmtree(collision_stash, ignore_errors=True)
		return False
	if collision_stash is not None:
		for entry in collision_stash.iterdir():
			move(str(entry), str(parent / entry.name))
		rmtree(collision_stash, ignore_errors=True)
	return True


def _sync_build_config(output: Path) -> bool:
	'''Explicitly copy source settings into this local build, preserving a backup and all runtime caches.'''
	temporary: Path | None = None
	try:
		source = CONFIG_SRC / "default.toml"
		contents = source.read_bytes()
		tomllib.loads(contents.decode("utf-8"))
		target = output / "config" / "default.toml"
		target.parent.mkdir(parents=True, exist_ok=True)
		if target.exists():
			if target.read_bytes() == contents:
				print(f'[build] Settings already match source -> {target}')
				return True
			backup = Path(mkdtemp(prefix=f"cs2esp-{output.name.lower()}-cfg-", dir=str(BUILD_DIR)))
			copy2(target, backup / "default.toml")
			print(f'[build] Previous settings backed up -> {backup}')
		temp_fd, temp_name = mkstemp(prefix=".default-build-", suffix=".toml.tmp", dir=str(target.parent))
		temporary = Path(temp_name)
		with fdopen(temp_fd, "wb") as config_file:
			config_file.write(contents)
			config_file.flush()
			fsync(config_file.fileno())
		temporary.replace(target)
		print(f'[build] Settings synchronized from source -> {target}')
		return True
	except (OSError, ValueError) as error:
		print(f'[build] Config synchronization failed: {error}')
		return False
	finally:
		if temporary is not None:
			temporary.unlink(missing_ok=True)


def build_dev(*, sync_config: bool = False) -> int:
	"""Build DEV mode into build/DEV."""
	print("[build] Mode: DEV - onedir build with console -> build/DEV/")
	_clean(WORK_DIR, SPEC_DIR)

	saved_config: Path | None = None
	existing_config = DEV_DIR / "config"
	if existing_config.is_dir():
		saved_config = Path(mkdtemp(prefix="cs2esp-cfg-", dir=str(BUILD_DIR)))
		copytree(existing_config, saved_config / "config")
		print(f"[build] Preserved existing config -> {saved_config}")

	if DEV_DIR.exists():
		rmtree(DEV_DIR, ignore_errors=True)
	DEV_DIR.mkdir(parents=True, exist_ok=True)

	output_config = DEV_DIR / "config"
	try:
		spec_path = SPEC_DIR / "cs2esp_dev.spec"
		_write_spec(spec_path, onefile=False, console=True)
		build_exit_code = _run_spec(spec_path, DEV_DIR)
		if build_exit_code != 0:
			print(f"[build] PyInstaller failed (exit_code={build_exit_code})")
			return build_exit_code

		if not _flatten_pyinstaller_output(DEV_DIR):
			print("[build] WARNING: nested cs2esp/ not found, layout may be off")

		target_executable = DEV_DIR / f"{EXE_NAME}.exe"
		if not target_executable.exists():
			print(f'[build] PyInstaller succeeded but {target_executable} is missing')
			return 1
	finally:
		preserved_config = saved_config / "config" if saved_config is not None else None
		if preserved_config is not None and preserved_config.is_dir():
			if output_config.exists():
				rmtree(output_config)
			move(str(preserved_config), str(output_config))
			rmtree(saved_config, ignore_errors=True)
			print(f"[build] Restored user config -> {output_config}")

	if saved_config is None:
		if output_config.exists():
			rmtree(output_config)
		copytree(CONFIG_SRC, output_config, ignore=_ignore_runtime_config)
		print(f"[build] Seeded config from source -> {output_config}")

	if sync_config and not _sync_build_config(DEV_DIR):
		return 1
	_print_done(target_executable, output_config, mode="DEV")
	return 0


def build_app(*, sync_config: bool = False) -> int:
	"""Build APP mode into build/APP/cs2esp.exe."""
	print("[build] Mode: APP - single-file build -> build/APP/cs2esp.exe")
	_clean(WORK_DIR, SPEC_DIR, APP_STAGE_DIR)
	APP_STAGE_DIR.mkdir(parents=True, exist_ok=True)

	spec_path = SPEC_DIR / "cs2esp_app.spec"
	_write_spec(spec_path, onefile=True, console=False)
	build_exit_code = _run_spec(spec_path, APP_STAGE_DIR)
	if build_exit_code != 0:
		print(f"[build] PyInstaller failed (exit_code={build_exit_code})")
		return build_exit_code

	staged_executable = APP_STAGE_DIR / f"{EXE_NAME}.exe"
	if not staged_executable.exists():
		print(f"[build] PyInstaller succeeded but {staged_executable} is missing")
		return 1

	APP_DIR.mkdir(parents=True, exist_ok=True)
	target_executable = APP_DIR / f"{EXE_NAME}.exe"
	try:
		staged_executable.replace(target_executable)
	except OSError as exc:
		print(f"[build] Could not replace {target_executable}: {exc}")
		print(f"[build] Close the running app, then move {staged_executable} into APP.")
		return 1
	rmtree(APP_STAGE_DIR, ignore_errors=True)

	if sync_config and not _sync_build_config(APP_DIR):
		return 1
	output_config = APP_DIR / "config"
	_print_done(target_executable, output_config if (output_config / "default.toml").exists() else None, mode="APP")
	return 0


def _print_done(exe_path: Path, config_dir: Path | None, *, mode: str) -> None:
	size_mib = 0.0
	try:
		size_mib = exe_path.stat().st_size / (1024 * 1024)
	except OSError:
		pass
	print()
	print("=" * 64)
	print(f"[build] DONE ({mode} mode)")
	print(f"[build] Executable : {exe_path}  ({size_mib:.1f} MiB)")
	if config_dir is not None:
		print(f"[build] Config dir : {config_dir}")
		if (config_dir / "default.toml").read_bytes() != (CONFIG_SRC / "default.toml").read_bytes():
			print("[build] Settings   : existing user settings retained; --sync-config copies the source settings instead")
	else:
		print("[build] Config     : embedded; seeded next to .exe on first run")
	print("[build] Manifest   : asInvoker (no UAC prompt)")
	print("=" * 64)


def main() -> int:
	args = sys.argv[1:]
	sync_config = "--sync-config" in args
	if args.count("--sync-config") > 1:
		_print_usage()
		return 2
	args = [arg for arg in args if arg != "--sync-config"]
	mode = environ.get(BUILD_MODE_ENV, BUILD_MODE).upper().strip()
	if args:
		if len(args) != 1:
			_print_usage()
			return 2
		requested_mode = args[0].strip().lower()
		if requested_mode in ("-h", "--help", "help"):
			_print_usage()
			return 0
		if requested_mode in ("dev", "--dev"):
			mode = "DEV"
		elif requested_mode in ("app", "--app"):
			mode = "APP"
		else:
			print(f"[build] Unknown argument: {args[0]!r}")
			_print_usage()
			return 2
	if mode not in ("DEV", "APP"):
		print(f"[build] Unknown BUILD_MODE = {mode!r} (expected DEV or APP)")
		return 2

	if importlib.util.find_spec("PyInstaller") is None:
		print("[build] Installing PyInstaller into the active venv...")
		subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

	missing_dependencies: list[str] = []
	for dependency in ("pymem", "imgui_bundle", "OpenGL", "glfw", "keyboard", "numpy", "tomli_w", "PySide6", "qtawesome"):
		try:
			__import__(dependency)
		except ImportError:
			missing_dependencies.append(dependency)
	if missing_dependencies:
		print('[build] Installing missing runtime deps: ' + ", ".join(missing_dependencies))
		subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])

	if mode == "DEV":
		return build_dev(sync_config=sync_config)
	if mode == "APP":
		return build_app(sync_config=sync_config)
	return 2


if __name__ == "__main__":
	raise SystemExit(main())
