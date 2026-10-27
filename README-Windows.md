# CYRL Radio Listener — Windows Setup (CMD / PowerShell)

This describes how to run the same ATC voice-to-board pipeline on Windows. The Python listener code and Cloudflare Worker upload are unchanged; only the installation method and background-service approach differ.

**Windows cannot run `install.sh` or `bazzite-setup.sh`.** Those install Linux packages and a systemd user service. You install the prerequisites manually and run the listener directly, or as a scheduled task.

---

## Prerequisites

1. **Python 3.10 or newer** — download the installer from [python.org](https://www.python.org/downloads/windows/) and check "Add Python to PATH" during installation.
2. **Git for Windows** (optional) — only needed for the `git clone` method. Download from [gitforwindows.org](https://gitforwindows.org/) or use `winget install --id Git.Git -e`. If you don't want to install it, use the ZIP download in "Option B" below.
3. **FFmpeg** — Whisper calls `ffmpeg` as a command when loading audio, so it must be on your `PATH`. See "Adding FFmpeg to PATH" below.
4. A USB audio adapter or sound card with a line-in/microphone jack, plus your scanner on 122.300 MHz.

## Get the files

**Option A — with Git (preferred):**

```bat
git clone https://github.com/leblancaaron250-prog/cyrl-radio-bazzite.git
cd cyrl-radio-bazzite
```

If you see `'git' is not recognized...`, either install Git or use Option B:

```bat
winget install --id Git.Git -e
```

Then **close and reopen Command Prompt** so the new PATH takes effect, and retry the clone.

**Option B — without Git (no installation needed):**

Download the repository as a ZIP straight from GitHub. Either click this link — [Download cyrl-radio-bazzite.zip](https://github.com/leblancaaron250-prog/cyrl-radio-bazzite/archive/refs/heads/main.zip) — or paste in Command Prompt / PowerShell:

```bat
curl -L -o cyrl-radio-bazzite.zip https://github.com/leblancaaron250-prog/cyrl-radio-bazzite/archive/refs/heads/main.zip
tar -xf cyrl-radio-bazzite.zip
cd cyrl-radio-bazzite-main
```

(`curl` and `tar` are built into Windows 10/11. The folder is named `cyrl-radio-bazzite-main` because the ZIP unpacks with the branch name appended.)

## Installation (Command Prompt)

Open **Command Prompt** (run as your normal user, no administrator required) and paste:

```bat
python -m pip install --upgrade pip

REM PyTorch CPU build (required before other packages)
python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu

REM Remaining packages
python -m pip install openai-whisper sounddevice scipy requests numpy
```

PowerShell is equivalent; just replace `REM` with `#`.

> **Note:** Option B users skip `git` entirely. The only downside is pulling updates later requires re-downloading the ZIP.

## Adding FFmpeg to PATH

**Easiest — winget (adds itself to PATH automatically):**

```bat
winget install --id Gyan.FFmpeg -e
```

Close and reopen Command Prompt, then verify:

```bat
ffmpeg -version
```

`winget` places a link in `%LOCALAPPDATA%\Microsoft\WinGet\Links`, which is already on your PATH — no manual editing needed.

**Manual — downloaded FFmpeg build (e.g. from ffmpeg.org):**

1. Extract the ZIP somewhere permanent, e.g. `C:\ffmpeg`
2. Press **Win**, type `env`, open **"Edit the system environment variables"**
3. Click **Environment Variables...**
4. Under **User variables for \<you\>** (top box), select **Path** → **Edit...**
5. **New** → enter `C:\ffmpeg\bin` → **OK** on all dialogs
6. **Close and reopen Command Prompt** — existing windows never see PATH changes
7. Verify with `ffmpeg -version`

> Add the folder containing `ffmpeg.exe` (the `bin` folder), not the top-level folder.

**Quick test without editing PATH (current window only):**

```bat
set PATH=%PATH%;C:\ffmpeg\bin
ffmpeg -version
```

This is temporary — it vanishes when the window closes. Useful to confirm the wiring works before making it permanent.

> **Note on pip:** Python package installs (`pip install torch ...`) never add directories to PATH. They don't need to — Python, not the shell, finds installed packages. Only command-line tools like `ffmpeg` (and `git`, `python`) need PATH entries.

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

> **Note:** `radio-listener.py` auto-detects the platform. On Windows it reads `config.ini` from the folder containing the script, and writes logs to `%LOCALAPPDATA%\cyrl-radio\logs`. On Linux it still reads `/etc/cyrl-pi/config`.

## List audio devices

```bat
python list-devices.py
```

This prints every device with its host API (MME, DirectSound, WASAPI, WDM-KS) and channel counts. Find your USB adapter/line-in and set `AUDIO_DEVICE` to that index.

**Which entry to pick on Windows:**

- Pick an `IN` entry (has input channels) for your adapter
- Prefer the **WASAPI** version, then DirectSound, then MME
- **Avoid `Windows WDM-KS` entries** — they cannot be read in blocking mode (PaErrorCode -9999). If you set one, the listener tries to auto-switch to the same device under WASAPI/MME, but picking the right index up front is cleaner

## Find the right input jack (scanner "record out" -> 3.5mm)

1. Plug the scanner's record-out cable into an input jack (mic jack, or a blue Line In if present). If Windows/Realtek pops up "Which device did you plug in?", choose **Line In** (preferred) or **Microphone**.
2. Run `python list-devices.py` — compare against the list before plugging in. A new `IN` entry (or one that changed) is your jack.
3. Run the level meter against each candidate index:
   ```bat
   python level-meter.py 0
   ```
   Key the scanner or open its squelch — the bar should jump on the right device. Try the "Microsoft Sound Mapper - Input" (MME, index 0) too — it follows the default recording device.
4. Set `AUDIO_DEVICE` in `config.ini` to the working index (prefer WASAPI entries; WDM-KS ones are auto-switched by the listener).

Notes:
- On machines where the mic input only appears under WDM-KS, set that jack as the default recording device (mmsys.cpl → Recording) and use the MME "Microsoft Sound Mapper - Input" entry — it maps to whatever the default input is.
- Keep the scanner volume moderate — the record-out jack is usually a fixed level, so test with the meter before running Whisper on it.

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
- `No module named 'packaging'` when loading Silero VAD: run `python -m pip install packaging` — without it the listener falls back to crude RMS-energy detection, which is much noisier.
- `PaErrorCode -9998 Invalid number of channels`: your `AUDIO_DEVICE` index points at a device that is not a usable input (often an output device). Run `python list-devices.py` and pick an `IN` entry. The listener now falls back to the system default input and warns loudly when this happens.
- `PaErrorCode -9999 / WDM-KS 'Blocking API not supported yet'`: you picked a WDM-KS entry — see "Which entry to pick" above.
- **No usable inputs at all** (e.g. `python list-devices.py` shows `Default input device: -1`, and inputs exist only under WDM-KS): your microphone is disabled in Windows. Press **Win+R**, run `mmsys.cpl` → **Recording** tab → right-click → **Show Disabled Devices** → right-click your microphone → **Enable** and **Set as Default Device**. Re-run `python list-devices.py` — MME/DirectSound/WASAPI mic entries should now appear; set `AUDIO_DEVICE` to the WASAPI one. Also check Settings → Privacy & security → Microphone → allow desktop apps if enabling doesn't do it.
- ZIP-install users (Option B) update by re-downloading the ZIP and replacing `radio-listener.py` — your `config.ini` and logs are unaffected.

## Verified against

- Windows 10/11 with Python 3.10+ and the CPU-only PyTorch wheel.
