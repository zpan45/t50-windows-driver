$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host 'Installing PyInstaller + Pillow...'
python -m pip install -q pyinstaller pillow

Write-Host 'Building T50Label.exe ...'
python -m PyInstaller --noconfirm --clean --onefile --windowed --name T50Label `
    --hidden-import t50 `
    --hidden-import t50.app `
    --hidden-import t50.hidwin `
    --hidden-import t50.ipp `
    --hidden-import t50.printer `
    --hidden-import t50.serve `
    --hidden-import t50.winsetup `
    --hidden-import tkinter `
    --collect-submodules t50 `
    --collect-all PIL `
    t50\__main__.py

$exe = Join-Path (Get-Location) 'dist\T50Label.exe'
if (-not (Test-Path $exe)) { throw "build failed: $exe missing" }
Write-Host "OK: $exe"
Write-Host 'Copy that file to any Windows 10/11 PC and double-click it.'
