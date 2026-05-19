#!/usr/bin/env bash
# =============================================================================
#  CYRL Audio Input Test — BazziteOS
#  Run this to verify your scanner/cable/USB adapter is working before
#  starting the full radio listener pipeline.
# =============================================================================

set -euo pipefail

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'

info()   { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()     { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()   { echo -e "${YELLOW}[WARN]${NC}  $*"; }
header() { echo -e "\n${BOLD}${CYAN}══ $* ══${NC}\n"; }

header "CYRL Audio Input Test"

# ── Read config ───────────────────────────────────────────────────────────────
CONFIG_FILE="/etc/cyrl-pi/config"
AUDIO_DEVICE=1
[[ -f "$CONFIG_FILE" ]] && {
    while IFS='=' read -r k v; do
        k="${k%%#*}"; k="${k// /}"
        [[ "$k" == "AUDIO_DEVICE" ]] && AUDIO_DEVICE="${v// /}"
    done < "$CONFIG_FILE"
}
info "Using AUDIO_DEVICE=$AUDIO_DEVICE (from $CONFIG_FILE)"
echo ""

# ── List all audio devices ────────────────────────────────────────────────────
header "1. Available audio devices"
python3 - <<PYEOF
import sounddevice as sd
devices = sd.query_devices()
print(f"  {'IDX':<4} {'NAME':<45} {'IN CH':<6} {'OUT CH'}")
print(f"  {'─'*4} {'─'*45} {'─'*6} {'─'*6}")
for i, d in enumerate(devices):
    marker = ""
    if i == sd.default.device[0]: marker += " ← default in"
    if i == sd.default.device[1]: marker += " ← default out"
    print(f"  [{i:<2}] {d['name']:<45} {d['max_input_channels']:<6} {d['max_output_channels']}{marker}")
print()
print("  TIP: Look for 'USB Audio', 'USB PnP', 'C-Media', or your adapter name.")
print("  Set AUDIO_DEVICE=<index> in /etc/cyrl-pi/config")
PYEOF

echo ""
read -r -p "  Which device index to test? [${AUDIO_DEVICE}]: " input_dev
AUDIO_DEVICE="${input_dev:-$AUDIO_DEVICE}"

# ── PipeWire/PulseAudio source check ─────────────────────────────────────────
header "2. PipeWire audio sources"
if command -v pactl &>/dev/null; then
    echo "  Input sources (microphones / line-in):"
    pactl list sources short 2>/dev/null | grep -v "\.monitor" | \
        awk '{printf "  %-50s %s\n", $2, $5}' | head -10
    echo ""
    info "If your USB adapter shows here but not in the Python list, it may need"
    info "to be set as default in Settings → Sound → Input."
fi

# ── Silence level test ────────────────────────────────────────────────────────
header "3. Silence level (no transmission)"
info "Recording 3 seconds of silence — keep the scanner squelch closed..."
python3 - <<PYEOF
import sounddevice as sd, numpy as np, time
DEVICE = ${AUDIO_DEVICE}
RATE   = 16000
SECS   = 3
try:
    data = sd.rec(int(RATE * SECS), samplerate=RATE, channels=1, device=DEVICE, dtype='int16')
    sd.wait()
    rms    = np.sqrt(np.mean(data.astype(np.float64)**2))
    peak   = np.max(np.abs(data))
    rating = "GOOD (very quiet)" if rms < 200 else \
             "OK (some background noise)" if rms < 800 else \
             "HIGH — check cable, gain, or grounding"
    print(f"  RMS amplitude : {rms:.0f}  ({rating})")
    print(f"  Peak amplitude: {peak}")
    print(f"  Target silence: RMS < 200")
except Exception as e:
    print(f"  ERROR: {e}")
    print(f"  → Try a different device index, or check PipeWire settings")
PYEOF

# ── Active transmission test ──────────────────────────────────────────────────
header "4. Active signal level (key up scanner)"
echo "  Key up your scanner or talk near the mic..."
read -r -p "  Press ENTER when ready to record 5 seconds: "
python3 - <<PYEOF
import sounddevice as sd, numpy as np
DEVICE = ${AUDIO_DEVICE}
RATE   = 16000
SECS   = 5
try:
    print("  Recording...")
    data = sd.rec(int(RATE * SECS), samplerate=RATE, channels=1, device=DEVICE, dtype='int16')
    sd.wait()
    rms    = np.sqrt(np.mean(data.astype(np.float64)**2))
    peak   = np.max(np.abs(data))

    if rms > 1000:
        rating = "EXCELLENT"
        advice = "Audio level is great — Whisper will transcribe reliably."
    elif rms > 400:
        rating = "GOOD"
        advice = "Audio level is adequate. Whisper should work well."
    elif rms > 100:
        rating = "LOW"
        advice = "Signal is weak. Try: increase scanner volume, check cable connection,\n  or lower VAD_THRESHOLD to 0.2 in config."
    else:
        rating = "TOO LOW / SILENT"
        advice = "Barely any signal detected. Check cable, scanner output, and audio device index."

    print(f"  RMS amplitude : {rms:.0f}  → {rating}")
    print(f"  Peak amplitude: {peak}")
    print(f"  Advice: {advice}")
except Exception as e:
    print(f"  ERROR: {e}")
    print(f"  → Device {DEVICE} may not be correct. Try a different index.")
PYEOF

# ── VAD test ──────────────────────────────────────────────────────────────────
header "5. VAD (voice detection) test"
info "Testing whether Silero VAD can detect your transmission..."
echo ""
read -r -p "  Press ENTER, then speak or key up scanner (5 seconds): "
python3 - <<PYEOF
import sounddevice as sd, numpy as np, torch
DEVICE = ${AUDIO_DEVICE}
RATE   = 16000
SECS   = 5

try:
    vad_model, utils = torch.hub.load(
        repo_or_dir='snakers4/silero-vad', model='silero_vad',
        force_reload=False, onnx=False, verbose=False
    )
    print("  VAD model loaded. Recording...")
    data = sd.rec(int(RATE * SECS), samplerate=RATE, channels=1, device=DEVICE, dtype='int16')
    sd.wait()
    audio = data.flatten().astype(np.float32) / 32768.0

    # Score in 512-sample chunks
    chunk = 512
    scores = []
    for i in range(0, len(audio) - chunk, chunk):
        t = torch.FloatTensor(audio[i:i+chunk])
        s = vad_model(t, RATE).item()
        scores.append(s)

    max_score = max(scores) if scores else 0
    avg_score = sum(scores) / len(scores) if scores else 0
    speech_chunks = sum(1 for s in scores if s > 0.35)

    print(f"  Max VAD score  : {max_score:.3f}  (threshold: 0.35)")
    print(f"  Avg VAD score  : {avg_score:.3f}")
    print(f"  Speech chunks  : {speech_chunks}/{len(scores)}")

    if max_score > 0.5:
        print("  ✓ VAD is detecting speech/signal reliably")
    elif max_score > 0.35:
        print("  ⚠ VAD is marginal — consider lowering VAD_THRESHOLD to 0.2 in config")
    else:
        print("  ✗ VAD did not detect speech — check audio level (test 4 above)")
except Exception as e:
    print(f"  VAD test failed: {e}")
PYEOF

# ── Quick Whisper transcription test ─────────────────────────────────────────
header "6. Whisper transcription test"
info "Recording 8 seconds — speak clearly or key up scanner with a transmission..."
echo "  Say something like: 'Kenora radio, this is Foxtrot Victor Whiskey Yankee, ten miles back'"
echo ""
read -r -p "  Press ENTER to start recording: "
python3 - <<PYEOF
import sounddevice as sd, numpy as np, tempfile, os
import scipy.io.wavfile as wav_io
import whisper

DEVICE  = ${AUDIO_DEVICE}
RATE    = 16000
SECS    = 8
MODEL   = "small"

PROMPT = (
    "Kenora radio, this is aircraft C-FXYZ, Piper Navajo, "
    "10 miles back from the field, inbound runway 08, "
    "Red Lake, CYRL, Perimeter, Wasaya, North Star, Superior, Skycare, "
    "landing, departing, circuit, final"
)

try:
    print("  Recording 8 seconds...")
    data = sd.rec(int(RATE * SECS), samplerate=RATE, channels=1, device=DEVICE, dtype='int16')
    sd.wait()
    print("  Transcribing with Whisper...")

    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        wav_path = f.name
    wav_io.write(wav_path, RATE, data)

    model = whisper.load_model(MODEL)
    result = model.transcribe(wav_path, language='en', initial_prompt=PROMPT, temperature=0.0)
    transcript = result.get('text', '').strip()
    os.unlink(wav_path)

    print()
    print(f"  Transcript: \"{transcript}\"")
    print()
    if transcript:
        print("  ✓ Whisper is transcribing successfully")
    else:
        print("  ⚠ Empty transcript — audio may be too quiet or no speech detected")
except Exception as e:
    print(f"  Whisper test failed: {e}")
PYEOF

# ── Final summary ─────────────────────────────────────────────────────────────
header "Test complete"
echo -e "  If all tests above show ${GREEN}✓${NC}, you're ready to start the service:"
echo ""
echo "    sudo nano /etc/cyrl-pi/config    ← set WORKER_SECRET and AUDIO_DEVICE"
echo "    systemctl --user start cyrl-radio"
echo "    journalctl --user -u cyrl-radio -f"
echo ""
