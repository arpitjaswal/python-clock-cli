"""Interactive curses TUI for Pulse — a clock-radio for the terminal.

Visual identity: a vacuum-fluorescent (VFD) clock-radio. The hero is a glowing
seven-segment time display inside a device bezel; lit segments glow teal, unlit
ones stay as faint ghosts, and the colon blinks once a second. When an alarm
fires the whole device flips to a pulsing amber alert.

curses gives non-blocking input (timeout) so the display keeps ticking while the
menu waits for a keypress — something blocking input() can't do. All drawing is
bounds-guarded so a small/resized terminal can't crash curses.

    Palette          ground #0B0E14 · text #C7D0DC
                     accent #3DE8C8 (VFD teal) · accent-2 #FFB454 (amber)
"""
from __future__ import annotations

import curses
import random
import time

from . import scheduler as sched
from . import segdisplay
from .audio import Siren
from .audit import Audit
from .challenge import make_challenge
from .scheduler import Alarm
from .store import load_alarms, load_config, save_alarms

# Color-pair ids
P_FRAME, P_BRAND, P_SEG_ON, P_SEG_OFF, P_DATE, P_SEL, P_ALERT, P_OK, P_KEY = range(1, 10)

# xterm-256 indices for the committed palette (used when COLORS >= 256).
_C256 = {
    P_FRAME: 30,    # dim teal bezel
    P_BRAND: 244,   # graphite label
    P_SEG_ON: 80,   # VFD teal glow  (#5fd7d7)
    P_SEG_OFF: 23,  # ghost teal     (#005f5f)
    P_DATE: 252,    # cool white
    P_SEL: 80,
    P_ALERT: 214,   # amber          (#ffaf00)
    P_OK: 114,      # soft green
    P_KEY: 80,
}
# 8-color fallback (fg, extra attr)
_C8 = {
    P_FRAME: (curses.COLOR_CYAN, curses.A_DIM),
    P_BRAND: (curses.COLOR_WHITE, curses.A_DIM),
    P_SEG_ON: (curses.COLOR_CYAN, curses.A_BOLD),
    P_SEG_OFF: (curses.COLOR_CYAN, curses.A_DIM),
    P_DATE: (curses.COLOR_WHITE, 0),
    P_SEL: (curses.COLOR_CYAN, curses.A_BOLD),
    P_ALERT: (curses.COLOR_YELLOW, curses.A_BOLD),
    P_OK: (curses.COLOR_GREEN, 0),
    P_KEY: (curses.COLOR_CYAN, curses.A_BOLD),
}
_EXTRA: dict[int, int] = {}  # per-pair extra attrs filled at init


# ------------------------------------------------------------------ helpers ---
def _pair(pid: int) -> int:
    return curses.color_pair(pid) | _EXTRA.get(pid, 0)


def _addstr(win, y, x, text, attr=0):
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x < 0 or x >= w:
        return
    # Clip to the row's remaining width (w - x). Writing the bottom-right cell
    # raises in curses, so only that corner is trimmed/caught — not every edge.
    avail = w - x
    if y == h - 1:
        avail -= 1
    if avail <= 0:
        return
    try:
        win.addstr(y, x, text[:avail], attr)
    except curses.error:
        pass


