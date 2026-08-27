@echo off
setlocal

cd /d "%~dp0"

for /f "delims=" %%V in ('python -c "import sys; sys.path.insert(0, r'src'); from commons.version import __version__; print(__version__)"') do set "VERSION=%%V"

if not defined VERSION (
    echo Failed to read the application version.
    exit /b 1
)

echo Building StickerGenie %VERSION%...
python -m PyInstaller --clean --noconfirm StickerGenie.spec
if errorlevel 1 exit /b 1

powershell.exe -NoProfile -Command ^
    "$source = Join-Path (Get-Location) 'dist\StickerGenie';" ^
    "$destination = Join-Path (Get-Location) ('dist\StickerGenie-' + $env:VERSION + '.zip');" ^
    "Compress-Archive -Path $source -DestinationPath $destination -Force"
if errorlevel 1 exit /b 1

echo Created dist\StickerGenie-%VERSION%.zip
