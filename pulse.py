#!/usr/bin/env python3
"""Thin launcher so you can run `python pulse.py ...` from the repo root."""
from pulse.clock import _cli

if __name__ == "__main__":
    raise SystemExit(_cli())
