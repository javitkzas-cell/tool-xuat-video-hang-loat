@echo off
cd /d "%~dp0"
call "%~dp0venv\Scripts\activate.bat"
python "%~dp0subtitle_tool.py"
if errorlevel 1 pause
