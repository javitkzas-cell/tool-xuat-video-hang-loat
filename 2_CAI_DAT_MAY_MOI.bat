@echo off
title CAI DAT TOOL CHOSEN - 1 CLICK
cd /d "%~dp0"
echo.
echo ============================================
echo    CAI DAT TOOL CHOSEN - 1 CLICK
echo ============================================
echo  Cac buoc: [1/4] Python  [2/4] venv  [3/4] thu vien  [4/4] rembg
echo.

rem ---------- [1/4] TIM PYTHON 3.10 - 3.12 ----------
set "PYCMD="
py -3.11 -V >nul 2>&1 && set "PYCMD=py -3.11"
if defined PYCMD goto :found
py -3.10 -V >nul 2>&1 && set "PYCMD=py -3.10"
if defined PYCMD goto :found
py -3.12 -V >nul 2>&1 && set "PYCMD=py -3.12"
if defined PYCMD goto :found
python -c "import sys;sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,12) else 1)" >nul 2>&1 && set "PYCMD=python"
if defined PYCMD goto :found

echo [1/4] Chua co Python 3.10-3.12. Dang TAI Python 3.11.9 - khoang 25MB...
curl -L -o "%TEMP%\python-3.11.9-amd64.exe" https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
if errorlevel 1 goto :err_download
echo       Dang CAI Python - im lang, cho 1-2 phut...
"%TEMP%\python-3.11.9-amd64.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_tcltk=1 Include_launcher=1 Include_pip=1
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" goto :inst_ok
echo.
echo  LOI: Cai xong nhung khong tim thay Python.
echo  Hay DONG cua so nay va chay lai file nay mot lan nua.
pause
exit /b 1

:inst_ok
set "PYCMD="%LocalAppData%\Programs\Python\Python311\python.exe""
echo       Cai Python xong.
goto :venv

:found
echo [1/4] Da co Python phu hop: %PYCMD%

:venv
rem ---------- [2/4] TAO VENV ----------
echo [2/4] Tao moi truong rieng - venv...
if exist "venv\Scripts\python.exe" goto :haveenv
%PYCMD% -m venv venv
if errorlevel 1 goto :err_venv
:haveenv
call "venv\Scripts\activate.bat"

rem ---------- [3/4] CAI THU VIEN CHINH ----------
echo [3/4] Cai thu vien - LAN DAU tai khoang 1-2GB, cho 5-20 phut tuy mang...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 goto :err_pip

rem ---------- [4/4] TUY CHON: REMBG ----------
echo [4/4] Cai them rembg - tach nen AI - tuy chon...
python -m pip install rembg onnxruntime
if errorlevel 1 echo       Bo qua rembg - tool VAN CHAY binh thuong, chi thieu tach nen AI.

echo.
echo ============================================
echo    CAI DAT HOAN TAT!
echo    Tu nay chay tool bang file:  run.bat
echo ============================================
echo  - ffmpeg: co san trong thu muc ffmpeg - khong can cai
echo  - Model Whisper: co san trong models - chay offline
echo.
pause
exit /b 0

:err_download
echo.
echo  LOI: Khong tai duoc Python. Kiem tra Internet roi chay lai file nay.
echo  Hoac tu cai Python 3.11 tai python.org - nho TICK "Add python.exe to PATH".
pause
exit /b 1

:err_venv
echo  LOI: Khong tao duoc venv. Chup man hinh gui dev.
pause
exit /b 1

:err_pip
echo.
echo  LOI khi cai thu vien chinh. Thu chay lai file nay 1 lan nua - pip se cai tiep phan thieu.
echo  Van loi thi chup man hinh gui dev.
pause
exit /b 1
