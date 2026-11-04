@echo off
setlocal
chcp 65001 >nul
set PYTHONDONTWRITEBYTECODE=1
"%~dp0runtime\python.exe" -X utf8 "%~dp0installer.py" install %*
set patch_result=%errorlevel%
echo.
pause
exit /b %patch_result%
