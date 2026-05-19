#!/usr/bin/env python3
"""
CYRL Radio Listener — BazziteOS / Fedora Edition
Red Lake Airport (CYRL) — voice-to-board pipeline

Identical to the Raspberry Pi version in function, adapted for:
  • PipeWire / PulseAudio (BazziteOS default audio stack)
  • User-mode systemd service (no root required)
  • Fedora-compatible paths and logging
  • Energy-floor VAD fallback tuned for laptop soundcards

Pipeline:
  Scanner (122.300 MHz) → USB audio adapter / combo jack
    → Silero VAD (detects transmissions)
    → Whisper small (aviation-biased transcription)
    → NLP parser (extracts registration, callsign, intent, distance, runway)
    → ADS-B cross-reference (optional, via local dump1090 if running)
    → POST to Cloudflare Worker /radio-report
    → Status board picks it up within one refresh cycle

Configuration: /etc/cyrl-pi/config (key=value, bash-compatible)

Required:
  WORKER_SECRET   — shared secret (set matching value in Admin → Pi Feeder)

Optional / tunable:
  AUDIO_DEVICE    — sounddevice index (default 1; run bazzite-test-audio.sh to find yours)
  WHISPER_MODEL   — base / small (default) / medium
  VAD_THRESHOLD   — 0.0–1.0 (default 0.35 — slightly more sensitive than Pi default)
  SILENCE_SECONDS — clip end detection (default 1.5s)
  LOG_DIR         — default ~/.local/share/cyrl-radio/logs
"""

import os
import sys
import re
import json
import time
import logging
import threading
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

# ── Paths ─────────────────────────────────────────────────────────────────────
HOME = Path.home()
DEFAULT_LOG_DIR = HOME / ".local" / "share" / "cyrl-radio" / "logs"
CONFIG_FILE = Path("/etc/cyrl-pi/config")

# ── Config loader ─────────────────────────────────────────────────────────────
def load_config() -> dict:
    cfg = {
        "WORKER_URL":         "https://cyrl-fa-relay.cyrl-airport.workers.dev",
        "WORKER_SECRET":      "",
        "WHISPER_MODEL":      "small",
        "AUDIO_DEVICE":       "1",
        "FREQUENCY":          "122.300",
        "VAD_THRESHOLD":      "0.35",
        "SILENCE_SECONDS":    "1.5",
        "MIN_SPEECH_SECONDS": "0.8",
        "DUMP1090_URL":       "http://127.0.0.1:8080/data/aircraft.json",
        "LOG_DIR":            str(DEFAULT_LOG_DIR),
        "SAMPLE_RATE":        "16000",
        # Bazzite-specific: PipeWire latency hint (ms)
        "PIPEWIRE_LATENCY":   "128",
    }
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k in cfg:
                cfg[k] = v
    return cfg

CFG = load_config()

LOG_DIR = Path(CFG["LOG_DIR"])
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "listener.log"),
    ],
)
log = logging.getLogger("cyrl-radio")

# ── Config values ─────────────────────────────────────────────────────────────
WORKER_URL         = CFG["WORKER_URL"].rstrip("/")
WORKER_SECRET      = CFG["WORKER_SECRET"]
WHISPER_MODEL_NAME = CFG["WHISPER_MODEL"]
AUDIO_DEVICE_IDX   = int(CFG["AUDIO_DEVICE"])
VAD_THRESHOLD      = float(CFG["VAD_THRESHOLD"])
SILENCE_SECS       = float(CFG["SILENCE_SECONDS"])
MIN_SPEECH_SECS    = float(CFG["MIN_SPEECH_SECONDS"])
DUMP1090_URL       = CFG["DUMP1090_URL"]
SAMPLE_RATE        = int(CFG["SAMPLE_RATE"])
PIPEWIRE_LATENCY   = int(CFG["PIPEWIRE_LATENCY"])

# Set PipeWire latency hint via environment before importing sounddevice
os.environ.setdefault("PIPEWIRE_LATENCY", f"{PIPEWIRE_LATENCY}/48000")

log.info(
    f"Config loaded — Worker: {WORKER_URL} | "
    f"Model: {WHISPER_MODEL_NAME} | Device: {AUDIO_DEVICE_IDX} | "
    f"Freq: {CFG['FREQUENCY']} MHz | VAD: {VAD_THRESHOLD}"
)

# ── Heavy imports ─────────────────────────────────────────────────────────────
import numpy as np
import sounddevice as sd
import scipy.io.wavfile as wav_io
import requests

