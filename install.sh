#!/usr/bin/env bash
# ByteCurve Payroll Adjustment — self-contained installer (macOS / Linux)
#
# ── Remote install (no repo clone needed) ─────────────────────────────────────
#   curl -fsSL https://raw.githubusercontent.com/bmmartinez1993/Bytcurve-Payroll-Adjustment-App/main/install.sh | bash
#
# With extras:
#   curl -fsSL .../install.sh | bash -s -- --ai      # + AI/ML features
#   curl -fsSL .../install.sh | bash -s -- --full    # + GUI + AI/ML features
#   curl -fsSL .../install.sh | bash -s -- --update  # pull latest and reinstall
#
# ── Local install (inside a cloned repo) ──────────────────────────────────────
#   bash install.sh [--ai | --full | --update]

set -euo pipefail

REPO_URL="https://github.com/bmmartinez1993/Bytcurve-Payroll-Adjustment-App.git"
DEFAULT_INSTALL_DIR="${HOME}/.bytecurve"
BIN_DIR="${HOME}/.local/bin"

# ── Parse flags ───────────────────────────────────────────────────────────────
EXTRAS=""
UPDATE=false
for arg in "$@"; do
    case "$arg" in
        --full)   EXTRAS="[full]" ;;
        --ai)     EXTRAS="[ai]"   ;;
        --update) UPDATE=true     ;;
    esac
done

# ── Local vs remote mode ──────────────────────────────────────────────────────
# When piped from curl, the current directory is not the repo.
if [ -f "${PWD}/pyproject.toml" ] && [ -f "${PWD}/cli.py" ]; then
    INSTALL_DIR="${PWD}"
    echo "=== ByteCurve Payroll — local setup ==="
    echo "[1/5] Using existing repo at ${INSTALL_DIR}."
else
    INSTALL_DIR="${DEFAULT_INSTALL_DIR}"
    echo "=== ByteCurve Payroll — installing to ${INSTALL_DIR} ==="

    if [ -d "${INSTALL_DIR}/.git" ]; then
        if $UPDATE; then
            echo "[1/5] Updating existing install..."
            git -C "${INSTALL_DIR}" pull --ff-only
        else
            echo "[1/5] Found existing install (pass --update to refresh)."
        fi
    else
        echo "[1/5] Downloading repository..."
        git clone --depth 1 "${REPO_URL}" "${INSTALL_DIR}"
    fi
fi

# ── Virtual environment ───────────────────────────────────────────────────────
VENV="${INSTALL_DIR}/.venv"
if [ ! -d "${VENV}" ]; then
    echo "[2/5] Creating virtual environment..."
    python3 -m venv "${VENV}"
else
    echo "[2/5] Virtual environment already exists."
fi

"${VENV}/bin/pip" install --quiet --upgrade pip

# ── Install package ───────────────────────────────────────────────────────────
echo "[3/5] Installing bytecurve-payroll${EXTRAS}..."
"${VENV}/bin/pip" install --quiet "${INSTALL_DIR}${EXTRAS}"

# ── Playwright Chrome ─────────────────────────────────────────────────────────
echo "[4/5] Installing Playwright Chrome driver..."
"${VENV}/bin/playwright" install chrome

# ── Register global command ───────────────────────────────────────────────────
echo "[5/5] Registering 'bytecurve' command in ${BIN_DIR}..."
mkdir -p "${BIN_DIR}"
cat > "${BIN_DIR}/bytecurve" <<WRAPPER
#!/usr/bin/env bash
exec "${VENV}/bin/python" "${INSTALL_DIR}/cli.py" "\$@"
WRAPPER
chmod +x "${BIN_DIR}/bytecurve"

# ── PATH notice ───────────────────────────────────────────────────────────────
echo ""
echo "Installation complete!"
echo ""

SHELL_RC="${HOME}/.bashrc"
[[ "${SHELL:-bash}" == *"zsh"* ]] && SHELL_RC="${HOME}/.zshrc"

if echo ":${PATH}:" | grep -q ":${BIN_DIR}:"; then
    echo "Run:  bytecurve --help"
    echo "      bytecurve --date 2026-06-13"
else
    echo "${BIN_DIR} is not in your PATH yet. Add it:"
    echo "  echo 'export PATH=\"\${HOME}/.local/bin:\${PATH}\"' >> ${SHELL_RC}"
    echo "  source ${SHELL_RC}"
    echo ""
    echo "Then run:  bytecurve --help"
fi
