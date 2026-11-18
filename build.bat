@echo off
setlocal
cd /d "%~dp0"
set "LOG=build.log"
echo Build started %date% %time% > "%LOG%"

set "PY=python"
where py >nul 2>&1 && set "PY=py -3"

echo [1/5] Setting up Python environment
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv >> "%LOG%" 2>&1
  if errorlevel 1 goto :fail
)
set "VPY=.venv\Scripts\python.exe"
"%VPY%" -m pip install -q --disable-pip-version-check -r requirements.txt >> "%LOG%" 2>&1
if errorlevel 1 goto :fail

echo [2/5] Generating icons
"%VPY%" make_icons.py >> "%LOG%" 2>&1
if errorlevel 1 goto :fail

echo [3/5] Building app
"%VPY%" -m PyInstaller --noconfirm --clean --log-level ERROR MrBAI.spec >> "%LOG%" 2>&1
if errorlevel 1 goto :fail

echo [4/5] Building installer
call :find_iscc
if not defined ISCC (
  echo       Installing Inno Setup...
  winget install --id JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements >> "%LOG%" 2>&1
  call :find_iscc
)
if not defined ISCC (
  echo Inno Setup was not found. Install it from https://jrsoftware.org/isdl.php and run this again.
  goto :fail
)
"%ISCC%" /Q installer.iss >> "%LOG%" 2>&1
if errorlevel 1 goto :fail

echo [5/5] Running installer
start /wait "" "installer\MrBAI-Setup.exe" /SILENT /SUPPRESSMSGBOXES /NORESTART
echo Done. Mr. B AI is installed.
exit /b 0

:find_iscc
set "ISCC="
for %%P in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe") do (
  if exist "%%~P" set "ISCC=%%~P"
)
exit /b 0

:fail
echo.
echo Build failed. Last lines of %LOG%:
powershell -NoProfile -Command "Get-Content '%LOG%' -Tail 20"
exit /b 1
