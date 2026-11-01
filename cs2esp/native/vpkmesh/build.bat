@echo off
rem Builds vpkmesh.dll (VPK + Source 2 mesh decoder) into cs2esp\assets\bin.
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
set "OBJ=%TEMP%\vpkmesh_obj"
if not exist "%OBJ%" mkdir "%OBJ%"
pushd "%HERE%"
cl /nologo /O2 /EHsc /std:c++20 /utf-8 /W0 /MT /LD /DZSTD_DISABLE_ASM ^
	/I. /Izstd /Imeshopt ^
	vpkmesh.cpp zstd\ZstdImpl.cpp meshopt\Allocator.cpp meshopt\VertexCodec.cpp meshopt\IndexCodec.cpp ^
	/Fo"%OBJ%\\" /Fe"%OUT%\vpkmesh.dll" /link /NOLOGO advapi32.lib
set "RC=%ERRORLEVEL%"
popd
del /q "%OUT%\vpkmesh.exp" "%OUT%\vpkmesh.lib" 2>nul
exit /b %RC%
