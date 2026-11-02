# Contributing

Keep changes focused, reproducible, and reviewable.

MS Mesh CS2 ESP is a Windows x64 Python application with a separate Qt control
center, an external OpenGL overlay, and optional native C++ helpers. Before
working on a change, read the relevant sections of `README.md` and preserve the
project's existing runtime, configuration, and packaging behavior.

## Development setup

Use 64-bit Python 3.11 or newer on Windows. The current build is tested with
Python 3.14.

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Run the source application with:

```powershell
.\.venv\Scripts\python.exe main.py
```

The application reads and writes user configuration under `config/`. Do not
include local logs, IPC files, model caches, crash dumps, or personal
configuration changes in a contribution.

## Build and verify

This repository does not use CMake or a website build. Use the packaging driver
that matches the change:

```powershell
.\.venv\Scripts\python.exe build\build.py --app
.\.venv\Scripts\python.exe build\build.py --dev
```

The APP build creates `build\APP\cs2esp.exe`; the DEV build creates
`build\DEV\cs2esp.exe` and its dependency directory. Generated build output is
local verification material and must not be committed.

Before submitting a pull request:

1. Install `requirements-dev.txt`.
2. Run the source application or the relevant packaged build.
3. Check the changed behavior in the running client, including the relevant
   Qt menu and overlay path.
4. Run available checks for the changed Python modules. If no automated test
   covers the change, describe the manual verification steps.
5. If native code changed, rebuild the affected helper with its
   `cs2esp\native\*\build.bat` script and verify the packaged client.

Keep process access external and read-only. Do not add DLL injection, in-process
hooks, game-memory writes, or code that broadens access beyond what the
existing application requires.

## Making changes

- Keep each pull request focused on one change.
- Follow the existing Python formatting, naming, configuration, and UI
  patterns.
- Preserve user settings and existing configuration migration behavior.
- Keep error handling visible through the project's existing logging or UI
  notification paths.
- Update `README.md` when user-visible commands, behavior, or requirements
  change.
- Preserve third-party copyright, license, and notice files.
- Do not add credentials, private dumps, generated build trees, proprietary
  assets, or personal logs.

For changes that affect runtime behavior, explain the observable result in the
pull request: what changed, how it was reproduced, and what was checked in the
running client. Include the Windows version, GPU, Python version, build mode,
and relevant logs when reporting a platform-specific issue. Remove personal or
machine-specific information from logs before sharing them.

## Pull requests

Use a branch created from the current `main` branch and keep commits
descriptive. A pull request should include:

- a short summary of the change and its motivation;
- the user-visible behavior change, if any;
- exact verification commands and manual steps;
- screenshots or recordings for meaningful UI changes;
- notes about configuration, native-helper, packaging, or license impacts.

Do not submit generated APP/DEV output or other build artifacts. Review the
diff before opening the pull request and confirm that only intended files are
included.

## Issues

For a bug report, include the source or APP version, Windows version, GPU,
reproduction steps, expected behavior, observed behavior, and the relevant
sanitized log. For UI issues, include display scaling and a screenshot when
possible.
