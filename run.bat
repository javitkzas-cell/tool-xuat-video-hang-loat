@echo off
cd /d "%~dp0"
if exist "venv\Scripts\activate.bat" goto :run
echo Chua cai dat! Hay chay  2_CAI_DAT_MAY_MOI.bat  truoc - 1 lan duy nhat.
pause
exit /b 1
:run
call "venv\Scripts\activate.bat"
python "%~dp0app.py"
if errorlevel 1 pause
