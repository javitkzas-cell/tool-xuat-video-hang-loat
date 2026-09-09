@echo off
rem ============================================================
rem  Tao shortcut "Van Pham" ngoai Desktop (co icon rieng,
rem  mo tool KHONG hien cua so den). Chay 1 lan tren moi may,
rem  SAU KHI da cai xong (da co venv).
rem ============================================================
cd /d "%~dp0"

if not exist "venv\Scripts\pythonw.exe" goto :err_venv
if not exist "icon.ico" echo (Thieu icon.ico - shortcut van tao nhung dung icon mac dinh)

set "VP_DIR=%~dp0"
rem Ten shortcut KHONG DAU ("Van Pham") vi bo tao shortcut cua Windows
rem (WScript.Shell) dung bang ma ANSI — may he thong tieng Anh khong ma hoa
rem duoc chu co dau -> ten thanh "V?n Ph?m" (ky tu cam) -> luu that bai.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell;$d=[Environment]::GetFolderPath('Desktop');$p=$env:VP_DIR;$s=$ws.CreateShortcut($d+'\Van Pham.lnk');$s.TargetPath=$p+'venv\Scripts\pythonw.exe';$s.Arguments='\"'+$p+'app.py\"';$s.WorkingDirectory=$p;$s.IconLocation=$p+'icon.ico,0';$s.Description='Van Pham - Batch Render Engine';$s.Save()"
if errorlevel 1 goto :err_ps

rem Lam moi icon cache cua Windows de icon hien ngay (khong phai logout)
ie4uinit.exe -show >nul 2>&1
ie4uinit.exe -ClearIconCache >nul 2>&1

echo.
echo ================= XONG! =================
echo Da tao shortcut "Van Pham" ngoai man hinh Desktop.
echo Tu nay mo tool bang shortcut do (khong hien cua so den).
echo.
echo Luu y: neu tool mo bang shortcut ma khong len gi ca,
echo hay chay run.bat de xem loi hien ra trong cua so console.
echo.
pause
exit /b 0

:err_venv
echo.
echo  LOI: Chua co venv! Hay chay  2_CAI_DAT_MAY_MOI.bat  truoc.
pause
exit /b 1

:err_ps
echo.
echo  LOI: Khong tao duoc shortcut. Chup man hinh gui dev.
pause
exit /b 1
