# CYRL Radio Listener — Windows Setup (CMD / PowerShell)

This describes how to run the same ATC voice-to-board pipeline on Windows. The Python listener code and Cloudflare Worker upload are unchanged; only the installation method and background-service approach differ.

**Windows cannot run `install.sh` or `bazzite-setup.sh`.** Those install Linux packages and a systemd user service. You install the prerequisites manually and run the listener directly, or as a scheduled task.

---

## Prerequisites

1. **Python 3.10 or newer** — download the installer from [python.org](https://www.python.org/downloads/windows/) and check "Add Python to PATH" during installation.
2. **Git for Windows** — download from [gitforwindows.org](https://gitforwindows.org/).
3. **FFmpeg** — download a Windows build from [ffmpeg.org/download.html](https://ffmpeg.org/download.html) or use `winget install Gyan.FFmpeg`, then add its `bin` folder to your `PATH`.
4. A USB audio adapter or sound card with a line-in/microphone jack, plus your scanner on 122.300 MHz.

## Installation (Command Prompt)

Open **Command Prompt** (run as your normal user, no administrator required) and paste:

```bat
git clone https://github.com/leblancaaron250-prog/cyrl-radio-bazzite.git
cd cyrl-radio-bazzite

python -m pip install --upgrade pip

REM PyTorch CPU build (required before other packages)
python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu

REM Remaining packages
python -m pip install openai-whisper sounddevice scipy requests numpy
```

PowerShell is equivalent; just replace `REM` with `#`.

## Configuration

Create a file named `config.ini` (or `.env`) in the repo folder with at least:

```ini
WORKER_URL=https://cyrl-fa-relay.cyrl-airport.workers.dev
WORKER_SECRET=REPLACE_ME
WHISPER_MODEL=small
AUDIO_DEVICE=1
FREQUENCY=122.300
VAD_THRESHOLD=0.35
SILENCE_SECONDS=1.5
MIN_SPEECH_SECONDS=0.8
DUMP1090_URL=http://127.0.0.1:8080/data/aircraft.json
```

Fill in `WORKER_SECRET` from Admin panel → Pi Feeder → Radio Listener Secret.

> **Note:** the current `radio-listener.py` reads `/etc/cyrl-pi/config` by default on Linux. On Windows it should read this `config.ini` in the working directory instead. If it does not yet do so, set the environment variables before running, or ask us to update the script to auto-detect Windows.

## List audio devices

```bat
python -c "import sounddevice as sd; print(sd.query_devices())"
```

Find your USB adapter/line-in in the list and set `AUDIO_DEVICE` to that index.

## Test audio input

`test-audio.sh` is a Linux shell script. On Windows, run the equivalent Python one-liner to record and play back a short clip:

```bat
python -c "import sounddevice as sd, numpy as np; d=sd.rec(int(5*44100), samplerate=44100, channels=1); sd.wait(); sd.play(d, samplerate=44100); sd.wait(); print('done')"
```

If you hear your scanner clearly, the wiring is correct.

## Run the listener

```bat
python radio-listener.py
```

Watch for `[VAD] Speech detected`, `[Whisper] "..."`, `[POST] → 200 OK` in the console.

## Run at startup / as a background service

Windows does not use systemd. Use **Task Scheduler**:

1. Open **Task Scheduler** → **Create Task**.
2. **General:** run as your user, "Run only when user is logged on" (needed for audio access).
3. **Triggers:** New → At log on.
4. **Actions:** New →
   - Program: `python`
   - Arguments: `radio-listener.py`
   - Start in: `C:\Users\%USERNAME%\cyrl-radio-bazzite` (adjust to your clone path).
5. **Settings:** allow restart on failure.

Or install as a Windows service using [NSSM](https://nssm.cc/) if it must run without a logged-in session. Audio-device access can be unreliable for session-0 services, so the Task Scheduler "logged on" option is more reliable for this use case.

## Known Windows differences

- `sounddevice` uses PortAudio, which ships with the `sounddevice` wheel on Windows, so no separate PortAudio build is required.
- `torch.hub` downloads to `%USERPROFILE%\.cache\torch\hub`; the Silero VAD and Whisper models download on first run, exactly as on Linux.
- Whisper's `fp16` warning on CPU appears on Windows too and is harmless.
- If the console closes immediately, run from an open Command Prompt so you can see any Python traceback.

## Verified against

- Windows 10/11 with Python 3.10+ and the CPU-only PyTorch wheel.
