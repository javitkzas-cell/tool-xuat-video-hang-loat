@echo off
title KIEM TRA MOI TRUONG GPU - VAN PHAM
cd /d "%~dp0"
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" (
  echo LOI: khong thay venv\Scripts\python.exe
  pause & exit /b 1
)
echo ============================================
echo  KIEM TRA MOI TRUONG (python cua VENV)
echo ============================================
"%PY%" -c "import sys;print('Python :',sys.executable)"
"%PY%" -c "import numpy;print('numpy  :',numpy.__version__)"
"%PY%" -c "import torch;print('torch  :',torch.__version__);print('CUDA   :',torch.cuda.is_available());print('GPU    :',torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'khong co')"
"%PY%" -c "import torchaudio;print('torchaudio:',torchaudio.__version__)" 2>nul || echo torchaudio: THIEU (can cai lai)
"%PY%" -c "import stable_whisper;print('stable-ts:',getattr(stable_whisper,'__version__','?'))" 2>nul || echo stable-ts: THIEU
echo.
echo Chup toan bo cua so nay gui dev de chuan doan.
pause
