@echo off
title CAI PYTORCH GPU (CUDA) - VAN PHAM
cd /d "%~dp0"
echo.
echo ============================================
echo    CAI PYTORCH BAN GPU (CUDA 12.1)
echo ============================================
echo  Dung khi phan tich kich ban BI CHAM (Whisper chay CPU).
echo  YEU CAU: may co GPU NVIDIA + driver moi.
echo  Se tai ~2.5GB, thay torch CPU bang torch GPU.
echo.
pause

if not exist "venv\Scripts\python.exe" goto :err_venv
call "venv\Scripts\activate.bat"

echo [1/2] Go torch ban cu (CPU)...
python -m pip uninstall -y torch torchvision torchaudio

echo [2/2] Cai torch GPU (CUDA 12.1)...
python -m pip install torch --index-url https://download.pytorch.org/whl/cu121
if errorlevel 1 goto :err_pip

echo.
echo Kiem tra:
python -c "import torch; print('CUDA kha dung:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'khong co')"
echo.
echo ============================================
echo    XONG! Mo lai Van Pham. Neu dong tren ghi
echo    'CUDA kha dung: True' la phan tich se nhanh lai.
echo ============================================
pause
exit /b 0

:err_venv
echo  LOI: Chua co venv! Hay chay  2_CAI_DAT_MAY_MOI.bat  truoc.
pause
exit /b 1

:err_pip
echo.
echo  LOI khi cai torch GPU. Kiem tra Internet / driver NVIDIA roi chay lai.
echo  Neu may KHONG co GPU NVIDIA thi khong dung duoc ban GPU.
pause
exit /b 1
