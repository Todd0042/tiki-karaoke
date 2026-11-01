param(
    [string]$Edition = ""
)
# Tiki Karaoke - Windows Standalone Build Script (PowerShell)
$ErrorActionPreference = "Continue"

$RawScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($RawScriptDir -match '^[a-zA-Z]:' -or $RawScriptDir -match '^\\\\') {
    $ScriptDir = (Get-Item $RawScriptDir).FullName
} else {
    $ScriptDir = (Get-Item (Join-Path (Get-Location).Path $RawScriptDir)).FullName
}
if (Test-Path "$ScriptDir\..\..\main.py") {
    $RepoDir = (Get-Item "$ScriptDir\..\..").FullName
} elseif (Test-Path "$ScriptDir\..\tiki-karaoke\main.py") {
    $RepoDir = (Get-Item "$ScriptDir\..\tiki-karaoke").FullName
} else {
    $RepoDir = (Get-Item "$ScriptDir\..\..").FullName
}
Set-Location $ScriptDir

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "TIKI KARAOKE - WINDOWS STANDALONE PACKAGING BUILDER" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan

# 0. Stop any running instances and clean prior build outputs
Write-Host "Stopping any running TikiKaraoke and helper instances..." -ForegroundColor Yellow
Stop-Process -Name "TikiKaraoke" -Force -ErrorAction SilentlyContinue
Stop-Process -Name "TikiKaraokeImporter" -Force -ErrorAction SilentlyContinue
Stop-Process -Name "cloudflared" -Force -ErrorAction SilentlyContinue
Stop-Process -Name "bore" -Force -ErrorAction SilentlyContinue
Stop-Process -Name "tiki-mic-router" -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500

Write-Host "Cleaning prior dist and build directories..." -ForegroundColor Yellow
if (Test-Path "$ScriptDir\dist") { Remove-Item -Path "$ScriptDir\dist" -Recurse -Force -ErrorAction SilentlyContinue }
if (Test-Path "$ScriptDir\build") { Remove-Item -Path "$ScriptDir\build" -Recurse -Force -ErrorAction SilentlyContinue }
Get-ChildItem -Path "$RepoDir" -Filter "__pycache__" -Recurse | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# 1. Locate Python
$PythonBin = "python"
$HasSystemPython = $false
try {
    $ver = & python --version 2>$null
    if ($LASTEXITCODE -eq 0 -and $ver) { $HasSystemPython = $true }
} catch {
    $HasSystemPython = $false
}

if (-not $HasSystemPython) {
    $PortablePy = "$ScriptDir\portable_python\python.exe"
    if (Test-Path $PortablePy) {
        $PythonBin = $PortablePy
        Write-Host "Using bundled portable Windows Python: $PythonBin" -ForegroundColor Green
    } else {
        Write-Host "[WARN] Python not found. Downloading portable Windows Python..." -ForegroundColor Yellow
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        New-Item -ItemType Directory -Force -Path "$ScriptDir\portable_python" | Out-Null
        $zipPath = "$ScriptDir\py.tar.gz"
        Invoke-WebRequest -Uri "https://github.com/astral-sh/python-build-standalone/releases/download/20261013/cpython-3.12.15%2B20261013-x86_64-pc-windows-msvc-install_only.tar.gz" -OutFile $zipPath
        tar -xzf $zipPath -C "$ScriptDir\portable_python" --strip-components=1
        Remove-Item $zipPath -Force
        $PythonBin = $PortablePy
    }
}

Write-Host "`n[1/5] Verifying Python version..." -ForegroundColor Yellow
& $PythonBin --version

