# CYRL Radio Listener — BazziteOS Edition

ATC voice-to-board pipeline for Red Lake Airport (CYRL).  
Runs on a BazziteOS laptop (or any Fedora-based system) connected to a radio scanner via USB audio adapter.

**Status board:** [status.redlakeairport.ca](https://status.redlakeairport.ca)  
**Admin panel:** [admin.redlakeairport.ca](https://admin.redlakeairport.ca)

---

## One-Line Install

Open a terminal and paste:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/leblancaaron250-prog/cyrl-radio-bazzite/main/install.sh)
```

That's it. The installer will:

1. Install `git` if missing (via `rpm-ostree` or `dnf`)
2. Clone this repo to `~/cyrl-radio-bazzite`
3. Install all required system and Python packages
4. Pre-download the Whisper `small` model (~460 MB)
5. Install the `cyrl-radio` systemd user service
6. List your available audio input devices

> **Re-running is safe.** Pull updates and re-run any time — your config at `/etc/cyrl-pi/config` is never overwritten.

---

## After Install — 4 Steps

### 1. Set your secret key

Get the Radio Listener Secret from [Admin → Pi Feeder](https://admin.redlakeairport.ca), then:

```bash
sudo nano /etc/cyrl-pi/config
```

Set `WORKER_SECRET=your-secret-here` and save.

### 2. Set your audio device

The installer prints a list of audio input devices. Find your USB adapter or combo-jack input, note its index number, then set it in config:

```
AUDIO_DEVICE=1   # ← replace with your index
```

Not sure which one? Run the audio test:

```bash
bash ~/cyrl-radio-bazzite/test-audio.sh
```

### 3. Start the service

```bash
systemctl --user start cyrl-radio
```

Watch live logs:

```bash
journalctl --user -u cyrl-radio -f
```

### 4. Enable at startup

```bash
systemctl --user enable cyrl-radio
```

---

## Hardware

| Item | Notes |
|------|-------|
| Radio scanner | Programmed to **122.300 MHz** (Kenora FSC) |
| USB audio adapter | Any USB sound card with a 3.5mm input (e.g. Sabrent, StarTech) |
| Cable | 3.5mm mono or stereo audio cable, scanner headphone jack → adapter mic/line-in |

Run `test-audio.sh` to confirm the scanner audio is reaching the laptop before starting the service.

---

## What It Does

```
Scanner (122.300 MHz)
  → USB audio adapter
    → Silero VAD  (detects when someone is transmitting)
      → Whisper small  (transcribes the transmission)
        → NLP parser  (extracts callsign, registration, intent, runway, distance)
          → ADS-B cross-reference  (optional — confirms aircraft if dump1090 is running)
            → POST /radio-report → Cloudflare Worker
              → Status board picks it up within one refresh cycle
```

Recognized intents: `landing`, `inbound`, `on_final`, `position_report`, `departing`, `taxiing`

---

## Files

| File | Purpose |
|------|---------|
| `install.sh` | One-line bootstrap — clones repo and runs setup |
| `bazzite-setup.sh` | Full installer (system packages, pip, service, config) |
| `radio-listener.py` | The listener daemon |
| `test-audio.sh` | Quick audio cable / mic sanity check |

---

## Manual Install (alternative)

```bash
git clone https://github.com/leblancaaron250-prog/cyrl-radio-bazzite.git
cd cyrl-radio-bazzite
bash bazzite-setup.sh
```

---

## Updating

```bash
cd ~/cyrl-radio-bazzite
git pull
bash bazzite-setup.sh   # safe to re-run — skips already-installed items
systemctl --user restart cyrl-radio
```

Or just re-run the one-liner — it pulls the latest automatically before running setup.

---

## Config Reference

File: `/etc/cyrl-pi/config`

| Key | Default | Description |
|-----|---------|-------------|
| `WORKER_SECRET` | *(required)* | Shared secret — set matching value in Admin → Pi Feeder |
| `AUDIO_DEVICE` | `1` | sounddevice input index |
| `WHISPER_MODEL` | `small` | `base` (fast) / `small` (recommended) / `medium` (accurate) |
| `VAD_THRESHOLD` | `0.35` | 0.0 = most sensitive, 1.0 = least sensitive |
| `SILENCE_SECONDS` | `1.5` | Seconds of silence before clip is considered complete |
| `MIN_SPEECH_SECONDS` | `0.8` | Minimum clip length to transcribe |
| `FREQUENCY` | `122.300` | Monitored frequency (informational — displayed in logs) |
| `DUMP1090_URL` | `http://127.0.0.1:8080/data/aircraft.json` | Local ADS-B feed for cross-reference (optional) |

---

## Troubleshooting

**Service won't start:**  
```bash
journalctl --user -u cyrl-radio -n 50
```

**No audio being detected:**  
```bash
bash ~/cyrl-radio-bazzite/test-audio.sh
```
Check cable connection and `AUDIO_DEVICE` index in config.

**Packages failed on Bazzite (rpm-ostree):**  
```bash
sudo rpm-ostree install portaudio portaudio-devel ffmpeg python3-pip
# Then reboot and re-run the installer
```

**Permission denied on `/etc/cyrl-pi/config`:**  
```bash
sudo mkdir -p /etc/cyrl-pi && sudo chmod 755 /etc/cyrl-pi
```
