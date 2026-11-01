@echo off
rem Builds the process-owned right-button filter; no game module is injected.
setlocal
set "HERE=%~dp0"
set "OUT=%HERE%..\..\assets\bin"
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VS=%%i"
if not defined VS exit /b 1
call "%VS%\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
if not exist "%OUT%" mkdir "%OUT%"
pushd "%HERE%"
cl /nologo /O2 /EHsc /std:c++20 /W4 /MT /LD guard.cpp /Fe"%OUT%\inputguard.dll" /link /NOLOGO user32.lib
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%
