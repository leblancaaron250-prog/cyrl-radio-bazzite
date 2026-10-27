#!/usr/bin/env python3
"""List all PortAudio devices with host API and channel counts.

Run:  python list-devices.py

Use this to pick the AUDIO_DEVICE index for config.ini.
Pick an entry marked IN (has input channels). On Windows, avoid
entries under the 'Windows WDM-KS' host API — they cannot be read
in blocking mode (PaErrorCode -9999). Prefer the WASAPI version of
your device, then DirectSound, then MME.
"""
import sounddevice as sd


def hostapi_name(dev: dict) -> str:
    try:
        return sd.query_hostapis(dev["hostapi"])["name"]
    except Exception:
        return "?"


def main() -> None:
    print(f"Default input  device: {sd.default.device[0]}")
    print(f"Default output device: {sd.default.device[1]}")
    print()
    for i, d in enumerate(sd.query_devices()):
        role = "IN " if d["max_input_channels"] > 0 else "out"
        chans = f"in:{d['max_input_channels']:<3d} out:{d['max_output_channels']:<3d}"
        try:
            default = " <-- default input" if i == sd.default.device[0] else ""
        except Exception:
            default = ""
        print(f"[{i:>2}] ({role}) {d['name']:<45s} [{chans}] ({hostapi_name(d)}){default}")


if __name__ == "__main__":
    main()
