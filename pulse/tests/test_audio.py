"""Tests for the audio module — pure helpers + tone generation. No playback."""
import os
import tempfile
import unittest
import wave

from pulse import audio


class TestPlayCommand(unittest.TestCase):
    def test_simple_players_take_path(self):
        self.assertEqual(audio.build_play_command("paplay", "a.wav"), ["paplay", "a.wav"])
        self.assertEqual(audio.build_play_command("aplay", "a.wav"), ["aplay", "a.wav"])

    def test_ffplay_gets_headless_flags(self):
        cmd = audio.build_play_command("ffplay", "a.wav")
        self.assertEqual(cmd[0], "ffplay")
        self.assertIn("-autoexit", cmd)
        self.assertIn("-nodisp", cmd)
        self.assertEqual(cmd[-1], "a.wav")

    def test_cvlc_plays_and_exits(self):
        cmd = audio.build_play_command("cvlc", "a.wav")
        self.assertIn("--play-and-exit", cmd)
        self.assertEqual(cmd[-1], "a.wav")


class TestTone(unittest.TestCase):
    def test_ensure_tone_writes_valid_wav(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "tone.wav")
            audio.ensure_tone(path)
            with open(path, "rb") as f:
                head = f.read(12)
            self.assertTrue(head.startswith(b"RIFF"))
            self.assertIn(b"WAVE", head)
            with wave.open(path, "rb") as w:
                self.assertEqual(w.getnchannels(), 1)
                self.assertEqual(w.getsampwidth(), 2)
                self.assertGreater(w.getnframes(), 0)

    def test_ensure_tone_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "tone.wav")
            audio.ensure_tone(path)
            mtime = os.path.getmtime(path)
            audio.ensure_tone(path)  # must not rewrite
            self.assertEqual(mtime, os.path.getmtime(path))


class TestResolveSound(unittest.TestCase):
    def test_prefers_existing_sound_file(self):
        with tempfile.TemporaryDirectory() as d:
            custom = os.path.join(d, "custom.wav")
            with open(custom, "wb") as f:
                f.write(b"RIFF....WAVE")
            tone = os.path.join(d, "tone.wav")
            self.assertEqual(audio.resolve_sound({"sound_file": custom}, tone), custom)
            self.assertFalse(os.path.exists(tone))  # tone not generated when file wins

    def test_falls_back_to_generated_tone(self):
        with tempfile.TemporaryDirectory() as d:
            tone = os.path.join(d, "tone.wav")
            out = audio.resolve_sound({"sound_file": None}, tone)
            self.assertEqual(out, tone)
            self.assertTrue(os.path.exists(tone))

    def test_missing_sound_file_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            tone = os.path.join(d, "tone.wav")
            out = audio.resolve_sound({"sound_file": "/nope/missing.wav"}, tone)
            self.assertEqual(out, tone)


if __name__ == "__main__":
    unittest.main()
