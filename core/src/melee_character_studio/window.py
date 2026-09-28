"""Show a local web UI in a window of its own (the same helper PascalPatch's app uses).

Order of preference: pywebview when it is installed (a native window), then Microsoft Edge or
Chrome in app mode (no tabs or address bar, a private profile under the data folder), then the
default browser. Returns when the window closes (the browser fallback returns at once and the
caller keeps serving until Ctrl+C).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path

APP_BROWSERS = [
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
]


def _app_browser():
    for p in APP_BROWSERS:
        path = Path(os.path.expandvars(p))
        if path.is_file():
            return str(path)
    return shutil.which("msedge") or shutil.which("chrome") or shutil.which("chromium")


def open_window(url, title, data_dir, size=(1320, 860)):
    try:
        import webview   # optional: pip install pywebview
        webview.create_window(title, url, width=size[0], height=size[1], min_size=(900, 600), background_color="#070B1C")
        webview.start()
        return
    except ImportError:
        pass
    exe = _app_browser()
    if exe:
        profile = Path(data_dir) / "window-profile" / title.lower().replace(" ", "-")
        profile.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", f"--window-size={size[0]},{size[1]}",
                                 "--no-first-run", "--no-default-browser-check", "--disable-extensions"])
        proc.wait()
        # msedge.exe is only a launcher: it hands the window to a browser process and exits at once, so
        # wait on the profile instead. Chromium keeps <profile>/lockfile open while any window is up.
        _wait_for_profile(profile)
        return
    webbrowser.open(url)
    _serve_until_interrupted()


def _profile_in_use(profile):
    lock = Path(profile) / "lockfile"
    if not lock.exists():
        return False
    try:
        os.close(os.open(lock, os.O_RDWR))   # a stale lock left by a crash opens fine
        return False
    except OSError:
        return True


def _wait_for_profile(profile, start_timeout=30.0):
    """Return once the browser holding ``profile`` has closed its last window."""
    deadline = time.monotonic() + start_timeout
    while not _profile_in_use(profile):
        if time.monotonic() > deadline:   # never saw the browser start: keep serving until Ctrl+C
            _serve_until_interrupted()
            return
        time.sleep(0.25)
    try:
        while _profile_in_use(profile):
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def _serve_until_interrupted():
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
