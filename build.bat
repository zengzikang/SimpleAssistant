@echo off
echo ========================================
echo  SimpleAssistant Build Script
echo ========================================
echo.

python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found, installing...
    pip install pyinstaller
)

echo [1/2] Cleaning old build...
if exist build  rmdir /s /q build
if exist dist   rmdir /s /q dist

echo [2/2] Building...
python -m PyInstaller SimpleAssistant.spec

if errorlevel 1 (
    echo.
    echo BUILD FAILED. Check the log above.
    pause
    exit /b 1
)

echo.
echo ========================================
echo  Build complete!
echo  Output: dist\SimpleAssistant\SimpleAssistant.exe
echo ========================================
pause
