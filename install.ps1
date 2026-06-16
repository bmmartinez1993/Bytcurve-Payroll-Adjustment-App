# ByteCurve Payroll Adjustment — self-contained installer (Windows PowerShell)
#
# ── Remote install (no repo clone needed) ─────────────────────────────────────
#   irm https://raw.githubusercontent.com/bmmartinez1993/Bytcurve-Payroll-Adjustment-App/main/install.ps1 | iex
#
# With extras (set env var before piping):
#   $env:BYTECURVE_EXTRAS="ai";   irm .../install.ps1 | iex   # + AI/ML features
#   $env:BYTECURVE_EXTRAS="full"; irm .../install.ps1 | iex   # + GUI + AI/ML features
#   $env:BYTECURVE_UPDATE="1";    irm .../install.ps1 | iex   # pull latest and reinstall
#
# ── Local install (inside a cloned repo) ──────────────────────────────────────
#   .\install.ps1 [-Ai] [-Full] [-Update]

param(
    [switch]$Ai,
    [switch]$Full,
    [switch]$Update
)

$RepoUrl     = "https://github.com/bmmartinez1993/Bytcurve-Payroll-Adjustment-App.git"
$DefaultDir  = Join-Path $env:USERPROFILE ".bytecurve"
$BinDir      = Join-Path $env:USERPROFILE ".local\bin"

# Extras: flag params take priority, then fall back to env var (for irm | iex usage)
$Extras = ""
if     ($Full)                             { $Extras = "[full]" }
elseif ($Ai)                               { $Extras = "[ai]"   }
elseif ($env:BYTECURVE_EXTRAS -eq "full")  { $Extras = "[full]" }
elseif ($env:BYTECURVE_EXTRAS -eq "ai")    { $Extras = "[ai]"   }

$DoUpdate = $Update -or ($env:BYTECURVE_UPDATE -eq "1")

# ── Local vs remote mode ──────────────────────────────────────────────────────
if ((Test-Path "pyproject.toml") -and (Test-Path "cli.py")) {
    $InstallDir = $PWD.Path
    Write-Host "=== ByteCurve Payroll — local setup ===" -ForegroundColor Cyan
    Write-Host "[1/5] Using existing repo at $InstallDir." -ForegroundColor Green
} else {
    $InstallDir = $DefaultDir
    Write-Host "=== ByteCurve Payroll — installing to $InstallDir ===" -ForegroundColor Cyan

    if (Test-Path (Join-Path $InstallDir ".git")) {
        if ($DoUpdate) {
            Write-Host "[1/5] Updating existing install..." -ForegroundColor Green
            git -C $InstallDir pull --ff-only
        } else {
            Write-Host "[1/5] Found existing install (pass -Update to refresh)." -ForegroundColor Yellow
        }
    } else {
        Write-Host "[1/5] Downloading repository..." -ForegroundColor Green
        git clone --depth 1 $RepoUrl $InstallDir
    }
}

$Venv = Join-Path $InstallDir ".venv"

# ── Virtual environment ───────────────────────────────────────────────────────
if (-not (Test-Path $Venv)) {
    Write-Host "[2/5] Creating virtual environment..." -ForegroundColor Green
    python -m venv $Venv
} else {
    Write-Host "[2/5] Virtual environment already exists." -ForegroundColor Yellow
}

& "$Venv\Scripts\pip.exe" install --quiet --upgrade pip

# ── Install package ───────────────────────────────────────────────────────────
Write-Host "[3/5] Installing bytecurve-payroll$Extras..." -ForegroundColor Green
& "$Venv\Scripts\pip.exe" install --quiet "$InstallDir$Extras"

# ── Playwright Chrome ─────────────────────────────────────────────────────────
Write-Host "[4/5] Installing Playwright Chrome driver..." -ForegroundColor Green
& "$Venv\Scripts\playwright.exe" install chrome

# ── Register global command ───────────────────────────────────────────────────
Write-Host "[5/5] Registering 'bytecurve' command in $BinDir..." -ForegroundColor Green
New-Item -ItemType Directory -Force $BinDir | Out-Null

# Wrapper batch file — works in cmd.exe and PowerShell
$WrapperPath = Join-Path $BinDir "bytecurve.cmd"
$WrapperContent = "@echo off`r`n`"$Venv\Scripts\python.exe`" `"$InstallDir\cli.py`" %*"
Set-Content -Path $WrapperPath -Value $WrapperContent -Encoding ASCII

# Add BinDir to user PATH if not already present
$UserPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($UserPath -notlike "*$BinDir*") {
    [Environment]::SetEnvironmentVariable("PATH", "$UserPath;$BinDir", "User")
    Write-Host "Added $BinDir to your user PATH." -ForegroundColor Green
}

Write-Host ""
Write-Host "Installation complete!" -ForegroundColor Cyan
Write-Host ""
Write-Host "Restart your terminal (so PATH updates), then run:" -ForegroundColor Cyan
Write-Host "  bytecurve --help"
Write-Host "  bytecurve --date 2026-06-13"
