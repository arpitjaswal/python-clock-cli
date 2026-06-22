"""Alarm audio: synthesize a tone with the stdlib, play via a system player.

Design goals:
  * No third-party deps — the tone is generated with the `wave` module.
  * Best-effort playback — detect any common CLI player; if none exists (or
    playback fails, e.g. no audio stack under WSL), fall back to the terminal
    bell. Audio must NEVER crash the UI.
  * User-customizable — a `sound_file` in config takes precedence over the
    generated tone, so the user can drop in their own .wav/.mp3.
"""
from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import threading
import wave

# Players we know how to drive, best-first. Each value is the base argv; the
# sound path is appended by build_play_command.
_PLAYERS = ("paplay", "aplay", "ffplay", "afplay", "cvlc", "play")

_FRAMERATE = 22050


def build_play_command(player: str, path: str) -> list[str]:
    """Pure: map a player name + sound path to a full argv. Unit-tested.

    ffplay/cvlc need flags to play once and exit without a GUI; the rest just
    take the filename.
    """
    if player == "ffplay":
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path]
    if player == "cvlc":
        return ["cvlc", "--play-and-exit", "--intf", "dummy", path]
    return [player, path]


def detect_player() -> str | None:
    """Return the first available player's name, or None if none is on PATH."""
    for p in _PLAYERS:
        if shutil.which(p):
            return p
    return None


def ensure_tone(path: str = "tone.wav") -> str:
    """Generate a short two-pitch alarm tone WAV if `path` doesn't exist.

    16-bit mono. Alternates between two frequencies in short bursts so it reads
    as an 'alarm' rather than a flat beep. Idempotent — generated once, reused.
    """
    if os.path.exists(path):
        return path
    duration = 0.9
    burst = 0.09           # seconds per pitch burst
    freqs = (880.0, 1320.0)
    amp = 0.45 * 32767
    n = int(_FRAMERATE * duration)
    frames = bytearray()
    for i in range(n):
        t = i / _FRAMERATE
        freq = freqs[int(t / burst) % len(freqs)]
        # gentle fade in/out to avoid clicks
        env = min(1.0, t / 0.02, (duration - t) / 0.05)
        sample = int(amp * env * math.sin(2 * math.pi * freq * t))
        frames += struct.pack("<h", max(-32768, min(32767, sample)))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_FRAMERATE)
        w.writeframes(bytes(frames))
    return path


def resolve_sound(cfg: dict, tone_path: str = "tone.wav") -> str:
    """Pick the sound to play: a configured (existing) file wins, else the tone."""
    sf = cfg.get("sound_file")
    if sf and os.path.exists(sf):
        return sf
    return ensure_tone(tone_path)


class Siren:
    """Loops alarm audio in a background thread until stopped.

    Falls back to a `beep` callback (terminal bell) if no player is available or
    a play attempt errors. Never raises into the caller.
    """

    def __init__(self, cfg: dict, beep=None):
        self.cfg = cfg
        self._beep = beep or (lambda: print("\a", end="", flush=True))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None

    def _command(self, path: str) -> list[str] | None:
        explicit = self.cfg.get("sound_cmd")
        if explicit:
            # e.g. "mpv --no-video" -> ["mpv","--no-video", path]
            return explicit.split() + [path]
        player = detect_player()
        return build_play_command(player, path) if player else None

    def _loop(self) -> None:
        try:
            path = resolve_sound(self.cfg)
        except OSError:
            path = None
        cmd = self._command(path) if path else None
        while not self._stop.is_set():
            played = False
            if cmd:
                try:
                    self._proc = subprocess.Popen(
                        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    self._proc.wait()
                    played = self._proc.returncode == 0
                except (OSError, ValueError):
                    played = False
                finally:
                    self._proc = None
            if not played:
                # no player or playback failed → bell, then pace the loop
                try:
                    self._beep()
                except Exception:
                    pass
                self._stop.wait(1.0)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc is not None:
            try:
                self._proc.terminate()
            except (OSError, ValueError):
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