def _center_x(w, text) -> int:
    return max(0, (w - len(text)) // 2)


def _human_delta(td) -> str:
    s = int(td.total_seconds())
    if s < 0:
        return "now"
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d {h:02d}h"
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s"


def _fmt_time(now, fmt: str) -> str:
    return now.strftime("%I:%M:%S" if fmt == "12h" else "%H:%M:%S")


def _draw_bezel(scr, y0, x0, h, w, pid):
    attr = _pair(pid)
    top = "╭" + "─" * (w - 2) + "╮"
    bot = "╰" + "─" * (w - 2) + "╯"
    _addstr(scr, y0, x0, top, attr)
    _addstr(scr, y0 + h - 1, x0, bot, attr)
    for r in range(1, h - 1):
        _addstr(scr, y0 + r, x0, "│", attr)
        _addstr(scr, y0 + r, x0 + w - 1, "│", attr)


def _draw_seg(scr, y, cx, text, colon_on, on_pid, off_pid):
    """Draw the seven-segment display centered on column `cx`, top row `y`."""
    grid = segdisplay.render(text, colon_on)
    x0 = cx - segdisplay.width(text) // 2
    for r, row in enumerate(grid):
        x = x0
        for ch, lit in row:
            if ch != " " and lit:           # only the lit segments — no ghosts
                _addstr(scr, y + r, x, ch, _pair(on_pid) | curses.A_BOLD)
            x += 1


def _read_line(win, y, x, prompt, maxlen=40) -> str | None:
    """Minimal line editor inside curses. Returns text, or None on ESC."""
    buf = ""
    while True:
        _addstr(win, y, x, prompt, _pair(P_BRAND))
        _addstr(win, y, x + len(prompt), buf + " ", _pair(P_DATE) | curses.A_BOLD)
        win.clrtoeol()
        win.refresh()
        ch = win.getch()
        if ch == 27:
            return None
        if ch in (curses.KEY_ENTER, 10, 13):
            return buf.strip()
        if ch in (curses.KEY_BACKSPACE, 127, 8):
            buf = buf[:-1]
        elif 32 <= ch < 127 and len(buf) < maxlen:
            buf += chr(ch)


# -------------------------------------------------------------------- app -----
class PulseTUI:
    PANEL_W = 52

    def __init__(self, stdscr):
        self.scr = stdscr
        self.cfg = load_config()
        self.audit = Audit("pulse.log")
        self.alarms = load_alarms()
        self.targets: dict[str, object] = {}
        self.sel = 0
        self.message = ""
        self.prev_wall = sched.local_now()
        self.prev_mono = time.monotonic()
        for a in self.alarms:
            self.audit.event("armed", a.id, time=a.time, repeat=a.repeat)
            self.targets[a.id] = sched.next_fire(a, self.prev_wall)

    # --- alarm polling (shared with the old daemon's logic) ---------------
    def _poll(self):
        now, mono = sched.local_now(), time.monotonic()
        anomaly = sched.detect_clock_jump(self.prev_wall, self.prev_mono, now, mono)
        if anomaly:
            time.tzset()
            now = sched.local_now()
            off = time.localtime().tm_gmtoff
            self.audit.event("clock_jump", seconds=round(anomaly, 1),
                             new_offset_min=(off // 60) if off is not None else 0)
            for a in self.alarms:
                self.targets[a.id] = sched.next_fire(a, now)
        self.prev_wall, self.prev_mono = now, mono

        ringer = None
        for a in self.alarms:
            decision = sched.fire_decision(a, self.targets.get(a.id), now)
            if decision == "fire" and ringer is None:
                ringer = a
            elif decision == "missed":
                target = self.targets[a.id]
                self.audit.event("missed", a.id, target=str(target),
                                 reason="clock jumped past catch-up window")
                sched.mark_fired(a, target)
                self.targets[a.id] = sched.next_fire(a, now)
        return ringer

    def _rearm(self, a: Alarm):
        self.targets[a.id] = sched.next_fire(a, sched.local_now())

    # --- layout constants -------------------------------------------------
    CLOCK_TOP = 2                 # rows below the top border
    CLOCK_ROWS = segdisplay.ROWS  # 7

    def _body_h(self) -> int:
        # border + eyebrow + clock + date + divider + label + alarms
        # + gap + message + keybar + border
        return self.CLOCK_TOP + self.CLOCK_ROWS + 6 + max(1, len(self.alarms))

    def _too_small(self, h, w):
        need_h, need_w = self._body_h(), self.PANEL_W + 2
        if h < need_h or w < need_w:
            self.scr.erase()
            msg = f"terminal too small — need {need_w}×{need_h}, have {w}×{h}"
            _addstr(self.scr, max(0, h // 2), max(0, (w - len(msg)) // 2),
                    msg, _pair(P_ALERT) | curses.A_BOLD)
            self.scr.refresh()
            return True
        return False

    # --- MAIN screen ------------------------------------------------------
    def draw_main(self):
        scr = self.scr
        h, w = scr.getmaxyx()
        if self._too_small(h, w):
            return
        scr.erase()
        now = sched.local_now()
        pw = self.PANEL_W
        body_h = self._body_h()
        x0 = (w - pw) // 2            # centered panel — stable, never shifts
        cx = x0 + pw // 2
        y0 = (h - body_h) // 2

        _draw_bezel(scr, y0, x0, body_h, pw, P_FRAME)

        # eyebrow: brand + a live "power" dot
        _addstr(scr, y0 + 1, x0 + 3, "P U L S E", _pair(P_BRAND) | curses.A_BOLD)
        _addstr(scr, y0 + 1, x0 + 15, "clock · alarms", _pair(P_BRAND))
        _addstr(scr, y0 + 1, x0 + pw - 5, "●", _pair(P_OK))

        # hero seven-segment clock (fixed width → no horizontal shift)
        colon_on = now.microsecond < 500_000
        _draw_seg(scr, y0 + self.CLOCK_TOP, cx, _fmt_time(now, self.cfg["time_format"]),
                  colon_on, P_SEG_ON, P_SEG_OFF)

        y = y0 + self.CLOCK_TOP + self.CLOCK_ROWS   # first row below the clock
        date = now.strftime("%A · %d %b %Y").upper()
        _addstr(scr, y, x0 + _center_x(pw, date), date, _pair(P_DATE))
        _addstr(scr, y + 1, x0 + 2, "─" * (pw - 4), _pair(P_FRAME))
        _addstr(scr, y + 2, x0 + 3, "ALARMS", _pair(P_SEG_OFF) | curses.A_BOLD)

        alarms_top = y + 3
        if not self.alarms:
            _addstr(scr, alarms_top, x0 + 5, "nothing set — press  a  to add one",
                    _pair(P_BRAND))
        for i, a in enumerate(self.alarms):
            nf = sched.next_fire(a, now)
            when = a.date if a.date else a.repeat
            eta = _human_delta(nf - now) if nf else "past"
            mark = "›" if i == self.sel else " "
            dot = "○" if not a.enabled else "◆"
            row = f"{mark} {dot} {a.time}  {when:<10} {a.label[:14]:<14} {eta:>7}"
            if i == self.sel:
                attr = _pair(P_SEL) | curses.A_BOLD
            elif not a.enabled:
                attr = _pair(P_SEG_OFF)
            else:
                attr = _pair(P_DATE)
            _addstr(scr, alarms_top + i, x0 + 3, row, attr)

        # message line + footer keybar
        if self.message:
            _addstr(scr, y0 + body_h - 3, x0 + 3, self.message[: pw - 6], _pair(P_OK))
        self._keybar(scr, y0 + body_h - 2, x0 + 3,
                     [("a", "add"), ("d", "delete"), ("t", "toggle"),
                      ("↑↓", "move"), ("q", "quit")])
        scr.refresh()

    def _keybar(self, scr, y, x, items):
        for key, label in items:
            _addstr(scr, y, x, key, _pair(P_KEY) | curses.A_BOLD)
            x += len(key) + 1
            _addstr(scr, y, x, label, _pair(P_BRAND))
            x += len(label) + 3

    # --- ADD screen -------------------------------------------------------
    def screen_add(self):
        self.scr.nodelay(False)
        try:
            self.scr.erase()
            h, w = self.scr.getmaxyx()
            x0 = _center_x(w, " " * self.PANEL_W)
            _draw_bezel(self.scr, 2, x0, 13, self.PANEL_W, P_FRAME)
            _addstr(self.scr, 3, x0 + 3, "NEW ALARM", _pair(P_SEG_ON) | curses.A_BOLD)
            _addstr(self.scr, 3, x0 + self.PANEL_W - 16, "esc to cancel",
                    _pair(P_BRAND))
            curses.curs_set(1)
            curses.echo(False)
            t = _read_line(self.scr, 5, x0 + 3, "time   ")
            if t is None:
                return
            label = _read_line(self.scr, 6, x0 + 3, "label  ") or ""
            rep = _read_line(self.scr, 7, x0 + 3,
                             "repeat (once/daily/weekdays/weekends)  ") or "once"
            date = _read_line(self.scr, 8, x0 + 3, "date   YYYY-MM-DD (blank=none)  ") or None
            try:
                a = Alarm(time=t, label=label, repeat=rep, date=date)
            except ValueError as e:
                self.message = f"✘ {e}"
                return
            self.alarms.append(a)
            save_alarms(self.alarms)
            self.audit.event("created", a.id, time=a.time, repeat=a.repeat,
                             label=a.label, date=a.date)
            self._rearm(a)
            if sched.next_fire(a, sched.local_now()) is None:
                self.message = f"⚠ added, but {a.date or a.time} is in the past"
            else:
                self.message = f"✔ set {a.time} · {a.label or a.repeat}"
        finally:
            curses.curs_set(0)
            self.scr.nodelay(True)

    # --- DELETE / TOGGLE --------------------------------------------------
    def delete_selected(self):
        if not self.alarms:
            return
        a = self.alarms.pop(self.sel)
        self.targets.pop(a.id, None)
        self.sel = max(0, self.sel - 1)
        save_alarms(self.alarms)
        self.audit.event("deleted", a.id)
        self.message = f"✘ removed {a.time} · {a.label or a.repeat}"

    def toggle_selected(self):
        if not self.alarms:
            return
        a = self.alarms[self.sel]
        a.enabled = not a.enabled
        save_alarms(self.alarms)
        self.audit.event("enable" if a.enabled else "disable", a.id)
        self._rearm(a)
        self.message = f"{a.time} {'armed' if a.enabled else 'off'}"

    # --- flip transition --------------------------------------------------
    RING_H = 16   # height of the alarm/ring + flip panel

    def _panel_origin(self, body_h):
        """Centered (y0, x0, cx) for a panel of the given height."""
        h, w = self.scr.getmaxyx()
        x0 = (w - self.PANEL_W) // 2
        return max(0, (h - body_h) // 2), x0, x0 + self.PANEL_W // 2

    def _flip(self, color_a, color_b):
        """Card-flip: collapse the device to a sliver in color_a, reopen in
        color_b. A frame-based stand-in for a 3D flip in the terminal."""
        if not self.cfg.get("flip_animation", True):
            return
        y0, _, cx = self._panel_origin(self.RING_H)
        pw = self.PANEL_W
        frames = [(ww, color_a) for ww in range(pw, 4, -6)] + \
                 [(ww, color_b) for ww in range(4, pw + 1, 6)]
        for ww, color in frames:
            self.scr.erase()
            _draw_bezel(self.scr, y0, max(0, cx - ww // 2), self.RING_H, ww, color)
            self.scr.refresh()
            time.sleep(0.014)

    # --- RINGING ----------------------------------------------------------
    def screen_ring(self, alarm: Alarm):
        target = self.targets[alarm.id]
        self.audit.event("fired", alarm.id, target=str(target), label=alarm.label)
        self._flip(P_FRAME, P_ALERT)          # clock → alarm
        siren = Siren(self.cfg, beep=curses.beep)
        siren.start()
        self.scr.nodelay(False)
        rng = random.Random()
        started = time.monotonic()
        pulse = 0
        pw = self.PANEL_W
        try:
            while True:
                elapsed = time.monotonic() - started
                level = int(elapsed // max(self.cfg["escalate_seconds"], 1))
                ch = make_challenge(self.cfg["challenge"], rng, level)
                pulse ^= 1
                self.scr.erase()
                h, w = self.scr.getmaxyx()
                y0, x0, cx = self._panel_origin(self.RING_H)
                compact = h < self.RING_H or w < pw + 2
                banner = "▲  W A K E   U P  ▲"
                if compact:
                    # too small for the big display — text-only overlay
                    _addstr(self.scr, 0, 0, banner, _pair(P_ALERT) | curses.A_BOLD)
                    _addstr(self.scr, 1, 0,
                            f"{alarm.label or 'alarm'} · {int(elapsed)}s · L{level}",
                            _pair(P_BRAND))
                    _addstr(self.scr, 2, 0, f"solve: {ch.prompt}",
                            _pair(P_DATE) | curses.A_BOLD)
                    ay, ax = 3, 0
                else:
                    _draw_bezel(self.scr, y0, x0, self.RING_H, pw, P_ALERT)
                    _addstr(self.scr, y0 + 1, x0 + _center_x(pw, banner), banner,
                            _pair(P_ALERT) | (curses.A_BOLD if pulse else curses.A_DIM))
                    _draw_seg(self.scr, y0 + 2, cx,
                              _fmt_time(sched.local_now(), self.cfg["time_format"]),
                              pulse, P_ALERT, P_SEG_OFF)
                    clk_bottom = y0 + 2 + segdisplay.ROWS
                    meta = f"{alarm.label or 'alarm'} · ringing {int(elapsed)}s · L{level}"
                    _addstr(self.scr, clk_bottom, x0 + _center_x(pw, meta), meta,
                            _pair(P_BRAND))
                    _addstr(self.scr, clk_bottom + 1, x0 + 2, "─" * (pw - 4),
                            _pair(P_ALERT))
                    prompt = f"solve to dismiss:  {ch.prompt}"
                    _addstr(self.scr, clk_bottom + 2, x0 + _center_x(pw, prompt),
                            prompt, _pair(P_DATE) | curses.A_BOLD)
                    ay, ax = clk_bottom + 4, x0 + 3
                curses.curs_set(1)
                given = _read_line(self.scr, ay, ax, "answer  ")
                if given is None:
                    continue  # ESC won't dismiss
                if ch.check(given):
                    self.audit.event("dismissed", alarm.id, level=level,
                                     ringing_s=int(elapsed))
                    self.message = "✔ dismissed — good morning"
                    break
                self.audit.event("challenge_failed", alarm.id, level=level)
        finally:
            siren.stop()
            curses.curs_set(0)
            self.scr.nodelay(True)
        self._flip(P_ALERT, P_FRAME)          # alarm → clock
        sched.mark_fired(alarm, target)
        save_alarms(self.alarms)
        self._rearm(alarm)

    # --- main loop --------------------------------------------------------
    def run(self):
        curses.curs_set(0)
        self.scr.nodelay(True)
        self.scr.timeout(120)  # ~8 fps so the colon blink reads smoothly
        while True:
            ringer = self._poll()
            if ringer is not None:
                self.screen_ring(ringer)
            self.draw_main()
            ch = self.scr.getch()
            if ch == -1:
                continue
            if ch in (ord("q"), ord("Q")):
                break
            if ch in (ord("a"), ord("A")):
                self.screen_add()
            elif ch in (ord("d"), ord("D")):
                self.delete_selected()
            elif ch in (ord("t"), ord("T")):
                self.toggle_selected()
            elif ch in (curses.KEY_UP, ord("k")):
                self.sel = max(0, self.sel - 1)
            elif ch in (curses.KEY_DOWN, ord("j")):
                self.sel = min(max(0, len(self.alarms) - 1), self.sel + 1)
            elif ch == curses.KEY_RESIZE:
                self.message = ""


def _init_colors():
    _EXTRA.clear()
    if not curses.has_colors():
        return
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        bg = curses.COLOR_BLACK
    if curses.COLORS >= 256:
        for pid, fg in _C256.items():
            curses.init_pair(pid, fg, bg)
    else:
        for pid, (fg, extra) in _C8.items():
            curses.init_pair(pid, fg, bg)
            _EXTRA[pid] = extra


def main(stdscr):
    _init_colors()
    PulseTUI(stdscr).run()


def launch():
    """Entry point: run the curses app, restoring the terminal on exit."""
    curses.wrapper(main)
