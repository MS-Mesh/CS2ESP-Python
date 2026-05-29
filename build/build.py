"""
PyInstaller build driver for CS2 ESP.

Builds DEV onedir and APP onefile releases from the same runtime entry
point. The script prepares a generated PyInstaller spec, trims noisy
optional plugin bundles, preserves user-edited config folders across
rebuilds, and exposes CLI/env mode selection without editing this file.
"""

import importlib.util
import subprocess
import sys
from os import environ
from pathlib import Path
from shutil import copytree, move, rmtree
from tempfile import mkdtemp

BUILD_MODE: str = "APP"
BUILD_MODE_ENV = "CS2_BUILD_MODE"
EXE_NAME:   str = "cs2esp"

ROOT      = Path(__file__).resolve().parent.parent
BUILD_DIR = Path(__file__).resolve().parent
WORK_DIR  = BUILD_DIR / "_work"
SPEC_DIR  = BUILD_DIR / "_spec"
HOOKS_DIR = BUILD_DIR / "_hooks"
DEV_DIR   = BUILD_DIR / "DEV"
APP_DIR   = BUILD_DIR / "APP"
APP_STAGE_DIR = BUILD_DIR / "_app_stage"

MAIN_PY    = ROOT / "main.py"
CONFIG_SRC = ROOT / "config"
QT_ASSETS_SRC = ROOT / "cs2esp" / "ui" / "qt" / "assets"
QT_AWESOME_FONT_PREFIX = "materialdesignicons5-"


ALL_EXCLUDES: list[str] = [
    "tkinter", "unittest", "pydoc", "doctest",
    "pytest", "lib2to3", "tzdata",
    # glfw's optional CFFI bridge is only for Vulkan pointer interop; this OpenGL app never compiles extensions at runtime.
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
    # Excluding unused PySide6 Python shims stops PyInstaller pulling their large backing DLLs into this widgets-only menu.
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.QtWebSockets", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.QtQml", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtDesigner", "PySide6.QtCharts", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtNetwork", "PySide6.QtSql",
    "PySide6.Qt3DCore", "PySide6.QtDataVisualization", "PySide6.QtTest",
    "PySide6.QtSensors", "PySide6.QtPositioning", "PySide6.QtBluetooth",
    # Requests treats Brotli and Zstandard as optional and does not advertise either encoding when its decoder is absent.
    "brotli", "_brotli", "brotlicffi", "zstandard", "_zstd",
    "compression.zstd",
    # Offset JSON uses urllib from the standard library, so the third-party HTTP stack is unnecessary.
    "requests", "urllib3", "certifi", "charset_normalizer", "idna",
    # The HTTPS offset fetch uses stdlib ssl, so cryptography's ~9 MB _rust.pyd is unused while libcrypto/libssl remain bundled.
    "cryptography",
]

if sys.version_info >= (3, 11):
    # Python 3.11+ provides tomllib in the standard library.
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
    # These are Qt modules a widgets-only menu never loads; WebEngine and its codecs alone are ~210 MB, while opengl32sw is an unused software fallback.
    "qt6webengine", "qt6webchannel", "qt6websockets", "qt6webview",
    "qt6quick", "qt6qml", "qt6pdf", "qt6designer", "qt6charts",
    "qt6datavisualization", "qt6multimedia", "qt6spatialaudio",
    "qt6sql", "qt6test", "qt6network", "qt6sensors", "qt6positioning",
    "qt63d", "qt6shadertools", "qt6bluetooth", "qt6nfc", "qt6serialport",
    "qt6opengl.dll", "qt6svg.dll", "qt6virtualkeyboard.dll",
    "opengl32sw.dll", "avcodec-", "avformat-", "avutil-", "swscale-",
    "swresample-", "qt6labs",
    # collect_all() otherwise copies ImGui's documentation, demos, sample assets, and editor stubs into the executable as runtime data.
    "imgui_bundle/assets/", "imgui_bundle/demos_assets/",
    "imgui_bundle/demos_cpp/", "imgui_bundle/demos_python/",
    "imgui_bundle/python_backends/examples/",
    "imgui_bundle/python_backends/examples_disabled/",
    # The app creates pixmaps in memory and only needs the native Windows platform plugin; translations fall back to built-in English.
    "pyside6/translations/", "pyside6/plugins/generic/",
    "pyside6/plugins/iconengines/", "pyside6/plugins/imageformats/",
    "pyside6/plugins/platforminputcontexts/",
    "pyside6/plugins/platforms/qdirect2d.dll",
    "pyside6/plugins/platforms/qminimal.dll",
    "pyside6/plugins/platforms/qoffscreen.dll",
    # collect_all() metadata and package documentation are not queried by the runtime; pydantic metadata must also go because that module is intentionally excluded.
    ".dist-info/", "imgui_bundle/readme_", "imgui_bundle/license",
    "imgui_bundle/python_backends/readme",
    "imgui_bundle/python_backends/license",
]

BINARY_DROP_PATHS: set[str] = {
    # pyGLFW's two copies use the obsolete MSVCR120 runtime; a runtime hook points it at imgui_bundle's already-required GLFW DLL instead.
    "glfw3.dll", "glfw/glfw3.dll", "glfw/msvcr120.dll",
}

