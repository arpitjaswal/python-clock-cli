"""Pulse — terminal alarm clock with a Wake Challenge and clock-jump resilience.

Run `python -m pulse.clock` (or `python pulse.py`) to start the daemon.
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import timedelta

from . import scheduler as sched
from .audit import Audit
from .challenge import make_challenge
from .scheduler import Alarm
from .store import load_alarms, load_config, save_alarms

CLEAR = "\033[2J\033[H"
HIDE, SHOW = "\033[?25l", "\033[?25h"


def _fmt_now(now, fmt: str) -> str:
    pattern = "%I:%M:%S %p" if fmt == "12h" else "%H:%M:%S"
    return now.strftime(f"%a %Y-%m-%d  {pattern}")


def _render_clock(now, alarms, cfg) -> None:
    sys.stdout.write(CLEAR)
    print("  ⏰  P U L S E")
    print("  " + "─" * 36)
    print(f"  {_fmt_now(now, cfg['time_format'])}")
    print()
    upcoming = [
        (sched.next_fire(a, now), a) for a in alarms if a.enabled
    ]
    upcoming = sorted((t for t in upcoming if t[0]), key=lambda x: x[0])
    if upcoming:
        nxt, a = upcoming[0]
        print(f"  next: {a.time} {a.label or '(no label)'}  [{a.repeat}]  in {_human_delta(nxt - now)}")
    else:
        print("  next: (no alarms armed)")
    print("\n  Ctrl-C to quit.")
    sys.stdout.flush()


def _human_delta(td) -> str:
    s = int(td.total_seconds())
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def _run_challenge(alarm: Alarm, cfg, audit: Audit) -> None:
    """Block, ringing, until the user passes an escalating Wake Challenge."""
    rng = random.Random()  # live randomness; tests seed their own
    started = time.monotonic()
    level = 0
    bell = "\a" if cfg["bell"] else ""
    while True:
        elapsed = time.monotonic() - started
        level = int(elapsed // max(cfg["escalate_seconds"], 1))
        ch = make_challenge(cfg["challenge"], rng, level)
        sys.stdout.write(CLEAR + bell)
        print("  🔔🔔🔔  W A K E   U P  🔔🔔🔔")
        print(f"  {alarm.time}  {alarm.label or ''}")
        print(f"  ringing {int(elapsed)}s   difficulty L{level}")
        print("  " + "─" * 36)
        print(f"  {ch.prompt}")
        sys.stdout.flush()
        try:
            given = input("  answer> ")
        except EOFError:
            given = ""
        if ch.check(given):
            audit.event("dismissed", alarm.id, level=level, ringing_s=int(elapsed))
            print("  ✔ dismissed. good morning.")
            return
        audit.event("challenge_failed", alarm.id, level=level)
        print("  ✘ wrong — new challenge.")


def run_demo(seconds: int = 6) -> int:
    """End-to-end demo: live clock, then an alarm fires in `seconds` and you
    solve a real Wake Challenge to dismiss it. Touches no saved state."""
    cfg = load_config()
    audit = Audit("pulse.log")
    now = sched.local_now()
    a = Alarm(time=now.strftime("%H:%M"), label="DEMO — solve to dismiss")
    target = now.replace(microsecond=0) + timedelta(seconds=seconds)
    audit.event("armed", a.id, demo=True, target=str(target))
    sys.stdout.write(HIDE)
    try:
        while sched.local_now() < target:
            now = sched.local_now()
            sys.stdout.write(CLEAR)
            print("  ⏰  P U L S E   —   D E M O")
            print("  " + "─" * 36)
            print(f"  {_fmt_now(now, cfg['time_format'])}")
            print(f"\n  alarm '{a.label}' rings in {int((target - now).total_seconds())}s …")
            print("\n  Ctrl-C to quit.")
            sys.stdout.flush()
            time.sleep(0.25)
        audit.event("fired", a.id, target=str(target), demo=True)
        _run_challenge(a, cfg, audit)
        return 0
    except KeyboardInterrupt:
        sys.stdout.write(SHOW + "\n  demo aborted.\n")
        return 0
    finally:
        sys.stdout.write(SHOW)


def run(args) -> int:
    cfg = load_config()
    audit = Audit()
    alarms = load_alarms()
    targets: dict[str, object] = {}

    if args.status:
        now = sched.local_now()
        for a in alarms:
            nf = sched.next_fire(a, now)
            print(f"{a.id}  {a.time} {a.repeat:8} {'on ' if a.enabled else 'off'}"
                  f"  {a.label or ''}  -> {nf}")
        return 0

    sys.stdout.write(HIDE)
    prev_wall, prev_mono = sched.local_now(), time.monotonic()
    for a in alarms:
        audit.event("armed", a.id, time=a.time, repeat=a.repeat)
        targets[a.id] = sched.next_fire(a, prev_wall)
    try:
        while True:
            now, mono = sched.local_now(), time.monotonic()

            # --- clock-jump resilience (travel / DST / NTP / resume) ---
            anomaly = sched.detect_clock_jump(prev_wall, prev_mono, now, mono)
            if anomaly:
                time.tzset()  # pick up a new timezone in a long-running process
                now = sched.local_now()
                audit.event("clock_jump", seconds=round(anomaly, 1),
                            new_offset_min=time.localtime().tm_gmtoff // 60
                            if time.localtime().tm_gmtoff is not None else 0)
                for a in alarms:                      # re-anchor every target
                    targets[a.id] = sched.next_fire(a, now)

            for a in alarms:
                decision = sched.fire_decision(a, targets.get(a.id), now)
                if decision == "fire":
                    target = targets[a.id]
                    audit.event("fired", a.id, target=str(target), label=a.label)
                    _run_challenge(a, cfg, audit)
                    sched.mark_fired(a, target)
                    save_alarms(alarms)
                    targets[a.id] = sched.next_fire(a, sched.local_now())
                elif decision == "missed":
                    target = targets[a.id]
                    audit.event("missed", a.id, target=str(target),
                                reason="clock jumped past catch-up window")
                    sched.mark_fired(a, target)
                    targets[a.id] = sched.next_fire(a, now)

            _render_clock(now, alarms, cfg)
            prev_wall, prev_mono = now, mono
            time.sleep(cfg["poll_seconds"])
    except KeyboardInterrupt:
        save_alarms(alarms)
        sys.stdout.write(SHOW + "\n  saved. bye.\n")
        return 0
    finally:
        sys.stdout.write(SHOW)


def _cli() -> int:
    p = argparse.ArgumentParser(prog="pulse", description="Pulse alarm clock")
    p.add_argument("--status", action="store_true", help="print alarms and exit")
    sub = p.add_subparsers(dest="cmd")

    a = sub.add_parser("add", help="add an alarm")
    a.add_argument("time"); a.add_argument("label", nargs="?", default="")
    a.add_argument("--repeat", default="once", choices=sched.REPEAT_MODES)
    a.add_argument("--date", default=None, help="fire once on YYYY-MM-DD")

    d = sub.add_parser("demo", help="run a full end-to-end demo (no saved state)")
    d.add_argument("--in", dest="secs", type=int, default=6,
                   help="seconds until the demo alarm fires (default 6)")

    sub.add_parser("list", help="list alarms")
    for name in ("rm", "enable", "disable"):
        sp = sub.add_parser(name, help=f"{name} an alarm by id")
        sp.add_argument("id")

    args = p.parse_args()
    audit = Audit()

    if args.cmd == "demo":
        return run_demo(args.secs)
    if args.cmd == "add":
        alarms = load_alarms()
        try:
            al = Alarm(time=args.time, label=args.label, repeat=args.repeat,
                       date=args.date)
        except ValueError as e:
            print(f"error: {e}"); return 2
        if sched.next_fire(al, sched.local_now()) is None:
            print(f"warning: {al.date or al.time} is in the past — this alarm will never fire.")
        alarms.append(al); save_alarms(alarms)
        audit.event("created", al.id, time=al.time, repeat=al.repeat,
                    label=al.label, date=al.date)
        when = f"{al.date} {al.time}" if al.date else f"{al.time} [{al.repeat}]"
        print(f"added {al.id}  {when} {al.label}")
        return 0
    if args.cmd == "list":
        for al in load_alarms():
            when = al.date if al.date else al.repeat
            print(f"{al.id}  {al.time}  {when:10}  {'on' if al.enabled else 'off':3}  {al.label}")
        return 0
    if args.cmd in ("rm", "enable", "disable"):
        alarms = load_alarms()
        hit = next((x for x in alarms if x.id == args.id), None)
        if not hit:
            print(f"no alarm {args.id}"); return 2
        if args.cmd == "rm":
            alarms.remove(hit)
        else:
            hit.enabled = args.cmd == "enable"
        save_alarms(alarms)
        audit.event(args.cmd if args.cmd != "rm" else "deleted", hit.id)
        print(f"{args.cmd} {hit.id}")
        return 0

    if args.status:
        return run(args)  # headless one-shot status print

    # Bare invocation -> interactive TUI. Fall back to the headless daemon if
    # curses can't run (no TTY, unsupported terminal).
    try:
        from .tui import launch
        launch()
        return 0
    except Exception as e:  # curses.error, ImportError, terminal issues
        print(f"interactive mode unavailable ({e}); running headless daemon.\n"
              "use `python pulse.py add ...` to manage alarms.")
        return run(args)


if __name__ == "__main__":
    raise SystemExit(_cli())
