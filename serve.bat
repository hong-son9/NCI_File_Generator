@echo off
rem Build web/dist and open the web version locally at http://localhost:8080
cd /d "%~dp0"
python web\build.py || goto :fail
start "" http://localhost:8080
python -m http.server 8080 -d web\dist
goto :eof
:fail
pause