Write-Host "`n[2/5] Installing/Upgrading PyInstaller and build dependencies..." -ForegroundColor Yellow
& $PythonBin -m ensurepip --default-pip
& $PythonBin -m pip install --upgrade pip
& $PythonBin -m pip install -r "$RepoDir\requirements.txt"
& $PythonBin -m pip install pyinstaller
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n[ERROR] Pip dependency installation failed." -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "`n[3/5] Staging Windows 64-bit binaries (ffmpeg, ffprobe, cloudflared, bore)..." -ForegroundColor Yellow
& $PythonBin "$ScriptDir\fetch_windows_binaries.py" "$ScriptDir\bin"

# Stage the native mic loopback helper.
# If not built yet, attempt to compile it via build.bat.
if (-not (Test-Path "$ScriptDir\bin\tiki-mic-router.exe")) {
    if (-not (Test-Path "$ScriptDir\mic_router\tiki-mic-router.exe")) {
        Write-Host "[Info] Attempting to build mic_router\tiki-mic-router.exe..." -ForegroundColor Cyan
        Start-Process -FilePath "cmd.exe" -ArgumentList "/c `"$ScriptDir\mic_router\build.bat`"" -Wait -NoNewWindow
    }
    if (Test-Path "$ScriptDir\mic_router\tiki-mic-router.exe") {
        if (-not (Test-Path "$ScriptDir\bin")) { New-Item -ItemType Directory -Force -Path "$ScriptDir\bin" | Out-Null }
        Copy-Item "$ScriptDir\mic_router\tiki-mic-router.exe" "$ScriptDir\bin\" -Force
        Write-Host "[OK] tiki-mic-router.exe staged into bin\" -ForegroundColor Green
    } else {
        Write-Host "[WARN] mic_router\tiki-mic-router.exe not found - Windows mic loopback will be unavailable." -ForegroundColor Yellow
        Write-Host "       Build it once with: mic_router\build.bat (needs MSVC or MinGW-w64 gcc)" -ForegroundColor Yellow
    }
} else {
    Write-Host "[OK] tiki-mic-router.exe found in bin\" -ForegroundColor Green
}

Write-Host "`n[4/5] Building TikiKaraoke standalone bundle via PyInstaller..." -ForegroundColor Yellow

$LocalBuild = "C:\TikiBuild_Run"
$LocalDist = "$LocalBuild\dist"
$LocalWork = "$LocalBuild\work"

