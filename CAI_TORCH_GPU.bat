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

rem QUAN TRONG: dung THANG python trong venv (KHONG dua vao 'activate' vi
rem tren 1 so may activate loi -> pip cai nham vao Python he thong, venv van CPU).
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" goto :err_venv

echo Python dang dung:
"%PY%" -c "import sys; print('  ', sys.executable)"
echo.
pause

echo [1/2] Go torch ban cu trong VENV...
"%PY%" -m pip uninstall -y torch torchvision torchaudio

echo [2/2] Cai torch + torchvision + torchaudio ban GPU (CUDA 12.1) vao VENV...
"%PY%" -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
if errorlevel 1 goto :err_pip

echo.
echo Kiem tra (chay bang python cua VENV):
"%PY%" -c "import torch, torchaudio; print('torch:', torch.__version__); print('CUDA kha dung:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'khong co')"
echo.
echo ============================================
echo    XONG! Mo lai Van Pham.
echo    Neu tren ghi torch co '+cu121' va 'CUDA kha dung: True'
echo    la phan tich se nhanh lai.
echo ============================================
pause
exit /b 0

:err_venv
echo  LOI: Khong tim thay venv\Scripts\python.exe !
echo  Hay chay  2_CAI_DAT_MAY_MOI.bat  truoc de tao venv.
pause
exit /b 1

:err_pip
echo.
echo  LOI khi cai torch GPU. Kiem tra Internet / driver NVIDIA roi chay lai.
echo  Neu may KHONG co GPU NVIDIA thi khong dung duoc ban GPU.
pause
exit /b 1
