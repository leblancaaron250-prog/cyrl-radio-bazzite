#!/usr/bin/env python3
"""Live input level meter — shows which device/jack carries your scanner audio.

Usage:
  python level-meter.py           # system default input
  python level-meter.py 0        # device index 0
  python level-meter.py 0 30     # stop after 30 seconds

Run 'python list-devices.py' to see indices. While the meter runs, key the
scanner or open its squelch — the bar should jump with static/voice.
"""
import sys
import numpy as np
import sounddevice as sd

CHUNK = 1600  # 100 ms @ 16 kHz


def main() -> None:
    device = int(sys.argv[1]) if len(sys.argv) > 1 else None
    duration = float(sys.argv[2]) if len(sys.argv) > 2 else None

    channels = 1
    try:
        stream_ctx = sd.InputStream(
            samplerate=16000, channels=1, dtype="int16",
            device=device, blocksize=CHUNK,
        )
    except sd.PortAudioError:
        # Some Windows drivers refuse mono-only opens — retry stereo
        channels = 2
        stream_ctx = sd.InputStream(
            samplerate=16000, channels=2, dtype="int16",
            device=device, blocksize=CHUNK,
        )

    with stream_ctx as stream:
        if device is None:
            print(f"Metering system default input (Ctrl+C to stop)")
        else:
            print(f"Metering device {device} (Ctrl+C to stop)")
        print("Key the scanner / open the squelch and watch the bar jump.")
        t = 0.0
        while duration is None or t < duration:
            data, _ = stream.read(CHUNK)
            if channels == 2:
                data = data.astype(np.float32).mean(axis=1).astype(np.int16)
            data = data.flatten()
            rms = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))
            peak = float(np.max(np.abs(data))) / 32768.0
            bar = "#" * int(min(rms / 327.68, 1.0) * 40)
            print(f"\rrms {rms:7.0f}  peak {peak:5.2f} |{bar:<40}|", end="", flush=True)
            t += 0.1
        print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
