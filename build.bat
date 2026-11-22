@echo off
cd /d "%~dp0"
python validate_build_inputs.py
if errorlevel 1 (
  pause
  exit /b 1
)
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Gerekli paketler kurulamadi.
  pause
  exit /b 1
)
python -m PyInstaller --noconfirm --clean VardiyaPuantaj.spec
if errorlevel 1 (
  echo EXE olusturulamadi.
  pause
  exit /b 1
)
echo EXE hazir: %~dp0dist\VardiyaPuantaj.exe
pause
