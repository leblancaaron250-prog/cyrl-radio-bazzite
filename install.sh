#!/usr/bin/env bash
# =============================================================================
#  CYRL Radio Listener — One-Line Installer for BazziteOS / Fedora
#  Red Lake Airport (CYRL)
#
#  Usage (paste this into your terminal):
#
#    bash <(curl -fsSL https://raw.githubusercontent.com/leblancaaron250-prog/cyrl-radio-bazzite/main/install.sh)
#
#  What this does:
#    1. Checks that git is available (installs it if not)
#    2. Clones this repo to ~/cyrl-radio-bazzite  (or pulls if already cloned)
#    3. Runs bazzite-setup.sh inside the cloned repo
#
#  You can also clone manually and run setup directly:
#    git clone https://github.com/leblancaaron250-prog/cyrl-radio-bazzite.git
#    cd cyrl-radio-bazzite
#    bash bazzite-setup.sh
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()   { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()     { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()   { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()    { echo -e "${RED}[ERROR]${NC} $*" >&2; }
header() { echo -e "\n${BOLD}${CYAN}══ $* ══${NC}\n"; }

REPO_URL="https://github.com/leblancaaron250-prog/cyrl-radio-bazzite.git"
CLONE_DIR="$HOME/cyrl-radio-bazzite"

header "CYRL Radio Listener — Bootstrap Installer"
echo "  Repo   : $REPO_URL"
echo "  Target : $CLONE_DIR"
echo ""

# ── 1. Ensure git is available ────────────────────────────────────────────────
if ! command -v git &>/dev/null; then
    warn "git not found — attempting to install..."
    if command -v rpm-ostree &>/dev/null; then
        info "Installing git via rpm-ostree (immutable Bazzite)..."
        sudo rpm-ostree install --apply-live git 2>&1 | tail -5 || {
            err "rpm-ostree install failed. Install git manually:"
            err "  sudo rpm-ostree install git"
            err "Then reboot and re-run this script."
            exit 1
        }
    elif command -v dnf &>/dev/null; then
        info "Installing git via dnf..."
        sudo dnf install -y git
    else
        err "Cannot install git automatically."
        err "Install it manually and re-run this script."
        exit 1
    fi
    ok "git installed"
fi

# ── 2. Clone or update the repo ───────────────────────────────────────────────
if [[ -d "$CLONE_DIR/.git" ]]; then
    info "Repo already exists at $CLONE_DIR — pulling latest changes..."
    git -C "$CLONE_DIR" pull --ff-only && ok "Updated to latest version"
else
    info "Cloning repo to $CLONE_DIR ..."
    git clone "$REPO_URL" "$CLONE_DIR"
    ok "Cloned successfully"
fi

# ── 3. Run the setup script ───────────────────────────────────────────────────
header "Running setup"
info "Handing off to bazzite-setup.sh..."
echo ""

cd "$CLONE_DIR"
chmod +x bazzite-setup.sh
bash bazzite-setup.sh

# ── Done ──────────────────────────────────────────────────────────────────────
# (bazzite-setup.sh prints its own summary — nothing more needed here)
