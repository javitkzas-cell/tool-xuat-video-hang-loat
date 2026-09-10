@echo off
title CAI TACH NEN AI (rembg) - VAN PHAM
cd /d "%~dp0"
echo.
echo ============================================
echo    CAI TACH NEN AI (rembg + onnxruntime)
echo ============================================
echo  Chi can cai 1 lan. Can Internet (tai ~100-200MB).
echo.

if not exist "venv\Scripts\python.exe" goto :err_venv
call "venv\Scripts\activate.bat"

echo [1/2] Nang cap pip...
python -m pip install --upgrade pip

echo [2/2] Cai rembg + onnxruntime (co the mat 3-10 phut)...
python -m pip install rembg onnxruntime
if errorlevel 1 goto :err_pip

echo.
echo ============================================
echo    CAI XONG! Mo lai Van Pham la tach nen AI chay.
echo    (Lan tach dau tien se tai model ~170MB - cho 1 chut)
echo ============================================
pause
exit /b 0

:err_venv
echo.
echo  LOI: Chua co venv! Hay chay  2_CAI_DAT_MAY_MOI.bat  truoc.
pause
exit /b 1

:err_pip
echo.
echo  LOI khi cai. Kiem tra Internet roi chay lai file nay.
echo  Van loi thi chup man hinh gui dev.
pause
exit /b 1
