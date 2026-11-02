# Contributing to MS Mesh

Contributions, bug reports, and focused improvements are welcome. Keep changes small, reproducible, and easy to review.

MS Mesh is a Windows x64 Python application with a separate PySide6 control center, an external OpenGL overlay, and native C++ helpers. Read [README.md](README.md) before making changes, especially the configuration and packaging sections.

## Development setup

Use **64-bit Python 3.11 or newer on Windows**. The current release was tested with Python 3.14.

From the repository root:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Launch the application from source:

```powershell
.\.venv\Scripts\python.exe main.py
```

[requirements-dev.txt](requirements-dev.txt) includes the runtime dependencies and optional pytest/Hypothesis tools. The internal regression suite is not distributed in this repository; installing development dependencies does not add those tests.

## Project layout

| Path | Purpose |
| --- | --- |
| `main.py` | Runtime entry point and overlay/menu orchestration |
| `cs2esp/` | Application modules, UI, rendering, and feature implementations |
| `cs2esp/native/` | Native-helper sources and Windows build scripts |
| `cs2esp/assets/` | Bundled assets, fonts, and native binaries |
| `cs2esp/sounds/` | Bundled audio resources |
| `config/default.toml` | Source configuration and defaults embedded during packaging |
| `build/build.py` | APP/DEV packaging driver |
| `media/` | README screenshots and illustrations |

## Build and verification

Use the Python packaging driver; there is no CMake or website build step:

```powershell
.\.venv\Scripts\python.exe build\build.py --app
.\.venv\Scripts\python.exe build\build.py --dev
```

- **APP:** `build/APP/cs2esp.exe`, a single-file application without a console.
- **DEV:** `build/DEV/cs2esp.exe` and its dependencies, with a console for debugging. Keep the entire output directory together.

Both modes include Python and runtime dependencies. Generated output is verification material, not source: do not commit APP/DEV directories, executables, or temporary build files.

For native changes, install Visual Studio's **Desktop development with C++** workload and rebuild the affected helper:

```powershell
.\cs2esp\native\vpkmesh\build.bat
.\cs2esp\native\overlayperf\build.bat
.\cs2esp\native\inputguard\build.bat
```

Verify the packaged client after changing native code, exports, resource paths, or build scripts. Do not edit generated binaries by hand.

Before opening a pull request:

1. Run the relevant source or packaged application path.
2. Reproduce the changed behavior and check the affected menu and overlay components.
3. Run applicable checks and any tests supplied with the contribution.
4. Check settings preservation and source/APP parity when configuration or packaging changes.
5. Record the exact commands, results, and manual steps. Clearly identify anything that was not verified.

Do not claim that automated tests passed when they were unavailable or not run. Documentation-only changes do not require a live game session or a rebuilt executable.

## Configuration and user data

Source launches use the project's `config/`. Packaged launches use `config/` beside the executable. Existing user configuration takes priority over embedded defaults.

- Preserve saved settings, keybinds, HUD positions/scales, and migration behavior.
- Do not change public defaults merely to match your local machine or test setup.
- Use a separate working copy or isolated configuration for destructive checks.
- Keep personal logs, IPC files, model caches, crash dumps, and local configuration changes out of commits.

For deliberate local config synchronization, `build/build.py --app --sync-config` (or `--dev --sync-config`) copies the current source defaults to the build and backs up its previous settings. Normal builds should preserve existing user preferences.

## Making changes

- Keep each pull request focused on one improvement or fix.
- Follow nearby formatting and naming conventions. Use English for code, comments, and UI text; prefer concise comments that explain non-obvious decisions.
- Keep cosmetic refactoring separate from behavior changes where practical.
- Preserve the external-process architecture and existing configuration, resource-loading, and packaging contracts.
- Do not broaden process permissions or introduce a new access mechanism as an incidental part of an unrelated change.
- Keep errors visible through the existing logging or UI notification paths; avoid silent failures and unnecessary fallback chains.
- Update the README when commands, requirements, or user-visible behavior change.
- Preserve third-party licenses, copyright notices, and attribution.
- Never commit credentials, private dumps, proprietary assets, personal logs, or generated build trees.

## Pull requests

Create a branch from the current `main`, use descriptive commit messages, and review the final diff before submitting. Include:

- A short summary and the reason for the change.
- The observable behavior before and after, when relevant.
- Exact verification commands and manual reproduction steps.
- Screenshots or recordings for meaningful UI changes.
- Any configuration, native-helper, packaging, dependency, or license impact.

Avoid unrelated cleanup, bulk formatting, and generated files in a focused fix. New resources and dependencies should have a clear purpose and compatible licensing.

## Issues

For a bug report, provide:

- Source commit or APP release version.
- Windows version, GPU, and display scaling; Python version for source launches.
- Build mode: source, APP, or DEV.
- Reproduction steps, expected behavior, and observed behavior.
- Relevant sanitized logs and, for visual problems, a screenshot or short recording.

Remove usernames, machine-specific paths, credentials, and other personal information before sharing logs. If you have an improvement idea rather than a bug, describe the intended result and how it fits the existing application.

## Contact

Use GitHub Issues for reproducible problems and pull requests for proposed changes. For collaboration, contact **MS Mesh** on [Discord](https://discord.com/users/492755314127536179) or [Telegram](https://t.me/test_number2).
