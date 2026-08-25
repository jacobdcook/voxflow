@echo off
REM mintflow cross-platform build queue (A-J)
REM On Linux: bash auto_build_mintflow.sh
REM On Windows: double-click this, or run from Git Bash / WSL
cd /d "%~dp0"

if exist "%ProgramFiles%\Git\bin\bash.exe" (
  "%ProgramFiles%\Git\bin\bash.exe" auto_build_mintflow.sh %*
  exit /b %ERRORLEVEL%
)
if exist "%ProgramFiles(x86)%\Git\bin\bash.exe" (
  "%ProgramFiles(x86)%\Git\bin\bash.exe" auto_build_mintflow.sh %*
  exit /b %ERRORLEVEL%
)

where wsl >nul 2>&1
if %ERRORLEVEL%==0 (
  wsl -e bash auto_build_mintflow.sh %*
  exit /b %ERRORLEVEL%
)

where bash >nul 2>&1
if %ERRORLEVEL%==0 (
  bash auto_build_mintflow.sh %*
  exit /b %ERRORLEVEL%
)

echo.
echo On this Linux box run:
echo   cd "/home/z1337/Desktop/PROJECTS/mintflow"
echo   bash auto_build_mintflow.sh
echo.
echo On Windows install Git Bash, then double-click this bat again.
pause
exit /b 1