log.info("Loading Silero VAD…")
try:
    import torch
    vad_model, utils = torch.hub.load(
        repo_or_dir="snakers4/silero-vad",
        model="silero_vad",
        force_reload=False,
        onnx=False,
        verbose=False,
        trust_repo=True,
    )
    VAD_AVAILABLE = True
    log.info("Silero VAD ready")
except Exception as e:
    log.warning(f"Silero VAD unavailable ({e}) — using RMS energy fallback")
    VAD_AVAILABLE = False

log.info(f"Loading Whisper model '{WHISPER_MODEL_NAME}'…")
import whisper as _whisper
_whisper_model = _whisper.load_model(WHISPER_MODEL_NAME)
log.info("Whisper ready — listening for transmissions")

# ── Aviation NLP tables ───────────────────────────────────────────────────────
NATO = {
    "alpha":"A","bravo":"B","charlie":"C","delta":"D","echo":"E",
    "foxtrot":"F","golf":"G","hotel":"H","india":"I","juliet":"J",
    "kilo":"K","lima":"L","mike":"M","november":"N","oscar":"O",
    "papa":"P","quebec":"Q","romeo":"R","sierra":"S","tango":"T",
    "uniform":"U","victor":"V","whiskey":"W","whisky":"W",
    "x-ray":"X","x ray":"X","yankee":"Y","zulu":"Z",
}

NUM_WORDS = {
    "zero":"0","one":"1","two":"2","three":"3","four":"4",
    "five":"5","six":"6","seven":"7","eight":"8","nine":"9","niner":"9",
}

AIRCRAFT_HINTS = {
    "navajo":"Piper PA-31 Navajo","pa-31":"Piper PA-31 Navajo","pa31":"Piper PA-31 Navajo",
    "king air":"Beechcraft King Air","kingair":"Beechcraft King Air",
    "cessna":"Cessna","caravan":"Cessna 208 Caravan",
    "otter":"DHC-6 Twin Otter","twin otter":"DHC-6 Twin Otter","beaver":"DHC-2 Beaver",
    "dash 8":"Bombardier Dash 8","dash8":"Bombardier Dash 8","q400":"Bombardier Q400",
    "metroliner":"Fairchild Metro","metro":"Fairchild Metro",
    "helicopter":"Helicopter","chopper":"Helicopter",
    "medevac":"Medevac","ambulance":"Air Ambulance",
    "beech":"Beechcraft","piper":"Piper",
}

BEARING_WORDS = {
    "north":"N","south":"S","east":"E","west":"W",
    "northeast":"NE","northwest":"NW","southeast":"SE","southwest":"SW",
    "on final":"FINAL",
}

KNOWN_CALLSIGN_PREFIXES = {
    "BLS":"Perimeter Aviation","PAG":"Perimeter Aviation","BSK":"Perimeter Aviation",
    "WSG":"Wasaya Airways","WAS":"Wasaya Airways","MKU":"Wasaya Airways",
    "MCB":"Missinippi Airways",
    "NSA":"North Star Air","NSK":"North Star Air","BF":"North Star Air (Blackfly)",
    "SUP":"Superior Airways","SAL":"Superior Airways","NCB":"Superior Airways","SR":"Superior Airways",
    "PHX":"Skycare Air Ambulance",
    "LIF":"ORNGE","PUL":"ORNGE",
    "JV":"Bearskin Airlines",
}

ARR_KEYWORDS = [
    "land","landing","inbound","arriving","arrival","final","approach",
    "looking to land","request landing","entering the circuit","circuit",
    "downwind","base leg","ten back","five back","miles back",
]
DEP_KEYWORDS = [
    "depart","departing","departure","taking off","rolling","lifting off",
    "backtrack","holding short","ready for departure","outbound","clearance",
    "leaving","departing the circuit",
]

# ── NLP helpers ───────────────────────────────────────────────────────────────
def decode_phonetic_registration(text: str) -> Optional[str]:
    direct = re.search(r'\bC[-\s]?([A-Z]{4})\b', text.upper())
    if direct:
        return f"C-{direct.group(1)}"
    words = text.lower().split()
    letters = []
    for w in words:
        w = w.strip(".,;:!?")
        if w in NATO:
            letters.append(NATO[w])
        elif len(w) == 1 and w.isalpha():
            letters.append(w.upper())
    for start in range(len(letters)):
        seg5 = letters[start:start+5]
        if len(seg5) == 5 and seg5[0] == "C":
            return f"C-{''.join(seg5[1:])}"
        seg4 = letters[start:start+4]
        if len(seg4) == 4:
            return f"C-{''.join(seg4)}"
    return None