if (Test-Path $LocalBuild) { Remove-Item -Path $LocalBuild -Recurse -Force -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force -Path $LocalDist | Out-Null
New-Item -ItemType Directory -Force -Path $LocalWork | Out-Null

& $PythonBin -m PyInstaller -y "$ScriptDir\tiki_karaoke.spec" --distpath "$LocalDist" --workpath "$LocalWork"

if (-not (Test-Path "$LocalDist\TikiKaraoke\TikiKaraoke.exe")) {
    Write-Host "`n[ERROR] [ERROR] PyInstaller build failed! TikiKaraoke.exe not found." -ForegroundColor Red
    exit 1
}

# Ensure tiki-mic-router.exe is physically inside the bundle's bin directory
if (Test-Path "$ScriptDir\bin\tiki-mic-router.exe") {
    $targetBins = @(
        "$LocalDist\TikiKaraoke\bin",
        "$LocalDist\TikiKaraoke\_internal\bin"
    )
    foreach ($tb in $targetBins) {
        if (-not (Test-Path $tb)) { New-Item -ItemType Directory -Force -Path $tb | Out-Null }
        Copy-Item "$ScriptDir\bin\tiki-mic-router.exe" "$tb\" -Force -ErrorAction SilentlyContinue
    }
}

# Stage Tiki Karaoke Smart Importer if available in sibling repo
$importerCandidates = @(
    "$ScriptDir\..\tiki-karaoke-importer\dist\windows\TikiKaraokeImporter.exe",
    "$ScriptDir\..\tiki-karaoke-importer\dist\TikiKaraokeImporter.exe"
)
foreach ($cand in $importerCandidates) {
    if (Test-Path $cand) {
        Copy-Item $cand "$LocalDist\TikiKaraoke\TikiKaraokeImporter.exe" -Force -ErrorAction SilentlyContinue
        Write-Host "[OK] Staged TikiKaraokeImporter.exe into bundle." -ForegroundColor Green
        break
    }
}

Write-Host "Copying fresh build bundle to repository dist directory..." -ForegroundColor Green
try {
    New-Item -ItemType Directory -Force -Path "$ScriptDir\dist" -ErrorAction SilentlyContinue | Out-Null
    robocopy "$LocalDist\TikiKaraoke" "$ScriptDir\dist\TikiKaraoke" /E /NP /R:2 /W:1 /NJH /NJS | Out-Null
} catch {
    Write-Host "Notice: Network share copy warning: $($_.Exception.Message)" -ForegroundColor Yellow
}

$DistExe = "$ScriptDir\dist\TikiKaraoke\TikiKaraoke.exe"
if (-not (Test-Path $DistExe)) {
    $DistExe = "$LocalDist\TikiKaraoke\TikiKaraoke.exe"
}

# Also ensure dist has tiki-mic-router.exe and TikiKaraokeImporter.exe
if (Test-Path "$ScriptDir\bin\tiki-mic-router.exe") {
    $distBins = @(
        "$ScriptDir\dist\TikiKaraoke\bin",
        "$ScriptDir\dist\TikiKaraoke\_internal\bin"
    )
    foreach ($db in $distBins) {
        if (-not (Test-Path $db)) { New-Item -ItemType Directory -Force -Path $db | Out-Null }
        Copy-Item "$ScriptDir\bin\tiki-mic-router.exe" "$db\" -Force -ErrorAction SilentlyContinue
    }
    Write-Host "[OK] Verified tiki-mic-router.exe in dist bundle." -ForegroundColor Green
}

foreach ($cand in $importerCandidates) {
    if (Test-Path $cand) {
        Copy-Item $cand "$ScriptDir\dist\TikiKaraoke\TikiKaraokeImporter.exe" -Force -ErrorAction SilentlyContinue
        Write-Host "[OK] Verified TikiKaraokeImporter.exe in dist bundle." -ForegroundColor Green
        break
    }
}

Write-Host "`n[5/5] Packaging release archives..." -ForegroundColor Yellow

# Determine Edition (Pro or Free)
if (-not $Edition) {
    try {
        $branch = & git -C "$RepoDir" rev-parse --abbrev-ref HEAD 2>$null
        if ($branch -eq "ugh-ads") {
            $Edition = "Free"
        } else {
            $Edition = "Pro"
        }
    } catch {
        $Edition = "Pro"
    }
}
Write-Host "Target Edition: $Edition" -ForegroundColor Cyan

# Parse version from installer.iss
$AppVersion = "1.0.5"
if (Test-Path "$ScriptDir\installer.iss") {
    $match = Select-String -Path "$ScriptDir\installer.iss" -Pattern '#define MyAppVersion "([^"]+)"'
    if ($match -and $match.Matches.Groups[1].Value) {
        $AppVersion = $match.Matches.Groups[1].Value
    }
}

# Always generate the turnkey Portable release ZIP
$ZipSrc = if (Test-Path "$LocalDist\TikiKaraoke") { "$LocalDist\TikiKaraoke\*" } else { "$ScriptDir\dist\TikiKaraoke\*" }
$ZipDst = "$ScriptDir\dist\TikiKaraoke-$Edition-Windows-v$AppVersion-Portable.zip"
Compress-Archive -Path $ZipSrc -DestinationPath $ZipDst -Force
Write-Host "[OK] Portable ZIP created at: $ZipDst" -ForegroundColor Green

# Check for Inno Setup compiler to also generate Setup Installer EXE
$Iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $Iscc)) {
    $Iscc = "C:\Program Files\Inno Setup 6\ISCC.exe"
}
if (-not (Test-Path $Iscc)) {
    $Iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
}
if (-not (Test-Path $Iscc)) {
    $cmdIscc = Get-Command iscc -ErrorAction SilentlyContinue
    if ($cmdIscc) { $Iscc = $cmdIscc.Source }
}

if (Test-Path $Iscc) {
    Write-Host "`nCompiling Windows setup installer via Inno Setup ($Iscc)..." -ForegroundColor Green
    & $Iscc "/DMyAppEdition=$Edition" "$ScriptDir\installer.iss"
    Write-Host "`n[OK] Setup installer generated at:" -ForegroundColor Green
    Get-ChildItem "$ScriptDir\dist_installer\*.exe" | ForEach-Object { Write-Host "   - $($_.FullName)" }
} else {
    Write-Host "[INFO] Inno Setup not detected on system. Portable ZIP is ready for distribution." -ForegroundColor Yellow
}

Write-Host "`n=================================================================" -ForegroundColor Green
Write-Host "*** BUILD COMPLETED SUCCESSFULLY!" -ForegroundColor Green
$DistDir = Split-Path -Parent $DistExe
Write-Host "Standalone directory: $DistDir" -ForegroundColor Green
Write-Host "Executable:           $DistExe" -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Green
