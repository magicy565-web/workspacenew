@echo off
setlocal
cd /d "%~dp0"
set "STUDIO_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "STUDIO_FLAGS="
if exist "%~dp0.venv\Scripts\python.exe" (
  set "STUDIO_PY=%~dp0.venv\Scripts\python.exe"
  goto run
)
if exist "%STUDIO_PY%" goto run
where py >nul 2>nul
if errorlevel 1 goto try_python
set "STUDIO_PY=py"
set "STUDIO_FLAGS=-3"
goto run
:try_python
where python >nul 2>nul
if errorlevel 1 goto missing
set "STUDIO_PY=python"
:run
if /i "%~1"=="--check" goto check
"%STUDIO_PY%" %STUDIO_FLAGS% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 goto missing
if exist "%~dp0.venv\Scripts\python.exe" goto activate
"%STUDIO_PY%" %STUDIO_FLAGS% -m venv "%~dp0.venv"
if errorlevel 1 goto end
:activate
set "STUDIO_PY=%~dp0.venv\Scripts\python.exe"
set "STUDIO_FLAGS="
"%STUDIO_PY%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto end
if /i "%~1"=="--install-mcp" goto install_mcp
echo Starting Agent Workspace at http://127.0.0.1:8765/
echo Keep this window open while you use the workspace.
"%STUDIO_PY%" %STUDIO_FLAGS% -X utf8 -m studio.server
goto end
:check
"%STUDIO_PY%" %STUDIO_FLAGS% --version
goto end
:install_mcp
"%STUDIO_PY%" %STUDIO_FLAGS% -m pip install --disable-pip-version-check -r requirements-mcp.txt
goto end
:missing
echo Python 3.10 or later is required. Install Python and try again.
pause
exit /b 1
:end
exit /b %errorlevel%
