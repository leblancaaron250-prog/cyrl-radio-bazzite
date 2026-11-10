#!/usr/bin/env python3
"""Self-updater for the CYRL radio listener scripts.

Usage (from the folder containing radio-listener.py):
    python update.py

Downloads the latest versions of the scripts from GitHub, verifies each one
compiles, and only then replaces the local copy. Never touches config.ini,
logs, or anything else — only the scripts listed in FILES below.

Pure standard library — no pip installs needed.
"""
import os
import py_compile
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO   = "leblancaaron250-prog/cyrl-radio-bazzite"
BRANCH = "main"
FILES  = [
    "radio-listener.py",
    "list-devices.py",
    "level-meter.py",
    "scan-levels.py",
    "update.py",   # updates itself last
]
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "cyrl-radio-updater"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def main() -> None:
    here = Path(__file__).resolve().parent
    os.chdir(here)

    print(f"Checking for updates ({REPO} @ {BRANCH})...\n")

    updated, unchanged, failed = [], [], []
    for name in FILES:
        url = f"{RAW_BASE}/{name}"
        try:
            new_bytes = fetch(url)
        except Exception as e:
            failed.append((name, f"download failed: {e}"))
            continue

        local = here / name
        if local.exists() and local.read_bytes() == new_bytes:
            unchanged.append(name)
            continue

        # Verify the new file is valid Python BEFORE replacing anything
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".py", delete=False, dir=here
            ) as tmp:
                tmp.write(new_bytes)
                tmp_path = tmp.name
            try:
                py_compile.compile(tmp_path, cfile=tmp_path + ".c", doraise=True)
            finally:
                os.unlink(tmp_path)
                try:
                    os.unlink(tmp_path + ".c")
                except FileNotFoundError:
                    pass
        except Exception as e:
            failed.append((name, f"compile check failed: {e}"))
            continue

        try:
            local.write_bytes(new_bytes)
            updated.append(name)
        except Exception as e:
            failed.append((name, f"write failed: {e}"))

    for name in unchanged:
        print(f"  up to date  : {name}")
    for name in updated:
        print(f"  UPDATED     : {name}")
    for name, why in failed:
        print(f"  SKIPPED     : {name} ({why})")

    if updated:
        print("\nUpdates installed. Restart the listener to use them:")
        print("    python radio-listener.py")
    elif not failed:
        print("\nEverything is already up to date.")
    else:
        print("\nSome files could not be updated — local copies are untouched.")
        sys.exit(1)


if __name__ == "__main__":
    main()
