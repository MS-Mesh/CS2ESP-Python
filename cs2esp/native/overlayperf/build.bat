@echo off
rem Builds overlayperf.dll (OpenMP bone/projection math) into cs2esp\assets\bin.
rem Needs Visual Studio 2022+ with the "Desktop development with C++" workload.
setlocal
set "HERE=%~dp0"
set "OUT=%HERE%..\..\assets\bin"
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
set "COMPONENT=Microsoft.VisualStudio.Component.VC.Tools.x86.x64"
for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires %COMPONENT% -property installationPath`) do set "VS=%%i"
if not defined VS (
	echo MSVC not found. Install Visual Studio with the C++ workload.
	exit /b 1
)
call "%VS%\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
if not exist "%OUT%" mkdir "%OUT%"
pushd "%HERE%"
cl /nologo /O2 /EHsc /std:c++20 /W0 /MT /LD /openmp /fp:fast perf.cpp /Fe"%OUT%\overlayperf.dll" /link /NOLOGO
set "RC=%ERRORLEVEL%"
popd
del /q "%OUT%\overlayperf.exp" "%OUT%\overlayperf.lib" "%OUT%\overlayperf.obj" "%HERE%\perf.obj" 2>nul
exit /b %RC%