def decode_callsign(text: str) -> Optional[str]:
    m = re.search(r'\b([A-Z]{2,3})\s?(\d{1,4})\b', text.upper())
    if m:
        return f"{m.group(1)}{m.group(2)}"
    return None

def extract_distance(text: str) -> Optional[int]:
    patterns = [
        r'(\d+)\s*(?:nautical\s*)?miles?\s*(?:back|out|away|from)',
        r'(\d+)\s*(?:nm|nmi)',
        r'(\d+)\s*miles?\s*(?:north|south|east|west|inbound)',
        r'(\d+)\s*(?:nautical\s*)?miles',
    ]
    tl = text.lower()
    for pat in patterns:
        m = re.search(pat, tl)
        if m:
            v = int(m.group(1))
            if 1 <= v <= 200:
                return v
    for word, digit in NUM_WORDS.items():
        if re.search(rf'\b{word}\s*miles?\b', tl):
            return int(digit)
    return None

def extract_runway(text: str) -> Optional[str]:
    tu = text.upper()
    for pat in [r'RUNWAY\s+(\d{2}[LRC]?)', r'RWY\s+(\d{2}[LRC]?)', r'\b(0[1-9]|[12]\d|3[0-6])[LRC]?\b']:
        m = re.search(pat, tu)
        if m:
            r = m.group(1)
            for w, d in [("ZERO","0"),("ONE","1"),("TWO","2"),("THREE","3"),("FOUR","4"),
                          ("FIVE","5"),("SIX","6"),("SEVEN","7"),("EIGHT","8"),("NINER","9")]:
                r = r.replace(w, d)
            r = re.sub(r'\s+', '', r)
            if re.match(r'^\d{1,2}[LRC]?$', r):
                return r.zfill(2) if len(r) <= 2 else r
    return None

def extract_aircraft_type(text: str) -> Optional[str]:
    tl = text.lower()
    for kw, name in AIRCRAFT_HINTS.items():
        if kw in tl:
            return name
    return None

def classify_intent(text: str) -> str:
    tl = text.lower()
    a = sum(1 for kw in ARR_KEYWORDS if kw in tl)
    d = sum(1 for kw in DEP_KEYWORDS if kw in tl)
    if a > d: return "arrival"
    if d > a: return "departure"
    return "unknown"

def parse_transmission(transcript: str) -> dict:
    result = {
        "registration": None, "callsign": None, "intent": "unknown",
        "distance_nm": None, "runway": None, "aircraft_type": None,
        "bearing": None, "confidence": 0.0, "transcript": transcript,
    }
    t = transcript.strip()
    if not t:
        return result

    result["registration"]   = decode_phonetic_registration(t)
    result["callsign"]       = decode_callsign(t)
    result["intent"]         = classify_intent(t)
    result["distance_nm"]    = extract_distance(t)
    result["runway"]         = extract_runway(t)
    result["aircraft_type"]  = extract_aircraft_type(t)

    tl = t.lower()
    for word, bearing in BEARING_WORDS.items():
        if word in tl:
            result["bearing"] = bearing
            break

    score = 0.0
    if result["registration"]: score += 0.35
    elif result["callsign"]:   score += 0.20
    if result["intent"] != "unknown": score += 0.20
    if result["distance_nm"] is not None: score += 0.15
    if result["runway"]:   score += 0.10
    if result["aircraft_type"]: score += 0.05
    if result["bearing"]:  score += 0.05
    if result["callsign"]:
        p3, p2 = result["callsign"][:3], result["callsign"][:2]
        if p3 in KNOWN_CALLSIGN_PREFIXES or p2 in KNOWN_CALLSIGN_PREFIXES:
            score += 0.10

    result["confidence"] = min(round(score, 3), 1.0)
    return result

# ── ADS-B cross-reference ─────────────────────────────────────────────────────
def fetch_adsb_aircraft() -> list:
    try:
        resp = requests.get(DUMP1090_URL, timeout=3)
        return resp.json().get("aircraft", [])
    except Exception:
        return []

