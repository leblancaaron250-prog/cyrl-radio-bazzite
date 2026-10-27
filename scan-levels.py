#!/usr/bin/env python3
"""Watch audio levels on ALL input devices at once.

Finds which jack/device carries your scanner audio without any manual
trial and error. Each unique physical input appears as one row (the same
jack is normally listed once per host API - MME, DirectSound, WASAPI,
WDM-KS - this script deduplicates and prefers the most reliable API).

Usage:
  python scan-levels.py           # run until Ctrl+C
  python scan-levels.py 30        # run for 30 seconds

Key the scanner or open its squelch — the row whose bar jumps is your jack.
Set AUDIO_DEVICE in config.ini to that row's index.
"""
import sys
import time
import numpy as np
import sounddevice as sd

API_RANK = {
    "Windows WASAPI": 0,
    "Windows DirectSound": 1,
    "MME": 2,
}


def hostapi_name(dev: dict) -> str:
    try:
        return sd.query_hostapis(dev["hostapi"])["name"]
    except Exception:
        return "?"


def main() -> None:
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else None

    inputs = [(i, d) for i, d in enumerate(sd.query_devices())
              if d["max_input_channels"] > 0]
    if not inputs:
        print("No input devices found.")
        return

    # Group by physical device name; try the best host API first
    by_name = {}
    for i, d in inputs:
        by_name.setdefault(d["name"], []).append(i)

    levels = {}
    names = {}
    watched = []
    failed = []
    streams = []

    def make_cb(idx):
        def cb(indata, frames, time_info, status):
            mono = indata[:, 0] if indata.shape[1] == 1 else indata.mean(axis=1)
            rms = float(np.sqrt(np.mean(mono.astype(np.float32) ** 2)))
            if rms > levels[idx]:
                levels[idx] = rms
        return cb

    for name, idxs in by_name.items():
        idxs.sort(key=lambda i: API_RANK.get(hostapi_name(sd.query_devices(i)), 9))
        for i in idxs:
            opened = False
            last_err = None
            for ch in (1, 2):
                try:
                    s = sd.InputStream(
                        samplerate=16000, channels=ch, dtype="int16",
                        device=i, callback=make_cb(i), blocksize=1600,
                    )
                    s.start()
                    streams.append(s)
                    levels[i] = 0.0
                    names[i] = f"{name} ({hostapi_name(sd.query_devices(i))})"
                    watched.append(i)
                    opened = True
                    break
                except Exception as e:
                    last_err = e
            if opened:
                break
        else:
            if idxs:
                failed.append((idxs[0], name, str(last_err)))

    print(f"Watching {len(watched)} unique input device(s):")
    for i in watched:
        print(f"  [{i:>2}] {names[i]}")
    if failed:
        print("Could not open (skipped):")
        for i, name, err in failed:
            print(f"  [{i:>2}] {name}: {err}")
    print("\nKey the scanner / open the squelch — the row whose bar jumps is your jack.")
    print("Set AUDIO_DEVICE in config.ini to that index. Ctrl+C to stop.\n")

    n_rows = len(watched)
    first = True
    t0 = time.time()
    try:
        while duration is None or (time.time() - t0) < duration:
            time.sleep(0.3)
            rows = []
            for i in watched:
                rms = levels[i]
                levels[i] = 0.0
                bar = "#" * int(min(rms / 327.68, 1.0) * 32)
                rows.append(f"[{i:>2}] {names[i]:<46.46s} {rms:7.0f} |{bar:<32s}|")
            block = "\n".join(rows)
            if first:
                print(block, end="", flush=True)
                first = False
            else:
                print("\033[" + str(n_rows) + "F" + block, end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        for s in streams:
            try:
                s.stop()
                s.close()
            except Exception:
                pass
    print()


if __name__ == "__main__":
    main()
