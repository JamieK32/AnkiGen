Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$python = "python"

Write-Host "Generating icon assets..."
& $python scripts\generate_app_icon.py

Write-Host "Building AnkiGen.exe with PyInstaller..."
& $python -m PyInstaller --noconfirm --clean AnkiGen.spec

$releaseDir = Join-Path $projectRoot "release"
$packageRoot = Join-Path $releaseDir "AnkiGen-windows-x64"
$zipPath = Join-Path $releaseDir "AnkiGen-windows-x64.zip"

if (Test-Path $packageRoot) {
    Remove-Item $packageRoot -Recurse -Force
}
if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}

New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
Copy-Item -Recurse -Path (Join-Path $projectRoot "dist\\AnkiGen") -Destination $packageRoot
New-Item -ItemType Directory -Force -Path (Join-Path $packageRoot "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $packageRoot "audio") | Out-Null

Compress-Archive -Path $packageRoot -DestinationPath $zipPath

Write-Host "Release package created:"
Write-Host "  $zipPath"
