"""Solution de référence — run-length encoding réversible.

Le piège de la tâche : un chiffre présent dans l'entrée est indiscernable
d'un compteur. On échappe donc les chiffres littéraux (et l'antislash qui
sert d'échappement). Les entrées sans chiffre ne sont pas affectées, ce qui
préserve `encode("aaabccd") == "a3bc2d"`.
"""
from __future__ import annotations

ESC = "\\"


def _emit(ch: str) -> str:
    return ESC + ch if ch.isdigit() or ch == ESC else ch


def encode(s: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(s):
        j = i
        while j < len(s) and s[j] == s[i]:
            j += 1
        run = j - i
        out.append(_emit(s[i]))
        if run >= 2:
            out.append(str(run))
        i = j
    return "".join(out)


def decode(s: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(s):
        if s[i] == ESC:
            ch = s[i + 1]
            i += 2
        else:
            ch = s[i]
            i += 1
        n = ""
        while i < len(s) and s[i].isdigit():
            n += s[i]
            i += 1
        out.append(ch * (int(n) if n else 1))
    return "".join(out)
