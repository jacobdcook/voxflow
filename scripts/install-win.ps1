# mintflow Windows installer (winget, with Chocolatey fallback).
# Run from PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\scripts\install-win.ps1

$ErrorActionPreference = "Stop"
$OllamaModel = if ($env:OLLAMA_MODEL) { $env:OLLAMA_MODEL } else { "qwen2.5:7b" }
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Root) { $Root = (Get-Location).Path }
$Venv = Join-Path $env:LOCALAPPDATA "mintflow\venv"
$ShimDir = Join-Path $env:LOCALAPPDATA "mintflow\bin"
$StartupDir = [Environment]::GetFolderPath("Startup")

function Write-Info([string]$Message) { Write-Host "`n==> $Message" }
function Write-Ok([string]$Message) { Write-Host "    OK  $Message" }
function Write-Warn([string]$Message) { Write-Host "    !!  $Message" }
function Fail([string]$Message) { Write-Host "    ERROR  $Message"; exit 1 }

function Test-Cmd([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-PythonVersion([string]$Exe) {
    try {
        $out = & $Exe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>$null
        return "$out".Trim()
    } catch {
        return ""
    }
}

function Test-Python310([string]$Exe) {
    if (-not (Test-Cmd $Exe)) { return $false }
    try {
        & $Exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Add-UserPath([string]$Dir) {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $userPath) { $userPath = "" }
    $parts = $userPath -split ";" | Where-Object { $_ -and $_.Trim() -ne "" }
    if ($parts -contains $Dir) { return }
    $newPath = if ($userPath) { "$Dir;$userPath" } else { $Dir }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    $env:Path = "$Dir;$env:Path"
    Write-Ok "added $Dir to your user PATH"
}

function Refresh-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$user;$machine"
    $guesses = @(
        "$env:LOCALAPPDATA\Programs\Python\Python313",
        "$env:LOCALAPPDATA\Programs\Python\Python313\Scripts",
        "$env:LOCALAPPDATA\Programs\Python\Python312",
        "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts",
        "$env:LOCALAPPDATA\Programs\Python\Python311",
        "$env:LOCALAPPDATA\Programs\Python\Python311\Scripts",
        "$env:LOCALAPPDATA\Programs\Ollama",
        "C:\Program Files\Ollama",
        "$env:LOCALAPPDATA\mintflow\bin"
    )
    foreach ($g in $guesses) {
        if (Test-Path $g) { $env:Path = "$g;$env:Path" }
    }
}

Write-Info "Checking Python 3.10+"
$Py = $null
foreach ($candidate in @("python", "py")) {
    if (Test-Python310 $candidate) {
        $Py = $candidate
        break
    }
}

if (-not $Py) {
    Write-Warn "Python 3.10+ was not found. Installing Python 3.12."
    if (Test-Cmd "winget") {
        cmd /c "winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements"
    } elseif (Test-Cmd "choco") {
        cmd /c "choco install python -y"
    } else {
        Fail "Install Python 3.10+ from https://www.python.org/downloads/  Check 'Add python.exe to PATH', then re-run this script."
    }
    Refresh-SessionPath
    Start-Sleep -Seconds 2
    foreach ($candidate in @("python", "py")) {
        if (Test-Python310 $candidate) {
            $Py = $candidate
            break
        }
    }
}

if (-not $Py) {
    Fail "Python 3.10+ is still missing. Install it from https://www.python.org/downloads/ and tick 'Add python.exe to PATH'."
}
Write-Ok "$Py $(Get-PythonVersion $Py)"

Write-Info "Installing mintflow"
New-Item -ItemType Directory -Force -Path $Venv, $ShimDir | Out-Null
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
    & $Py -m venv $Venv
}
$VenvPy = Join-Path $Venv "Scripts\python.exe"
& $VenvPy -m pip install --upgrade pip wheel
$PyProject = Join-Path $Root "pyproject.toml"
if (Test-Path $PyProject) {
    & $VenvPy -m pip install ($Root + '[desktop]')
    Write-Ok "installed from this repo"
} else {
    & $VenvPy -m pip install ('mintflow' + '[desktop]')
    Write-Ok "installed from PyPI"
}

$MintflowExe = Join-Path $Venv "Scripts\mintflow.exe"
$Shim = Join-Path $ShimDir "mintflow.cmd"
@(
    "@echo off",
    "`"$MintflowExe`" %*"
) | Set-Content -Path $Shim -Encoding ASCII
Add-UserPath $ShimDir
Write-Ok "mintflow -> $Shim"

Write-Info "Checking Ollama"
Refresh-SessionPath
if (-not (Test-Cmd "ollama")) {
    Write-Warn "Ollama was not found. Installing it."
    if (Test-Cmd "winget") {
        cmd /c "winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements"
    } elseif (Test-Cmd "choco") {
        cmd /c "choco install ollama -y"
    } else {
        Write-Warn "Install Ollama from https://ollama.com/download then run: ollama pull $OllamaModel"
    }
    Refresh-SessionPath
}

if (Test-Cmd "ollama") {
    Write-Ok "ollama found"
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2 -UseBasicParsing | Out-Null
    } catch {
        Write-Warn "Starting Ollama. A window may open."
        Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
    }
    Write-Info "Pulling cleanup model $OllamaModel (this can take a while the first time)"
    & ollama pull $OllamaModel
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "ollama pull failed. Later run: ollama pull $OllamaModel"
    }
} else {
    Write-Warn "Ollama is not on PATH yet. Cleanup will use simple local rules until you install it from https://ollama.com/download"
}

Write-Info "First-run setup (GPU detect + hotkey)"
& $MintflowExe setup
if ($LASTEXITCODE -ne 0) {
    Write-Warn "setup did not finish. Later run: mintflow setup"
}

Write-Info "Setting up Start Menu autostart"
$StartupBat = Join-Path $StartupDir "mintflow.bat"
$Pythonw = Join-Path $Venv "Scripts\pythonw.exe"
if (Test-Path $Pythonw) {
    @"
@echo off
start "" "$Pythonw" -m mintflow
"@ | Set-Content -Path $StartupBat -Encoding ASCII
} else {
    @"
@echo off
start "" "$MintflowExe"
"@ | Set-Content -Path $StartupBat -Encoding ASCII
}
Write-Ok "startup shortcut -> $StartupBat"

try {
    Start-Process -FilePath $MintflowExe -WindowStyle Hidden
} catch {
    Write-Warn "Could not start mintflow now. Run mintflow from a new PowerShell window."
}

$Key = "Pause"
$Cfg = Join-Path $env:APPDATA "mintflow\config.json"
if (Test-Path $Cfg) {
    try {
        $json = Get-Content -Raw -Path $Cfg | ConvertFrom-Json
        if ($json.hotkey_label) { $Key = $json.hotkey_label }
        elseif ($json.hotkey) { $Key = ([string]$json.hotkey).ToUpper() }
    } catch { }
}

Write-Host ""
Write-Host "Ready! Press $Key to talk."
Write-Host "Stop later with: mintflow quit"
Write-Host "If mintflow is not found, close this window and open a new PowerShell."
