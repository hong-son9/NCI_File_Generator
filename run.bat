@echo off
rem Run NICE Test Data Generator (Tkinter desktop app)
cd /d "%~dp0"
python datagen_ui.py
if errorlevel 1 pause