def adsb_crossref(parsed: dict) -> dict:
    parsed["adsb_confirmed"] = False
    aircraft = fetch_adsb_aircraft()
    if not aircraft:
        return parsed
    reg      = parsed.get("registration", "")
    callsign = parsed.get("callsign", "")
    for ac in aircraft:
        ac_flight = (ac.get("flight") or "").strip().upper()
        ac_reg    = (ac.get("registration") or "").strip().upper()
        if (reg and ac_reg == reg.replace("-","").upper()) or \
           (callsign and ac_flight.startswith(callsign[:4].upper())):
            parsed["adsb_confirmed"] = True
            parsed["confidence"] = min(parsed["confidence"] + 0.25, 1.0)
            if ac.get("alt_baro") is not None:
                parsed["adsb_altitude_ft"] = ac["alt_baro"]
            if ac.get("gs") is not None:
                parsed["adsb_groundspeed_kts"] = int(ac["gs"])
            break
    return parsed

# ── Worker POST ───────────────────────────────────────────────────────────────
def post_to_worker(parsed: dict, timestamp_utc: str) -> bool:
    if not WORKER_SECRET:
        log.warning("WORKER_SECRET not set — skipping POST. Set it in /etc/cyrl-pi/config")
        return False
    try:
        resp = requests.post(
            f"{WORKER_URL}/radio-report",
            json={**parsed, "ts": timestamp_utc, "source": "cyrl-bazzite"},
            headers={"Content-Type": "application/json", "X-Radio-Secret": WORKER_SECRET},
            timeout=10,
        )
        if resp.status_code == 200:
            log.info(
                f"✓ POST OK — {parsed.get('registration') or parsed.get('callsign')} "
                f"intent={parsed['intent']} conf={parsed['confidence']:.2f}"
            )
            return True
        log.warning(f"POST failed: HTTP {resp.status_code} — {resp.text[:200]}")
    except Exception as e:
        log.warning(f"POST error: {e}")
    return False

def log_transmission(parsed: dict, ts: str):
    try:
        log_file = LOG_DIR / "transmissions.jsonl"
        with log_file.open("a") as f:
            f.write(json.dumps({**parsed, "ts": ts}) + "\n")
    except Exception as e:
        log.warning(f"Log write failed: {e}")

# ── Whisper transcription ─────────────────────────────────────────────────────
WHISPER_PROMPT = (
    "Kenora radio, this is aircraft C-FXYZ, Piper Navajo, "
    "10 miles back from the field, inbound runway 08, "
    "Cessna Caravan, King Air, Twin Otter, Dash 8, "
    "Perimeter BLS, Wasaya WSG, North Star NSA, "
    "Superior SUP, Skycare PHX, ORNGE, medevac, "
    "landing, departing, circuit, final, downwind, "
    "Red Lake, CYRL, Kenora, 122.300"
)

def transcribe_wav(wav_path: str) -> str:
    try:
        result = _whisper_model.transcribe(
            wav_path, language="en",
            initial_prompt=WHISPER_PROMPT,
            temperature=0.0,
            condition_on_previous_text=False,
            fp16=False,
        )
        return result.get("text", "").strip()
    except Exception as e:
        log.error(f"Whisper error: {e}")
        return ""

# ── VAD + recording loop ──────────────────────────────────────────────────────
# RMS fallback threshold — tuned for laptop soundcards (slightly higher noise floor)
ENERGY_THRESHOLD = 600

def rms_energy(chunk: np.ndarray) -> float:
    return float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))

def record_transmission() -> Optional[np.ndarray]:
    CHUNK_FRAMES  = int(SAMPLE_RATE * 0.1)   # 100 ms
    PRE_BUFFER_N  = 8                          # ~800ms pre-buffer (laptop mic has more latency)
    MAX_SECS      = 60
    silence_limit = int(SILENCE_SECS / 0.1)

    pre_buffer = []
    speech_buf = []
    in_speech  = False
    silence_cnt = 0

    log.debug("Listening for transmission…")

    # PipeWire-friendly: use 'default' if device is PipeWire virtual node
    device = AUDIO_DEVICE_IDX

    with sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="int16",
        device=device, blocksize=CHUNK_FRAMES,
        latency="low",   # PipeWire handles this gracefully
    ) as stream:
        while True:
            chunk, _ = stream.read(CHUNK_FRAMES)
            chunk = chunk.flatten()

            if VAD_AVAILABLE:
                tensor = torch.FloatTensor(chunk.astype(np.float32) / 32768.0)
                is_speech = vad_model(tensor, SAMPLE_RATE).item() >= VAD_THRESHOLD
            else:
                is_speech = rms_energy(chunk) >= ENERGY_THRESHOLD

            if not in_speech:
                pre_buffer.append(chunk)
                if len(pre_buffer) > PRE_BUFFER_N:
                    pre_buffer.pop(0)
                if is_speech:
                    in_speech = True
                    speech_buf = list(pre_buffer) + [chunk]
                    log.debug("Transmission start")
            else:
                speech_buf.append(chunk)
                if not is_speech:
                    silence_cnt += 1
                    if silence_cnt >= silence_limit:
                        break
                else:
                    silence_cnt = 0
                if len(speech_buf) * 0.1 >= MAX_SECS:
                    log.warning("Max clip duration reached — truncating")
                    break

    if not speech_buf:
        return None

    audio = np.concatenate(speech_buf)
    duration = len(audio) / SAMPLE_RATE

    if duration < MIN_SPEECH_SECS:
        log.debug(f"Clip too short ({duration:.2f}s) — skipping")
        return None

    log.info(f"Recorded {duration:.1f}s clip")
    return audio

