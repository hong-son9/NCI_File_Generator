@echo off
rem Build web/dist and deploy to Cloudflare Pages.
rem Needs Node.js (npx). First run opens the browser to log in to Cloudflare.
cd /d "%~dp0"
python web\build.py || goto :fail
call npx wrangler pages deploy web\dist --project-name nci-file-generator --branch main || goto :fail
pause
goto :eof
:fail
echo Deploy failed.
pause
