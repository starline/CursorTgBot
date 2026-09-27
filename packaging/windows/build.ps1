# Build the Windows app on a Windows machine:
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
# Result: dist\CursorTgBot\CursorTgBot.exe and dist\CursorTgBot-windows.zip
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root

$Python = "python"
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    $Python = "py"
}

& $Python -m pip install -r requirements-desktop.txt pyinstaller
& $Python -m PyInstaller (Join-Path $Root "packaging\CursorTgBot.spec") --noconfirm --clean

$OutDir = Join-Path $Root "dist\CursorTgBot"
$Zip = Join-Path $Root "dist\CursorTgBot-windows.zip"
if (Test-Path $Zip) {
    Remove-Item $Zip -Force
}
Compress-Archive -Path $OutDir -DestinationPath $Zip
Write-Host "Built $OutDir\CursorTgBot.exe"
Write-Host "Archive $Zip"
