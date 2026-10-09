@echo off
rem Keep this file ASCII-only: cmd reads .bat files in the system code page before chcp runs.
chcp 65001 >nul
cd /d "%~dp0"
start "" .venv\Scripts\pythonw.exe -m autofill gui
