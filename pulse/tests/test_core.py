"""Unit tests for Pulse core logic — pure, no real clock, no sleep."""
import random
import unittest
from datetime import datetime

from pulse import scheduler as s
from pulse import challenge as c


class TestNextFire(unittest.TestCase):
    def test_later_today(self):
        a = s.Alarm(time="07:30")
        now = datetime(2026, 6, 22, 6, 0)  # Monday
        self.assertEqual(s.next_fire(a, now), datetime(2026, 6, 22, 7, 30))

    def test_rolls_to_tomorrow_when_past(self):
        a = s.Alarm(time="07:30")
        now = datetime(2026, 6, 22, 8, 0)
        self.assertEqual(s.next_fire(a, now), datetime(2026, 6, 23, 7, 30))

    def test_weekdays_skips_weekend(self):
        a = s.Alarm(time="07:30", repeat="weekdays")
        sat = datetime(2026, 6, 20, 8, 0)  # Saturday
        self.assertEqual(s.next_fire(a, sat), datetime(2026, 6, 22, 7, 30))  # Monday

    def test_disabled_has_no_fire(self):
        a = s.Alarm(time="07:30", enabled=False)
        self.assertIsNone(s.next_fire(a, datetime(2026, 6, 22, 6, 0)))


class TestDatedAlarm(unittest.TestCase):
    def test_fires_on_exact_date(self):
        a = s.Alarm(time="07:30", date="2026-07-04")
        now = datetime(2026, 6, 22, 6, 0)
        self.assertEqual(s.next_fire(a, now), datetime(2026, 7, 4, 7, 30))

    def test_date_overrides_weekday_repeat(self):
        # 2026-07-04 is a Saturday; weekdays repeat would skip it, date must not.
        a = s.Alarm(time="07:30", date="2026-07-04", repeat="weekdays")
        now = datetime(2026, 6, 22, 6, 0)
        self.assertEqual(s.next_fire(a, now), datetime(2026, 7, 4, 7, 30))

    def test_past_date_never_fires(self):
        a = s.Alarm(time="07:30", date="2020-01-01")
        self.assertIsNone(s.next_fire(a, datetime(2026, 6, 22, 6, 0)))

    def test_dated_alarm_is_spent_after_firing(self):
        a = s.Alarm(time="07:30", date="2026-07-04")
        s.mark_fired(a, datetime(2026, 7, 4, 7, 30))
        self.assertIsNone(s.next_fire(a, datetime(2026, 7, 4, 6, 0)))

    def test_invalid_date_rejected(self):
        with self.assertRaises(ValueError):
            s.Alarm(time="07:30", date="2026-02-30")  # not a real day
        with self.assertRaises(ValueError):
            s.Alarm(time="07:30", date="07/04/2026")   # wrong format


class TestFireDecision(unittest.TestCase):
    def test_waits_before_target(self):
        a = s.Alarm(time="07:30")
        target = datetime(2026, 6, 22, 7, 30)
        self.assertEqual(s.fire_decision(a, target, datetime(2026, 6, 22, 7, 29)), "wait")

    def test_fires_within_window(self):
        a = s.Alarm(time="07:30")
        target = datetime(2026, 6, 22, 7, 30)
        self.assertEqual(s.fire_decision(a, target, datetime(2026, 6, 22, 7, 30, 5)), "fire")

    def test_missed_beyond_catchup(self):
        a = s.Alarm(time="07:30")
        target = datetime(2026, 6, 22, 7, 30)
        late = datetime(2026, 6, 22, 9, 0)  # 90 min late (travel/sleep)
        self.assertEqual(s.fire_decision(a, target, late), "missed")

    def test_no_replay_after_fired(self):
        a = s.Alarm(time="07:30")
        target = datetime(2026, 6, 22, 7, 30)
        s.mark_fired(a, target)
        self.assertEqual(s.fire_decision(a, target, datetime(2026, 6, 22, 7, 30, 5)), "wait")

    def test_backward_jump_across_midnight_no_replay(self):
        # Fired 00:05 today; clock rewinds across midnight to yesterday 23:55.
        # A date-only dedupe key would refire; our minute-keyed one must not.
        a = s.Alarm(time="00:05")
        target = datetime(2026, 6, 22, 0, 5)
        s.mark_fired(a, target)
        # next_fire from the rewound time must not re-pick the same target
        rewound = datetime(2026, 6, 21, 23, 55)
        self.assertNotEqual(s.next_fire(a, rewound), target)


class TestClockJump(unittest.TestCase):
    def test_normal_advance_no_jump(self):
        p_wall = datetime(2026, 6, 22, 7, 0, 0)
        n_wall = datetime(2026, 6, 22, 7, 0, 1)
        self.assertEqual(s.detect_clock_jump(p_wall, 100.0, n_wall, 101.0), 0.0)

    def test_forward_travel_detected(self):
        # +3h wall jump but monotonic only moved 1s -> ~10799s anomaly.
        p_wall = datetime(2026, 6, 22, 7, 0, 0)
        n_wall = datetime(2026, 6, 22, 10, 0, 1)
        self.assertGreater(s.detect_clock_jump(p_wall, 100.0, n_wall, 101.0), 10000)

    def test_backward_jump_detected_negative(self):
        p_wall = datetime(2026, 6, 22, 7, 0, 0)
        n_wall = datetime(2026, 6, 22, 6, 0, 1)
        self.assertLess(s.detect_clock_jump(p_wall, 100.0, n_wall, 101.0), 0)


class TestChallenge(unittest.TestCase):
    def test_math_is_correct_and_escalates(self):
        rng = random.Random(42)
        ch0 = c.make_challenge("math", rng, level=0)
        self.assertTrue(ch0.check(ch0.answer))
        self.assertFalse(ch0.check("definitely wrong"))

    def test_sequence_grows_with_level(self):
        rng = random.Random(1)
        low = c.make_challenge("sequence", random.Random(1), level=0).answer
        high = c.make_challenge("sequence", random.Random(1), level=5).answer
        self.assertGreater(len(high), len(low))

    def test_unknown_kind_falls_back_to_math(self):
        ch = c.make_challenge("nope", random.Random(0), 0)
        self.assertTrue(ch.check(ch.answer))


if __name__ == "__main__":
    unittest.main()
