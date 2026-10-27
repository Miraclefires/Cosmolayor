@echo off
rem Build Cosmolayor.exe from tools\launcher.cs (needs only the .NET Framework csc.exe
rem that ships with Windows - nothing to install).
rem NOTE: keep this file ASCII-only; cmd.exe reads .cmd as the OEM codepage.
setlocal
cd /d "%~dp0.."

set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not exist "%CSC%" (
  echo [x] csc.exe not found - needs .NET Framework 4.x ^(built into Win10/11^)
  pause
  exit /b 1
)

"%CSC%" /nologo /target:exe /platform:x64 /optimize+ /codepage:65001 ^
  /out:Cosmolayor.exe ^
  /r:System.dll ^
  tools\launcher.cs

if errorlevel 1 (
  echo [x] build failed
  pause
  exit /b 1
)
echo [ok] Cosmolayor.exe -^> %CD%\Cosmolayor.exe
