"""Ephemeral salary-region OCR. No network, files, credentials, AI fees or DB writes."""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import struct
import subprocess
import threading
import unicodedata
from pathlib import Path

from app.core.paths import PROJECT_ROOT

_LOCK = threading.Lock()
MAX_IMAGE_BYTES = 262144


def parse_salary(text: str) -> str | None:
    """Strict whole-region parsing; never guess O/0, missing units or missing ranges."""
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
    text = text.replace("–", "-").replace("—", "-").replace("－", "-")
    match = re.fullmatch(r"(\d{1,3}(?:\.\d{1,2})?)-(\d{1,3}(?:\.\d{1,2})?)([Kk万])(?:[·•・](\d{2})薪)?", text)
    if not match:
        return None
    low, high, unit, months = match.groups()
    if not 0 < float(low) <= float(high) <= 300 or (months and not 12 <= int(months) <= 24):
        return None
    return f"{low}-{high}{unit.upper()}" + (f"·{months}薪" if months else "")


def validate_image(image: str) -> bytes:
    if len(image) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
        raise ValueError("image_too_large")
    try:
        raw = base64.b64decode(image, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid_png") from exc
    if len(raw) < 33 or len(raw) > MAX_IMAGE_BYTES or raw[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
        raise ValueError("invalid_png")
    width, height = struct.unpack(">II", raw[16:24])
    if not (12 <= width <= 800 and 8 <= height <= 160 and width * height <= 100000):
        raise ValueError("invalid_crop_dimensions")
    return raw


def recognize_salary(image: str) -> dict:
    validate_image(image)
    unknown = {"salary_text": None, "source": "windows_local_ocr", "reason": "unavailable"}
    if os.name != "nt":
        return unknown
    if not _LOCK.acquire(blocking=False):
        return {**unknown, "reason": "busy"}
    try:
        # Explicit Windows PowerShell (not pwsh: WinRT projection needs .NET Framework).
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        completed = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
             str(PROJECT_ROOT / "scripts/ocr/recognize-salary.ps1")],
            input=json.dumps({"image": image}), encoding="utf-8", errors="replace", capture_output=True,
            timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if completed.returncode != 0 or len(completed.stdout) > 4096:
            return unknown
        texts = json.loads(completed.stdout).get("texts", [])
        if not isinstance(texts, list) or len(texts) != 2 or not all(isinstance(t, str) for t in texts):
            return unknown
        salaries = [parse_salary(t) for t in texts]
        # Agreement is an extra guard, not a fabricated confidence score.
        if not salaries[0] or salaries[0] != salaries[1]:
            return {**unknown, "reason": "uncertain"}
        return {"salary_text": salaries[0], "source": "windows_local_ocr", "reason": "two_scale_agreement"}
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, AttributeError):
        return unknown
    finally:
        _LOCK.release()