def process_clip(audio: np.ndarray):
    ts_utc = datetime.now(timezone.utc).isoformat()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav_path = f.name
    try:
        wav_io.write(wav_path, SAMPLE_RATE, audio)
        transcript = transcribe_wav(wav_path)
        if not transcript:
            log.info("Empty transcript — skipping")
            return
        log.info(f"Transcript: {transcript!r}")
        parsed = parse_transmission(transcript)
        parsed = adsb_crossref(parsed)
        log.info(
            f"Parsed — reg={parsed['registration']} cs={parsed['callsign']} "
            f"intent={parsed['intent']} dist={parsed['distance_nm']}nm "
            f"rwy={parsed['runway']} conf={parsed['confidence']:.2f} "
            f"adsb={parsed['adsb_confirmed']}"
        )
        log_transmission(parsed, ts_utc)
        if parsed["confidence"] >= 0.3:
            post_to_worker(parsed, ts_utc)
        else:
            log.info(f"Confidence {parsed['confidence']:.2f} < 0.30 — not posting")
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass

# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    # List available devices on startup
    try:
        log.info("Available audio input devices:")
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                default = " ← SELECTED" if i == AUDIO_DEVICE_IDX else ""
                log.info(f"  [{i}] {d['name']}{default}")
    except Exception as e:
        log.warning(f"Could not enumerate audio devices: {e}")

    if not WORKER_SECRET:
        log.warning("="*60)
        log.warning("WORKER_SECRET is not set in /etc/cyrl-pi/config")
        log.warning("Transmissions will be logged locally but NOT sent to the board.")
        log.warning("Set WORKER_SECRET and restart to enable board integration.")
        log.warning("="*60)

    log.info(
        f"\n{'='*55}\n"
        f"  CYRL Radio Listener — BazziteOS\n"
        f"  Audio device : {AUDIO_DEVICE_IDX}\n"
        f"  Sample rate  : {SAMPLE_RATE} Hz\n"
        f"  Frequency    : {CFG['FREQUENCY']} MHz\n"
        f"  Whisper model: {WHISPER_MODEL_NAME}\n"
        f"  VAD backend  : {'Silero' if VAD_AVAILABLE else 'RMS energy fallback'}\n"
        f"  VAD threshold: {VAD_THRESHOLD}\n"
        f"  Worker       : {WORKER_URL}\n"
        f"  Log dir      : {LOG_DIR}\n"
        f"{'='*55}"
    )

    consecutive_errors = 0

    while True:
        try:
            audio = record_transmission()
            if audio is not None:
                t = threading.Thread(target=process_clip, args=(audio,), daemon=True)
                t.start()
            consecutive_errors = 0

        except KeyboardInterrupt:
            log.info("Interrupted — shutting down")
            break
        except sd.PortAudioError as e:
            consecutive_errors += 1
            log.error(f"Audio device error ({consecutive_errors}): {e}")
            if "PipeWire" in str(e) or "ALSA" in str(e):
                log.warning("PipeWire/ALSA error — try: systemctl --user restart pipewire pipewire-pulse")
            if consecutive_errors >= 5:
                log.error("Too many errors — sleeping 60s")
                time.sleep(60)
                consecutive_errors = 0
            else:
                time.sleep(5)
        except Exception as e:
            consecutive_errors += 1
            log.error(f"Unexpected error: {e}", exc_info=True)
            time.sleep(5)

if __name__ == "__main__":
    main()
