"""Solution de référence — fusion d'intervalles."""
from __future__ import annotations


def merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    norm = sorted((min(a, b), max(a, b)) for a, b in intervals)
    out: list[tuple[int, int]] = []
    for lo, hi in norm:
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out
