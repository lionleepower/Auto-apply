@echo off
rem Keep this file ASCII-only: cmd reads .bat files in the system code page before chcp runs.
chcp 65001 >nul
title autofill local server
cd /d "%~dp0"
.venv\Scripts\autofill.exe serve
pause
