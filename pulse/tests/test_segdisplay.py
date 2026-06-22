"""Tests for the seven-segment renderer — pure, no curses."""
import unittest

from pulse import segdisplay as sd


def _lit(grid):
    return sum(1 for row in grid for _, on in row if on)


def _glyphs(grid):
    return sum(1 for row in grid for ch, _ in row if ch != " ")


class TestSegDisplay(unittest.TestCase):
    def test_grid_height(self):
        self.assertEqual(len(sd.render("12:34")), sd.height())
        self.assertEqual(sd.height(), 7)

    def test_every_digit_has_same_shape(self):
        # Fixed width/shape: all digits draw the same set of segment cells,
        # only their lit-ness differs. This is what keeps spacing consistent.
        counts = {ch: _glyphs(sd.render(ch)) for ch in "0123456789"}
        self.assertEqual(len(set(counts.values())), 1)

    def test_eight_lights_everything(self):
        grid = sd.render("8")
        self.assertEqual(_lit(grid), _glyphs(grid))  # all segments lit

    def test_one_is_two_verticals(self):
        # '1' = segments b + c, each a 2-cell vertical → 4 lit cells, on the
        # right side. Distinct from a blank and clearly a seven-segment 1.
        grid = sd.render("1")
        self.assertEqual(_lit(grid), 4)
        # all four lit cells are in the right-most column of the digit
        right_col = sd.CELL_W - 1
        lit_cols = [c for row in grid for c, (_, on) in enumerate(row) if on]
        self.assertTrue(all(c == right_col for c in lit_cols))

    def test_ghost_segments_drawn_but_unlit(self):
        grid = sd.render("1")
        self.assertEqual(_glyphs(grid) - _lit(grid),
                         _glyphs(sd.render("8")) - 4)  # rest are ghosts

    def test_colon_dots_centered_and_blink(self):
        on = sd.render(":", colon_on=True)
        off = sd.render(":", colon_on=False)
        lit_rows = [r for r, row in enumerate(on) for _, o in row if o]
        # two dots, symmetric around the middle row
        self.assertEqual(len(lit_rows), 2)
        mid = sd.height() // 2
        self.assertEqual(lit_rows, [mid - 1, mid + 1])
        self.assertEqual(_lit(off), 0)

    def test_width_matches_grid(self):
        for text in ("12:34:56", "07:30", "8", "00:00:00"):
            self.assertEqual(sd.width(text), len(sd.render(text)[0]))


if __name__ == "__main__":
    unittest.main()