DATA_DROP_SUFFIXES: tuple[str, ...] = (".py", ".pyi", ".lib")

GLFW_RUNTIME_HOOK = '''"""Use imgui_bundle's GLFW DLL for the Python glfw wrapper."""

import os
import sys

bundle_root = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
os.environ.setdefault(
    "PYGLFW_LIBRARY", os.path.join(bundle_root, "imgui_bundle", "glfw3.dll")
)
'''


def _print_usage() -> None:
    print("Usage: python build/build.py [--dev|--app]")
    print(
        f"       Or set {BUILD_MODE_ENV} to DEV or APP in your shell, "
        "then run python build/build.py"
    )


def _clean(*dirs: Path) -> None:
    for target_dir in dirs:
        if target_dir.exists():
            rmtree(target_dir, ignore_errors=True)


def _write_spec(spec_path: Path, *, onefile: bool, console: bool) -> None:
    """Render a PyInstaller spec file with custom binary filtering."""
    drop_patterns_lit = ", ".join(repr(p.lower()) for p in BINARY_DROP_PATTERNS)
    drop_paths_lit = ", ".join(repr(p.lower()) for p in sorted(BINARY_DROP_PATHS))
    drop_suffixes_lit = ", ".join(repr(s.lower()) for s in DATA_DROP_SUFFIXES)
    qta_font_prefix_lit = repr(QT_AWESOME_FONT_PREFIX.lower())
    excludes_lit = ", ".join(repr(m) for m in ALL_EXCLUDES)
    submodule_drop_lit = repr(SUBMODULE_DROP_PATTERNS)

    main_py = MAIN_PY.as_posix()
    config_src = CONFIG_SRC.as_posix()
    qt_assets_src = QT_ASSETS_SRC.as_posix()
    hooks_dir = HOOKS_DIR.as_posix()
    runtime_hook_path = HOOKS_DIR / "pyi_rth_cs2esp_glfw.py"
    runtime_hook_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_hook_path.write_text(GLFW_RUNTIME_HOOK, encoding="utf-8")
    runtime_hook = runtime_hook_path.as_posix()

    spec = f'''
# Auto-generated by build/build.py - do not edit by hand; regenerate it by re-running 'python build/build.py'.
from PyInstaller.utils.hooks import collect_all

DROP_PATTERNS = [{drop_patterns_lit}]
DROP_PATHS = {{{drop_paths_lit}}}
DROP_SUFFIXES = ({drop_suffixes_lit},)
QTA_FONT_PREFIX = {qta_font_prefix_lit}
SUBMODULE_DROP_PATTERNS = {submodule_drop_lit}


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
        if (
            dest in DROP_PATHS
            or any(p in dest for p in DROP_PATTERNS)
            or dest.endswith(DROP_SUFFIXES)
            or unused_qta_font
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
    (r"{config_src}", "config"),
    (r"{qt_assets_src}", "cs2esp/ui/qt/assets"),
]


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
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--workpath", str(WORK_DIR),
        "--distpath", str(dist_dir),
        str(spec_path),
    ]
    return subprocess.call(cmd, cwd=str(ROOT))


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
                collision_stash = Path(mkdtemp(
                    prefix="cs2esp-flatten-", dir=str(parent)
                ))
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


def build_dev() -> int:
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
            print(
                f"[build] PyInstaller succeeded but {target_executable} "
                "is missing"
            )
            return 1
    finally:
        preserved_config = (
            saved_config / "config" if saved_config is not None else None
        )
        if preserved_config is not None and preserved_config.is_dir():
            if output_config.exists():
                rmtree(output_config)
            move(str(preserved_config), str(output_config))
            rmtree(saved_config, ignore_errors=True)
            print(f"[build] Restored user config -> {output_config}")

    if saved_config is None:
        if output_config.exists():
            rmtree(output_config)
        copytree(CONFIG_SRC, output_config)
        print(f"[build] Seeded config from source -> {output_config}")

    _print_done(target_executable, output_config, mode="DEV")
    return 0


def build_app() -> int:
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

    _print_done(target_executable, None, mode="APP")
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
    else:
        print("[build] Config     : embedded; seeded next to .exe on first run")
    print("[build] Manifest   : asInvoker (no UAC prompt)")
    print("=" * 64)


def main() -> int:
    args = sys.argv[1:]
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
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pyinstaller"]
        )

    missing_dependencies: list[str] = []
    for dependency in (
        "pymem", "imgui_bundle", "OpenGL", "glfw", "keyboard", "numpy",
        "tomli_w", "PySide6", "qtawesome",
    ):
        try:
            __import__(dependency)
        except ImportError:
            missing_dependencies.append(dependency)
    if missing_dependencies:
        print(
            "[build] Installing missing runtime deps: "
            + ", ".join(missing_dependencies)
        )
        subprocess.check_call([
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            str(ROOT / "requirements.txt"),
        ])

    if mode == "DEV":
        return build_dev()
    if mode == "APP":
        return build_app()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
