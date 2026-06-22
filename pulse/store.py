"""Persistence for alarms and config. Degrades gracefully on corruption."""
from __future__ import annotations

import json
import os
import tempfile

from .scheduler import Alarm

DEFAULT_CONFIG = {
    "time_format": "24h",        # "24h" or "12h"
    "bell": True,                # ring the terminal bell on fire
    "challenge": "math",         # "math" | "typing" | "sequence"
    "escalate_seconds": 30,      # difficulty step interval while ringing
    "poll_seconds": 1,           # clock refresh / due-check interval
    "sound_file": None,          # custom alarm sound path; falls back to a tone
    "sound_cmd": None,           # explicit player command override, e.g. "mpv --no-video"
    "flip_animation": True,      # card-flip transition when an alarm fires
}


def _atomic_write(path: str, text: str) -> None:
    """Write via temp file + rename so a crash mid-write can't corrupt state."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def load_alarms(path: str = "alarms.json") -> list[Alarm]:
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            raw = json.load(f)
        return [Alarm.from_dict(d) for d in raw]
    except (json.JSONDecodeError, TypeError, ValueError, KeyError) as e:
        print(f"⚠️  {path} unreadable ({e}); starting with no alarms.")
        return []


def save_alarms(alarms: list[Alarm], path: str = "alarms.json") -> None:
    _atomic_write(path, json.dumps([a.to_dict() for a in alarms], indent=2))


def load_config(path: str = "config.json") -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(path):
        try:
            with open(path) as f:
                cfg.update(json.load(f))
        except (json.JSONDecodeError, TypeError) as e:
            print(f"⚠️  {path} unreadable ({e}); using defaults.")
    return cfg
