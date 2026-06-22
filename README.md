# ⏰ Pulse

A terminal alarm clock for people who live in the shell — a glowing seven-segment
clock, in-app alarm management, and a wake-up you can't sleep through.

Pulse treats every alarm like a small piece of production infrastructure:
config-driven, fully audited, resilient to the clock moving under it, and unit-tested.
Zero third-party dependencies — **Python 3.9+ standard library only**.

---

## What makes it different

- **No snooze — a Wake Challenge instead.** To dismiss an alarm you must solve a
  randomly generated task (math, type a phrase, or repeat a sequence). Difficulty
  **escalates** the longer it rings. You can't fake being awake.
- **A real seven-segment display** rendered in the terminal — bold, fixed-width
  digits with a blinking colon, inside a device bezel that **flips** to amber when
  an alarm fires.
- **Clock-jump resilient.** Travel, DST, an NTP correction, or resume-from-sleep
  can move the wall clock; Pulse detects it and re-anchors every alarm without the
  times you set ever drifting.
- **Fully audited.** Every alarm lifecycle event is appended as JSON-lines to
  `pulse.log`.

---

## Quick start

```bash
python3 pulse.py            # launch the interactive TUI
```

Inside the app:

| key   | action                          |
|-------|---------------------------------|
| `a`   | add an alarm (form)             |
| `d`   | delete the selected alarm       |
| `t`   | toggle the selected alarm on/off|
| `↑ ↓` | move the selection (also `j`/`k`)|
| `q`   | quit (state is saved)           |

When an alarm fires the device flips to a pulsing amber screen with the Wake
Challenge — solve it to dismiss; `ESC` won't.

### Scriptable CLI

The same store is usable headlessly (handy for cron or scripts):

```bash
python3 pulse.py add 07:30 "Standup" --repeat weekdays
python3 pulse.py add 06:00 "Launch"  --date 2026-07-04   # one-shot on a date
python3 pulse.py list
python3 pulse.py rm <id> | disable <id> | enable <id>
python3 pulse.py --status        # print alarms + next-fire times, then exit
python3 pulse.py demo            # near-instant fire to see the alarm flow
```

Bare `python3 pulse.py` launches the TUI; if there's no usable terminal
(no TTY, unsupported `$TERM`) it falls back to a headless daemon automatically.

---

## Architecture

A small package with one responsibility per module. The UI sits on top of a pure,
time-injectable core that holds all the logic worth testing.

```
pulse.py                 thin launcher → pulse.clock:_cli
pulse/
├── scheduler.py         Alarm model + pure scheduling/firing logic
├── store.py             persistence (alarms.json, config.json)
├── challenge.py         Wake Challenge generators + verifier
├── audio.py             tone synthesis + Siren playback
├── segdisplay.py        seven-segment digit rendering
├── audit.py             JSON-lines event log
├── clock.py             argparse CLI, --status, demo, headless daemon
├── tui.py               curses interactive app (the default experience)
└── tests/               unit tests (core, audio, segdisplay)
```

### Layering

```
            ┌──────────────┐     ┌──────────────┐
            │   tui.py      │     │   clock.py    │   front-ends
            │ (curses app)  │     │ (CLI/daemon)  │
            └──────┬────────┘     └──────┬────────┘
                   └──────────┬──────────┘
        ┌───────────┬─────────┼─────────┬───────────┐
        ▼           ▼         ▼         ▼           ▼
   scheduler.py  store.py  challenge.py audio.py  segdisplay.py   core
        │                                              (audit.py cross-cuts)
        ▼
   pure functions: next_fire · fire_decision · detect_clock_jump · mark_fired
```

Both front-ends share the **same** core. `tui.py` and `clock.py`'s daemon run an
identical poll loop; the only difference is how they draw.

### Module responsibilities

