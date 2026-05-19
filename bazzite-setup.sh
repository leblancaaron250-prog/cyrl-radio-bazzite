#!/usr/bin/env bash
# =============================================================================
#  CYRL Radio Listener — BazziteOS / Fedora Setup Script
#  Red Lake Airport (CYRL)
#
#  Installs and configures the voice-to-board pipeline on a BazziteOS laptop.
#  BazziteOS is based on Fedora Atomic (uses rpm-ostree or dnf/distrobox).
#
#  Usage:
#    chmod +x bazzite-setup.sh
#    bash bazzite-setup.sh
#
#  What this installs:
#    • Python 3 + pip (system)
#    • PortAudio (audio I/O library for sounddevice)
#    • FFmpeg (audio format support)
#    • openai-whisper, sounddevice, torch, scipy, requests, numpy (pip)
#    • Silero VAD model (auto-downloads on first run)
#    • cyrl-radio systemd user service (no root needed for service)
#    • /etc/cyrl-pi/config  (you fill in WORKER_SECRET)
#
#  NOTE: BazziteOS uses an immutable filesystem. System packages are installed
#  via 'rpm-ostree install' (applies on next boot) OR via a Distrobox container.
#  This script auto-detects which method is available and uses the right one.
#  Python packages are always installed to ~/.local (user pip, no root).
# =============================================================================

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()      { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()     { echo -e "${RED}[ERROR]${NC} $*" >&2; }
header()  { echo -e "\n${BOLD}${CYAN}══ $* ══${NC}\n"; }

# ── Paths ─────────────────────────────────────────────────────────────────────
INSTALL_DIR="$HOME/.local/opt/cyrl-radio"
CONFIG_FILE="/etc/cyrl-pi/config"
LOG_DIR="$HOME/.local/share/cyrl-radio/logs"
SERVICE_DIR="$HOME/.config/systemd/user"
SCRIPT_SRC="$(dirname "$(realpath "$0")")/radio-listener.py"

header "CYRL Radio Listener — BazziteOS Setup"
echo "  Install dir : $INSTALL_DIR"
echo "  Config file : $CONFIG_FILE"
echo "  Log dir     : $LOG_DIR"
echo ""

# ── Detect Bazzite/Fedora environment ────────────────────────────────────────
header "Detecting environment"

IS_BAZZITE=false
IS_FEDORA=false
HAS_RPM_OSTREE=false
HAS_DNF=false
HAS_DISTROBOX=false

[[ -f /etc/os-release ]] && source /etc/os-release
[[ "${ID:-}" == "bazzite" || "${ID_LIKE:-}" =~ "fedora" ]] && IS_BAZZITE=true
[[ "${ID:-}" == "fedora" || "${ID_LIKE:-}" =~ "fedora" ]]  && IS_FEDORA=true
command -v rpm-ostree &>/dev/null && HAS_RPM_OSTREE=true
command -v dnf        &>/dev/null && HAS_DNF=true
command -v distrobox  &>/dev/null && HAS_DISTROBOX=true

info "OS: ${PRETTY_NAME:-unknown}"
info "rpm-ostree: $HAS_RPM_OSTREE | dnf: $HAS_DNF | distrobox: $HAS_DISTROBOX"

# ── Install system packages ───────────────────────────────────────────────────
header "Installing system packages"

SYS_PKGS="python3 python3-pip portaudio portaudio-devel ffmpeg gcc python3-devel"

install_via_dnf() {
    info "Installing via dnf (layered or standard Fedora)..."
    sudo dnf install -y $SYS_PKGS 2>&1 | grep -E "Install|Already|Error" || true
}

install_via_rpm_ostree() {
    info "Detected rpm-ostree (immutable Bazzite). Checking if packages are already layered..."
    MISSING=()
    for pkg in portaudio portaudio-devel ffmpeg python3-pip; do
        rpm -q "$pkg" &>/dev/null || MISSING+=("$pkg")
    done
    if [[ ${#MISSING[@]} -gt 0 ]]; then
        warn "Missing packages: ${MISSING[*]}"
        warn "rpm-ostree requires a reboot after layering packages."
        echo ""
        read -r -p "  Layer packages now and reboot? [y/N] " confirm
        if [[ "${confirm,,}" == "y" ]]; then
            sudo rpm-ostree install --apply-live ${MISSING[*]} || {
                warn "--apply-live failed, falling back to standard layer (reboot required)"
                sudo rpm-ostree install ${MISSING[*]}
                warn ""
                warn "PACKAGES QUEUED. Please reboot and run this script again."
                warn "  sudo systemctl reboot"
                exit 0
            }
        else
            warn "Skipping system packages. Python pip install may fail without portaudio-devel."
            warn "You can install manually: sudo rpm-ostree install ${MISSING[*]}"
        fi
    else
        ok "All system packages already present"
    fi
}

if $HAS_DNF && ! $HAS_RPM_OSTREE; then
    install_via_dnf
elif $HAS_RPM_OSTREE; then
    install_via_rpm_ostree
else
    warn "No supported package manager found. Attempting pip install directly..."
    warn "If it fails, install manually: portaudio portaudio-devel python3-pip ffmpeg"
fi

# ── Python packages ───────────────────────────────────────────────────────────
header "Installing Python packages"
info "Installing to ~/.local (user pip, no root required)"

# Upgrade pip first
python3 -m pip install --upgrade pip --quiet

PIP_PKGS=(
    "openai-whisper"
    "sounddevice"
    "scipy"
    "requests"
    "numpy"
)

# Torch — CPU-only is fine for a laptop running Whisper small/base
# CPU install is much smaller (~200 MB vs ~2 GB for CUDA)
info "Installing PyTorch (CPU-only — appropriate for laptop use)..."
python3 -m pip install --upgrade \
    torch torchaudio \
    --index-url https://download.pytorch.org/whl/cpu \
    --quiet && ok "PyTorch installed" || {
    warn "CPU-only torch install failed — trying standard index..."
    python3 -m pip install torch torchaudio --quiet
}

info "Installing audio/ML packages..."
python3 -m pip install --upgrade "${PIP_PKGS[@]}" --quiet && ok "Python packages installed"

# ── Pre-download Silero VAD model ─────────────────────────────────────────────
header "Pre-downloading Silero VAD model"
info "This runs once — ~10 MB download"
python3 - <<'PYEOF' || warn "Silero VAD pre-download failed — will retry on first run"
import torch
print("Downloading Silero VAD...")
model, utils = torch.hub.load(
    repo_or_dir='snakers4/silero-vad',
    model='silero_vad',
    force_reload=False,
    onnx=False,
    verbose=False,
)
print("Silero VAD ready.")
PYEOF

# ── Pre-download Whisper model ────────────────────────────────────────────────
header "Pre-downloading Whisper model"
WHISPER_MODEL="${WHISPER_MODEL:-small}"
info "Downloading Whisper '$WHISPER_MODEL' model — this may take a few minutes..."
python3 - <<PYEOF || warn "Whisper pre-download failed — will retry on first run"
import whisper
print(f"Downloading Whisper model: ${WHISPER_MODEL}")
m = whisper.load_model("${WHISPER_MODEL}")
print("Whisper model ready.")
PYEOF

# ── Install listener script ───────────────────────────────────────────────────
header "Installing radio listener"
mkdir -p "$INSTALL_DIR"
mkdir -p "$LOG_DIR"

if [[ -f "$SCRIPT_SRC" ]]; then
    cp "$SCRIPT_SRC" "$INSTALL_DIR/radio-listener.py"
    chmod +x "$INSTALL_DIR/radio-listener.py"
    ok "Copied radio-listener.py → $INSTALL_DIR/"
else
    warn "radio-listener.py not found next to setup script."
    warn "Download it and copy to: $INSTALL_DIR/radio-listener.py"
fi

# ── Write config file ─────────────────────────────────────────────────────────
header "Writing config"

if [[ -f "$CONFIG_FILE" ]]; then
    warn "$CONFIG_FILE already exists — skipping (preserving your settings)"
else
    if [[ ! -w /etc/cyrl-pi ]] && [[ ! -d /etc/cyrl-pi ]]; then
        sudo mkdir -p /etc/cyrl-pi
        sudo chmod 755 /etc/cyrl-pi
    fi
    sudo tee "$CONFIG_FILE" > /dev/null <<'EOF'
# CYRL Radio Listener — Configuration
# Edit this file, then restart the service: systemctl --user restart cyrl-radio

# Cloudflare Worker URL (do not change unless you redeploy)
WORKER_URL=https://cyrl-fa-relay.cyrl-airport.workers.dev

# Secret key — MUST match the key set in Admin panel → Pi Feeder → Radio Listener Secret
# Get it from: https://admin.redlakeairport.ca
WORKER_SECRET=REPLACE_ME

# Whisper model: base (fastest), small (recommended), medium (most accurate)
WHISPER_MODEL=small

# Audio device index — run: python3 -c "import sounddevice; print(sounddevice.query_devices())"
# Look for your USB audio adapter or combo-jack mic input
AUDIO_DEVICE=1

# Frequency being monitored (informational only — displayed in logs)
FREQUENCY=122.300

# VAD sensitivity: 0.0 (most sensitive) → 1.0 (least sensitive)
# Lower = picks up more transmissions, higher = requires clearer speech
VAD_THRESHOLD=0.35

# Seconds of silence before a clip is considered complete
SILENCE_SECONDS=1.5

# Minimum clip length to bother transcribing (seconds)
MIN_SPEECH_SECONDS=0.8

# Local ADS-B feed (optional — used to cross-reference registrations)
# Leave default if not running dump1090; listener will skip cross-reference
DUMP1090_URL=http://127.0.0.1:8080/data/aircraft.json

# Log directory (user-writable, no root required on Bazzite)
LOG_DIR=REPLACE_WITH_HOME/.local/share/cyrl-radio/logs
EOF
    # Replace placeholder with actual home
    sudo sed -i "s|REPLACE_WITH_HOME|$HOME|g" "$CONFIG_FILE"
    ok "Config written to $CONFIG_FILE"
    warn "IMPORTANT: Edit $CONFIG_FILE and set WORKER_SECRET before starting the service"
fi

# ── systemd user service ──────────────────────────────────────────────────────
header "Installing systemd user service"
mkdir -p "$SERVICE_DIR"

cat > "$SERVICE_DIR/cyrl-radio.service" <<EOF
[Unit]
Description=CYRL Radio Listener — Whisper ATC voice-to-board pipeline
Documentation=https://github.com/leblancaaron250-prog/cyrl-airport
After=network-online.target sound.target pipewire.service pipewire-pulse.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=${HOME}/.local/bin/python3 ${INSTALL_DIR}/radio-listener.py
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal
SyslogIdentifier=cyrl-radio
Environment=PYTHONUNBUFFERED=1
Environment=HOME=${HOME}
WorkingDirectory=${INSTALL_DIR}

[Install]
WantedBy=default.target
EOF

# Use python3 from PATH
PYTHON_BIN=$(command -v python3)
sed -i "s|${HOME}/.local/bin/python3|${PYTHON_BIN}|g" "$SERVICE_DIR/cyrl-radio.service"

systemctl --user daemon-reload
ok "systemd user service installed: cyrl-radio"

# Enable lingering so the service runs even when not logged in (optional)
if command -v loginctl &>/dev/null; then
    loginctl enable-linger "$USER" 2>/dev/null && \
        info "Linger enabled — service survives logout" || \
        warn "Could not enable linger (service will stop on logout)"
fi

# ── PipeWire / audio permissions ──────────────────────────────────────────────
header "Audio setup (PipeWire)"
info "Bazzite uses PipeWire. Checking audio group membership..."

if groups | grep -qw "audio"; then
    ok "User is in 'audio' group"
else
    warn "User not in 'audio' group. Adding..."
    sudo usermod -aG audio "$USER" && \
        warn "Added to audio group — log out and back in for this to take effect" || \
        warn "Could not add to audio group — you may need to do this manually: sudo usermod -aG audio $USER"
fi

# PipeWire volume / input source check
if command -v pactl &>/dev/null; then
    info "Available audio sources (microphone/line-in inputs):"
    pactl list sources short 2>/dev/null | grep -v "monitor" | head -10 || true
fi

# ── Audio device detection helper ────────────────────────────────────────────
header "Detecting audio input devices"
python3 - <<'PYEOF'
try:
    import sounddevice as sd
    devices = sd.query_devices()
    inputs = [(i, d) for i, d in enumerate(devices) if d['max_input_channels'] > 0]
    if inputs:
        print("  Input-capable audio devices:")
        for i, d in inputs:
            default = " ← DEFAULT INPUT" if i == sd.default.device[0] else ""
            print(f"    [{i}] {d['name']} (channels: {d['max_input_channels']}){default}")
        print()
        print("  → Set AUDIO_DEVICE=<index> in /etc/cyrl-pi/config")
    else:
        print("  No input devices found yet. Connect your USB audio adapter and re-run.")
except Exception as e:
    print(f"  Could not query devices: {e}")
PYEOF

# ── Summary ───────────────────────────────────────────────────────────────────
header "Setup complete"
echo -e "${BOLD}Next steps:${NC}"
echo ""
echo -e "  1. ${YELLOW}Edit config:${NC}"
echo "     sudo nano $CONFIG_FILE"
echo "     → Set WORKER_SECRET (from Admin panel → Pi Feeder)"
echo "     → Set AUDIO_DEVICE (index from the list above)"
echo ""
echo -e "  2. ${YELLOW}Test audio input:${NC}"
echo "     bash test-audio.sh"
echo ""
echo -e "  3. ${YELLOW}Start the listener:${NC}"
echo "     systemctl --user start cyrl-radio"
echo ""
echo -e "  4. ${YELLOW}Watch live logs:${NC}"
echo "     journalctl --user -u cyrl-radio -f"
echo ""
echo -e "  5. ${YELLOW}Enable at login:${NC}"
echo "     systemctl --user enable cyrl-radio"
echo ""
echo -e "  ${GREEN}Admin panel:${NC} https://admin.redlakeairport.ca → Pi Feeder"
echo ""
