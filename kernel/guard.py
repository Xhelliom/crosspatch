"""
NOYAU IMMUABLE. Aucun agent ne peut proposer de patch sur ce fichier.

C'est le seul endroit qui décide ce qui est modifiable. Si tu ne devais
relire qu'un fichier avant de lancer un run autonome, c'est celui-ci.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

# Chemins que les agents ne peuvent jamais modifier, même indirectement.
PROTECTED = (
    "kernel/",
    "harness/",
    "mission/OBJECTIVE.md",
    "mission/DIRECTIONS.yaml",
    ".github/",
    "Dockerfile",
    "docker-compose.yml",
    ".env",
)

# Zone explicitement ouverte à l'auto-amélioration (Goal B).
MUTABLE = (
    "orchestrator/",
    "api/",
    "mcp/",
)

# Motifs qui trahissent une tentative de contournement plutôt qu'une
# amélioration. Volontairement grossiers : un faux positif coûte un tour,
# un faux négatif coûte l'expérience entière.
SUSPICIOUS = (
    re.compile(r"\bPROTECTED\b"),
    re.compile(r"\bpass_rate\s*=\s*[\d.]"),      # score écrit en dur
    re.compile(r"subprocess|os\.system|eval\(|exec\("),
    re.compile(r"BUDGET|max_spend", re.IGNORECASE),
    re.compile(r"\.\./"),                          # échappement de chemin
)


@dataclass
class Verdict:
    ok: bool
    reason: str = ""
    risk: str = "low"          # low | medium | high
    needs_human: bool = False


def classify(paths: list[str], diff_text: str) -> Verdict:
    """Décide si un patch est recevable, et s'il doit remonter à l'humain."""
    for p in paths:
        norm = p.lstrip("./")
        if any(norm.startswith(x) for x in PROTECTED):
            return Verdict(False, f"chemin protégé : {p}", "high", True)
        if not any(norm.startswith(x) for x in MUTABLE):
            return Verdict(False, f"hors zone modifiable : {p}", "medium", True)

    hits = [r.pattern for r in SUSPICIOUS if r.search(diff_text)]
    if hits:
        return Verdict(True, f"motifs à revoir : {hits}", "high", True)

    # Toucher au prompt d'un agent ou à la boucle = revue humaine.
    sensitive = ("orchestrator/loop.py", "orchestrator/prompts/")
    if any(p.lstrip("./").startswith(s) for p in paths for s in sensitive):
        return Verdict(True, "modifie la boucle ou les prompts", "medium", True)

    return Verdict(True, "", "low", False)


def fingerprint(root: Path) -> str:
    """Empreinte des zones protégées. Vérifiée à chaque génération.

    Si elle bouge sans qu'un humain ait poussé un commit, on arrête tout :
    quelque chose a écrit là où il ne devait pas.
    """
    h = hashlib.sha256()
    for rel in PROTECTED:
        target = root / rel
        files = sorted(target.rglob("*")) if target.is_dir() else [target]
        for f in files:
            if f.is_file():
                h.update(f.relative_to(root).as_posix().encode())
                h.update(f.read_bytes())
    return h.hexdigest()
