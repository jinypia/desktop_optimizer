<#
.SYNOPSIS
    Builds the Desktop Optimizer Windows installer and portable ZIP.

.DESCRIPTION
    Runs PyInstaller, then Inno Setup, then zips the bundle. Artifacts land
    in dist\installer\.

    Build work happens on a LOCAL scratch directory by default: this project
    often lives on a network share, where writing ~150 MB of Qt binaries
    would take minutes. Only the finished installer is copied back.

.PARAMETER Python
    Interpreter to build with. Must have pyinstaller + pillow installed and
    must NOT be Microsoft Store Python (its sandbox breaks builds).

.PARAMETER SkipIcon
    Reuse the existing assets\app.ico instead of regenerating it.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#>
[CmdletBinding()]
param(
    [string]$Python = "$env:LOCALAPPDATA\DesktopOptimizer\venv\Scripts\python.exe",
    [string]$Iscc = "$env:LOCALAPPDATA\InnoSetup6\ISCC.exe",
    [string]$WorkRoot = "$env:LOCALAPPDATA\DesktopOptimizer\build",
    [switch]$SkipIcon
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
Write-Host "== Desktop Optimizer build ==" -ForegroundColor Cyan
Write-Host "project : $root"
Write-Host "python  : $Python"

if (-not (Test-Path $Python)) {
    throw "Python not found: $Python  (see README > Building the installer)"
}

# --- 1. icon ---------------------------------------------------------------
if (-not $SkipIcon) {
    Write-Host "`n[1/4] generating icon..." -ForegroundColor Cyan
    & $Python packaging\make_icon.py
    if ($LASTEXITCODE -ne 0) { throw "icon generation failed" }
} else {
    Write-Host "`n[1/4] icon: skipped (reusing assets\app.ico)" -ForegroundColor Cyan
}

# --- 2. PyInstaller --------------------------------------------------------
Write-Host "`n[2/4] building executable (PyInstaller)..." -ForegroundColor Cyan
$distDir = Join-Path $root "dist"
$work = Join-Path $WorkRoot "work"
$stage = Join-Path $WorkRoot "dist"
New-Item -ItemType Directory -Force $work, $stage, $distDir | Out-Null

& $Python -m PyInstaller packaging\desktop_optimizer.spec --noconfirm `
    --distpath $stage --workpath $work
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$bundle = Join-Path $stage "DesktopOptimizer"
$exe = Join-Path $bundle "DesktopOptimizer.exe"
if (-not (Test-Path $exe)) { throw "expected executable missing: $exe" }
$size = [math]::Round(((Get-ChildItem $bundle -Recurse -File |
        Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host "      bundle: $size MB" -ForegroundColor Green

# Inno Setup reads from dist\DesktopOptimizer relative to the project
$localBundle = Join-Path $distDir "DesktopOptimizer"
if ($bundle -ne $localBundle) {
    Write-Host "      copying bundle into project dist\ ..."
    if (Test-Path $localBundle) {
        Remove-Item $localBundle -Recurse -Force -Confirm:$false
    }
    Copy-Item $bundle $localBundle -Recurse -Force
}

# --- 3. installer ----------------------------------------------------------
Write-Host "`n[3/4] building installer (Inno Setup)..." -ForegroundColor Cyan
if (-not (Test-Path $Iscc)) {
    throw "ISCC.exe not found: $Iscc  (see README > Building the installer)"
}
# Drop installers from earlier versions so the output folder never offers
# two versions side by side.
Get-ChildItem (Join-Path $distDir "installer") -Filter "*-setup.exe" `
    -ErrorAction SilentlyContinue |
    Remove-Item -Force -Confirm:$false -ErrorAction SilentlyContinue
# app/version.py is the single source of truth. Pass it in rather than
# letting installer.iss keep its own copy, which silently drifts and then
# ships an installer whose version disagrees with the running app -- which
# would also make the in-app update check lie.
$verLine = Select-String -Path "app\version.py" -Pattern '__version__\s*=\s*"([^"]+)"'
if (-not $verLine) { throw "could not read __version__ from app\version.py" }
$appVersion = $verLine.Matches[0].Groups[1].Value
Write-Host "      version: $appVersion (from app\version.py)"

# PyInstaller reads version_info.txt directly, so it cannot be injected --
# verify it instead of shipping mismatched file metadata.
$viExpect = ($appVersion -split '\.')[0..2] -join ', '
$vi = Get-Content "packaging\version_info.txt" -Raw
if ($vi -notmatch [regex]::Escape("filevers=($viExpect, 0)")) {
    throw ("packaging\version_info.txt does not match version $appVersion " +
           "-- expected 'filevers=($viExpect, 0)'. Update it and rebuild.")
}
if ($vi -notmatch [regex]::Escape("`"$appVersion.0`"")) {
    throw ("packaging\version_info.txt File/ProductVersion does not match " +
           "$appVersion.0. Update it and rebuild.")
}

& $Iscc /Q "/DAppVersion=$appVersion" "packaging\installer.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

# --- 4. portable zip -------------------------------------------------------
Write-Host "`n[4/4] packing portable ZIP..." -ForegroundColor Cyan
$outDir = Join-Path $distDir "installer"
New-Item -ItemType Directory -Force $outDir | Out-Null
$zip = Join-Path $outDir "DesktopOptimizer-portable.zip"
if (Test-Path $zip) { Remove-Item $zip -Force -Confirm:$false }
Compress-Archive -Path $localBundle -DestinationPath $zip -CompressionLevel Optimal

Write-Host "`n== done ==" -ForegroundColor Green
Get-ChildItem $outDir | ForEach-Object {
    Write-Host ("  {0}  ({1} MB)" -f $_.Name, [math]::Round($_.Length / 1MB, 1))
}
