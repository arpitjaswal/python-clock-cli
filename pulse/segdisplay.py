"""Seven-segment display rendering for the terminal clock — the visual hero.

Renders time as glowing seven-segment digits the way a clock-radio / VFD does:
every segment is drawn, but only the *lit* ones glow — the unlit ones stay as
faint "ghost" segments behind them (the detail that sells a real display).

Geometry: each digit is a fixed CELL_W x ROWS grid (5 wide, 7 tall), so every
digit — and the whole clock — keeps a constant width and never shifts as the
numbers change. Horizontal segments are 3 cells wide; verticals are 2 cells
tall. The colon is its own 1-wide cell with two dots centered vertically.

Pure and testable: render() returns a ROWS-row grid of (char, lit) cells; the
front-end decides how to colour lit vs ghost.

Segment names (standard):
        a
      f   b
        g
      e   c
        d
"""
from __future__ import annotations

# Which segments are lit for each character.
SEGMENTS = {
    "0": "abcdef",
    "1": "bc",
    "2": "abged",
    "3": "abgcd",
    "4": "fgbc",
    "5": "afgcd",
    "6": "afgedc",
    "7": "abc",
    "8": "abcdefg",
    "9": "abcdfg",
}

CELL_W = 5          # digit cell width
ROWS = 7            # digit cell height
_MID = ROWS // 2    # row of the middle segment (g)
_HBAR, _VBAR = "━", "┃"


def _segment_at(r: int, c: int) -> tuple[str, str | None]:
    """Glyph and segment name at (row, col) of a digit cell, or (' ', None)."""
    inner = 1 <= c <= CELL_W - 2          # horizontal bars span the inner cols
    left, right = c == 0, c == CELL_W - 1
    if inner and r == 0:
        return _HBAR, "a"
    if inner and r == _MID:
        return _HBAR, "g"
    if inner and r == ROWS - 1:
        return _HBAR, "d"
    if 1 <= r <= _MID - 1:                 # upper verticals
        if left:
            return _VBAR, "f"
        if right:
            return _VBAR, "b"
    if _MID + 1 <= r <= ROWS - 2:          # lower verticals
        if left:
            return _VBAR, "e"
        if right:
            return _VBAR, "c"
    return " ", None


def _digit_cell(ch: str) -> list[list[tuple[str, bool]]]:
    lit = set(SEGMENTS.get(ch, ""))
    grid = []
    for r in range(ROWS):
        row = []
        for c in range(CELL_W):
            glyph, seg = _segment_at(r, c)
            row.append((glyph, seg is not None and seg in lit))
        grid.append(row)
    return grid


def _colon_cell(on: bool) -> list[list[tuple[str, bool]]]:
    """1-col colon; two dots centered vertically (rows _MID-1 and _MID+1)."""
    rows = []
    for r in range(ROWS):
        is_dot = r in (_MID - 1, _MID + 1)
        rows.append([("●", on)] if is_dot else [(" ", False)])
    return rows


def render(text: str, colon_on: bool = True) -> list[list[tuple[str, bool]]]:
    """Render a clock string (digits + ':') to ROWS rows of (char, lit) cells.

    A single space gutter separates cells. `colon_on` toggles the blink. The
    colon dots are always drawn (dim when off) so total width is constant.
    """
    rows: list[list[tuple[str, bool]]] = [[] for _ in range(ROWS)]
    for i, ch in enumerate(text):
        cell = _colon_cell(colon_on) if ch == ":" else _digit_cell(ch)
        for r in range(ROWS):
            if i:
                rows[r].append((" ", False))  # gutter
            rows[r].extend(cell[r])
    return rows


def width(text: str) -> int:
    """Rendered character width of `text` (digits=CELL_W, colon=1, +gutters)."""
    if not text:
        return 0
    cells = sum(1 if ch == ":" else CELL_W for ch in text)
    return cells + (len(text) - 1)


def height() -> int:
    return ROWS
