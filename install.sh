#!/usr/bin/env bash
# ByteCurve Payroll Adjustment — one-step CLI setup (macOS / Linux)
# Usage: bash install.sh [--full | --ai]
#   --full   Install with GUI + AI/ML extras
#   --ai     Install with AI/ML extras only (no GUI)
#   (none)   Install CLI-only (no GUI, no AI)

set -euo pipefail

EXTRAS=""
for arg in "$@"; do
    case "$arg" in
        --full) EXTRAS="[full]" ;;
        --ai)   EXTRAS="[ai]"   ;;
    esac
done

echo "=== ByteCurve Payroll — setup (macOS / Linux) ==="

# 1. Create virtual environment
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "[1/4] Virtual environment created."
else
    echo "[1/4] Virtual environment already exists, skipping."
fi

# 2. Activate and upgrade pip
source .venv/bin/activate
pip install --quiet --upgrade pip

# 3. Install package
echo "[2/4] Installing bytecurve-payroll${EXTRAS}..."
pip install --quiet ".${EXTRAS}"

# 4. Install Playwright's Chromium + Chrome driver
echo "[3/4] Installing Playwright browser (Chrome)..."
playwright install chrome

echo "[4/4] Done."
echo ""
echo "Activate the environment and run:"
echo "  source .venv/bin/activate"
echo "  bytecurve --help"
echo ""
echo "To run for a specific date:"
echo "  bytecurve --date YYYY-MM-DD"
