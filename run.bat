
@echo off
cd /d "%~dp0"
echo Starting ROM Tools...
echo.
call venv\Scripts\activate.bat
python -u romtools.py 2>&1
echo.
echo Exit code: %ERRORLEVEL%
echo.
echo Server stopped.
pause
 