- **`scheduler.py`** — the `Alarm` dataclass and all scheduling logic as **pure,
  time-injectable functions** (every function takes `now` rather than calling the
  clock itself, so it's testable without waiting):
  - `next_fire(alarm, now)` — next local datetime an alarm should ring (handles
    `once`/`daily`/`weekdays`/`weekends` and one-shot `date`), or `None` if spent.
  - `fire_decision(alarm, target, now)` — `"wait"` / `"fire"` / `"missed"`.
    Firing is **target-based with a catch-up window**, not an exact-minute match.
  - `detect_clock_jump(...)` — compares wall-clock vs monotonic advance to spot
    *any* discontinuity (travel, DST, NTP, resume).
  - `mark_fired(alarm, target)` — records the fired minute so it can't replay.

- **`store.py`** — load/save `alarms.json` (atomic temp-file + rename) and
  `config.json`. Corrupt files degrade gracefully (warn, start empty/defaults).

- **`challenge.py`** — pure Wake Challenge generators (`math`, `typing`,
  `sequence`) plus a verifier. Seeded RNG so tests are deterministic.

- **`audio.py`** — synthesizes an alarm tone with the stdlib `wave` module, plays
  it via a detected system player, and falls back to the terminal bell. `Siren`
  loops audio in a daemon thread and is killed cleanly on dismiss/exit.

- **`segdisplay.py`** — renders a clock string to a fixed-width grid of
  `(char, lit)` cells (7 rows × 5 cols per digit). Pure; front-end decides colour.

- **`audit.py`** — appends structured JSON-lines events to `pulse.log`. Never
  raises into the caller (logging must not take the clock down).

- **`clock.py`** — argparse surface (`add`/`list`/`rm`/`enable`/`disable`/
  `--status`/`demo`), the headless daemon, and the bare-invocation → TUI launch
  with fallback.

- **`tui.py`** — the curses app: a MAIN / ADD / DELETE / RINGING state machine.
  Non-blocking input (`timeout`) keeps the clock ticking while the menu waits.
  All drawing is bounds-guarded; small/resized terminals show a size hint instead
  of crashing.

---

## How time is handled

This is the part with the most thought behind it.

**Wall-clock semantics.** Everything uses naive *local* time, never UTC. An alarm
set for `07:30` matches when the local clock reads `07:30` — the numbers you set
never shift, wherever you are.

**But the wall clock isn't monotonic.** It can jump from travel, DST, an NTP
correction, or resume-from-suspend. Pulse compares how far `datetime.now()` moved
against how far `time.monotonic()` moved each poll; a divergence beyond tolerance
is a jump. On a jump it:

1. calls `time.tzset()` so a long-running process actually picks up a new zone,
2. logs a `clock_jump` event, and
3. re-anchors every alarm's next target in the now-current zone.

**Firing is target-based with catch-up**, not an exact-minute match — so a forward
jump can't silently skip an alarm. An alarm whose moment was jumped *past* (beyond
the catch-up window) is logged as `missed` rather than firing late or vanishing.
A minute-keyed dedupe (`last_fired`) stops a backward jump from re-firing.

**Known limit.** A user-space polling clock **cannot fire while the machine is
suspended** — on resume it detects the jump and reports missed alarms, but true
wake-from-sleep needs an OS primitive (cron / systemd-timer / RTC wake), which is
out of scope here.

---

## Configuration

Drop a `config.json` next to `pulse.py` (any subset; missing keys use defaults):

```json
{
  "time_format": "24h",       // "24h" or "12h"
  "bell": true,               // terminal bell fallback when no audio player
  "challenge": "math",        // "math" | "typing" | "sequence"
  "escalate_seconds": 30,     // difficulty step interval while ringing
  "poll_seconds": 1,          // headless daemon refresh interval
  "sound_file": null,         // path to your own .wav/.mp3 (wins over the tone)
  "sound_cmd": null,          // explicit player override, e.g. "mpv --no-video"
  "flip_animation": true      // card-flip transition when an alarm fires
}
```

### Audio

By default Pulse generates a two-pitch tone (`tone.wav`, made once with stdlib
`wave`) and plays it through the first available player: `paplay`, `aplay`,
`ffplay`, `afplay`, `cvlc`, or `play`. To use your own sound, set `sound_file`.
If no player or audio stack is present (common under WSL), it falls back to the
terminal bell — audio never crashes the UI.

---

## Data & files

| file          | what it is                                                |
|---------------|-----------------------------------------------------------|
| `alarms.json` | your alarms (written atomically; survives restart)        |
| `config.json` | optional settings (above)                                 |
| `pulse.log`   | JSON-lines audit trail                                     |
| `tone.wav`    | generated alarm tone (created on first ring)              |

**Audit events:** `created`, `armed`, `fired`, `challenge_failed`, `dismissed`,
`missed`, `clock_jump`, `enable`, `disable`, `deleted`.

```bash
cat pulse.log    # one JSON object per line — grep/jq friendly
```

---

## Testing

```bash
python3 -m unittest discover -s pulse/tests -v
```

The core logic is pure and time-injectable, so the suite runs instantly with no
real clock or sleeping. Coverage includes scheduling, dated alarms, the
fire/missed decision, clock-jump detection (travel/DST/NTP/resume), challenge
generation, audio command-building + tone generation, and seven-segment layout.
The curses screens themselves aren't unit-tested — their logic lives in the pure
modules above.

---

## Requirements

- Python 3.9+
- A terminal that supports curses (Linux/macOS/WSL). On a non-curses environment
  Pulse falls back to the headless daemon.
- Optional: a CLI audio player for sound (otherwise the terminal bell is used).
  For crisp glyphs, use a **monospace** font with good box-drawing/Unicode
  coverage (JetBrains Mono, Cascadia Code, Meslo, etc.).
