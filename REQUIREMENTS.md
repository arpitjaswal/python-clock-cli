# ⏰ Pulse — Requirements Specification

**Project:** `python-clock` (codename *Pulse*)
**Type:** Terminal-based alarm clock with a twist
**Build budget:** 30 minutes (single sitting, one developer)
**Language/Runtime:** Python 3.9+ (standard library only — zero install)

---

## 1. Vision

Most alarm clocks just *wake you up*. **Pulse** makes you *prove you're awake* before it
shuts up, and treats every alarm as a first-class, observable, auditable event — the way
an enterprise system would treat a scheduled job. It's a clock that behaves like a tiny
piece of production infrastructure: configurable, logged, testable, and resilient.

The "unique" hook: **no snooze button — a Wake Challenge instead.** To dismiss an alarm
you must solve a randomly generated task (math problem, type a phrase, or a sequence).
Difficulty escalates the longer you ignore it. You can't fake being awake.

---

## 2. Scope (what we build in 30 min)

A single-file (or small-package) CLI app that:

1. Shows a live, updating clock in the terminal.
2. Lets the user set one or more alarms.
3. Fires alarms at the right time with an audible signal + visual takeover.
4. Requires a **Wake Challenge** to dismiss.
5. Persists alarms and logs every alarm lifecycle event.

Out of scope (explicit non-goals): GUI, mobile, cloud sync, real audio synthesis beyond
the terminal bell, multi-user accounts.

---

## 3. Functional Requirements

### FR-1 — Live Clock Display
- Render the current local time, updating at least once per second.
- Format: `HH:MM:SS` plus date and weekday; 12/24-hour configurable.
- Must not flicker (in-place redraw, not scroll spam).

### FR-2 — Alarm Management
- **Create** an alarm: time (`HH:MM`), optional label, optional repeat
  (`once`, `daily`, `weekdays`, `weekends`).
- **List** all alarms with next-fire time and enabled/disabled state.
- **Enable / disable / delete** an alarm by id.
- At least one alarm active at a time; support N alarms.

### FR-3 — Alarm Firing
- An armed alarm fires within ≤1s of its scheduled second.
- On fire: emit terminal bell (`\a`) on a loop, take over the screen with a bold
  flashing banner showing label + time.
- Firing is **non-blocking** to the clock loop where reasonably possible.

### FR-4 — Wake Challenge (the unique bit)
- No plain dismiss. To stop the alarm the user must pass a challenge:
  - **Math:** solve `a op b` (escalating operand size).
  - **Typing:** retype a randomly chosen phrase exactly.
  - **Sequence:** repeat a shown digit sequence.
- Challenge **difficulty escalates** every 30s the alarm rings.
- Wrong answer → new challenge, alarm keeps ringing.
- One pass dismisses the alarm.

### FR-5 — Persistence
- Alarms saved to `alarms.json` and reloaded on startup.
- Survives restart; corrupt file degrades gracefully (warn + start empty).

### FR-6 — Audit Log (the enterprise bit)
- Append structured JSON-lines events to `pulse.log`:
  `created`, `armed`, `fired`, `challenge_failed`, `dismissed`, `disabled`.
- Each record: ISO-8601 timestamp, event, alarm id, label, metadata.

---

## 4. Non-Functional / "Enterprise Grade" Requirements

| # | Requirement |
|---|-------------|
| NFR-1 | **Config-driven** — behavior tunable via `config.json` (time format, bell on/off, challenge type, escalation interval). Sane defaults if absent. |
| NFR-2 | **Observability** — every state transition logged (FR-6); a `--status` flag prints next alarm + uptime. |
| NFR-3 | **Resilience** — never crash on bad input; catch `KeyboardInterrupt` to exit cleanly and persist state. |
| NFR-4 | **Testability** — core scheduling/challenge logic in pure functions, time injectable (no hard `datetime.now()` in logic) so it's unit-testable. Ship ≥3 tests. |
| NFR-5 | **Zero-dependency** — standard library only; runs anywhere Python 3.9+ exists. |
| NFR-6 | **Separation of concerns** — clock/scheduler, alarm store, challenge engine, and UI/renderer as distinct modules or clearly-bounded sections. |

---

## 5. Architecture (target)

```
pulse/
├── clock.py        # main loop, terminal renderer
├── scheduler.py    # alarm model, next-fire calc, due check (pure, time-injectable)
├── store.py        # load/save alarms.json + config.json
├── challenge.py    # Wake Challenge generators + verifier (pure)
├── audit.py        # JSON-lines event logger
└── tests/test_core.py
```
*(If time-pressed, collapse to a single `pulse.py` keeping the same logical sections.)*

---

## 6. CLI Surface

```
python pulse.py                      # start the live clock + alarm daemon
python pulse.py add 07:30 "Standup" --repeat weekdays
python pulse.py list
python pulse.py rm <id>
python pulse.py disable <id>
python pulse.py --status
```

---

## 7. Acceptance Criteria (Definition of Done)

- [ ] Clock displays and updates every second without flicker.
- [ ] Can add, list, and delete an alarm; persists across restart.
- [ ] An alarm set for "now + 1 min" fires within 1s of target.
- [ ] Alarm cannot be dismissed without passing a Wake Challenge.
- [ ] Challenge difficulty visibly escalates over time.
- [ ] `pulse.log` contains a full, parseable event trail for one alarm lifecycle.
- [ ] `Ctrl-C` exits cleanly and saves state.
- [ ] ≥3 unit tests pass (`python -m pytest` or `unittest`).

---

## 8. Stretch (only if time remains)

- ASCII "big digit" clock face.
- Per-alarm challenge type override.
- "Sunrise" gradual brightening via ANSI color ramp before fire.
- `--simulate <iso-time>` to fast-forward and demo without waiting.

---

## 9. 30-Minute Build Plan

| Min | Task |
|-----|------|
| 0–5 | Skeleton, config/store load, alarm model |
| 5–12 | Scheduler: next-fire calc + due check + tests |
| 12–18 | Live clock loop + in-place render |
| 18–24 | Firing + Wake Challenge engine |
| 24–28 | Audit log + persistence wiring |
| 28–30 | CLI args, smoke test, README one-liner |
