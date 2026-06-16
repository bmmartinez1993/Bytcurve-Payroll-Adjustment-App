# ByteCurve Payroll Adjustment — one-step CLI setup (Windows)
# Usage: .\install.ps1 [-Full] [-Ai]
#   -Full   Install with GUI + AI/ML extras
#   -Ai     Install with AI/ML extras only (no GUI)
#   (none)  Install CLI-only (no GUI, no AI)

param(
    [switch]$Full,
    [switch]$Ai
)

$Extras = ""
if ($Full) { $Extras = "[full]" }
elseif ($Ai) { $Extras = "[ai]" }

Write-Host "=== ByteCurve Payroll — setup (Windows) ===" -ForegroundColor Cyan

# 1. Create virtual environment
if (-not (Test-Path ".venv")) {
    python -m venv .venv
    Write-Host "[1/4] Virtual environment created." -ForegroundColor Green
} else {
    Write-Host "[1/4] Virtual environment already exists, skipping." -ForegroundColor Yellow
}

# 2. Activate and upgrade pip
.\.venv\Scripts\Activate.ps1
python -m pip install --quiet --upgrade pip

# 3. Install package
Write-Host "[2/4] Installing bytecurve-payroll$Extras..." -ForegroundColor Green
pip install --quiet ".$Extras"

# 4. Install Playwright Chrome
Write-Host "[3/4] Installing Playwright browser (Chrome)..." -ForegroundColor Green
playwright install chrome

Write-Host "[4/4] Done." -ForegroundColor Green
Write-Host ""
Write-Host "Activate the environment and run:" -ForegroundColor Cyan
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  bytecurve --help"
Write-Host ""
Write-Host "To run for a specific date:"
Write-Host "  bytecurve --date YYYY-MM-DD"
