"""Structured JSON-lines audit log — every alarm-lifecycle event.

Timestamps are local wall-clock ISO-8601 *with* offset, so a log read back
later is unambiguous even if the machine has since travelled. The offset is
recorded for forensics; it never feeds back into alarm matching (which stays
on bare wall-clock numbers).
"""
from __future__ import annotations

import json
from datetime import datetime


class Audit:
    def __init__(self, path: str = "pulse.log"):
        self.path = path

    def event(self, event: str, alarm_id: str = "", **meta) -> None:
        rec = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "event": event,
            "alarm_id": alarm_id,
            **meta,
        }
        try:
            with open(self.path, "a") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass  # logging must never take the clock down
