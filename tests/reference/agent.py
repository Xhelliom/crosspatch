"""
Agent de référence — le `solve()` bidon de l'étape 1 de la passation.

Il n'appelle aucun modèle : il recopie une solution écrite à la main. Son
seul rôle est de valider le harness indépendamment du reste (si celui-ci
ne sort pas `pass_rate: 1.0` avec des solutions correctes, le problème est
dans le harness, pas dans les agents).

Il est monté à la place de `orchestrator/agent.py` dans un workspace jetable,
jamais dans le dépôt : ce n'est pas un agent candidat.
"""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).parent

# Reconnaissance par mot-clé du prompt. Volontairement rudimentaire : ce
# fichier ne doit rien apprendre à personne, seulement fournir la vérité.
BY_KEYWORD = (
    ("merge(intervals", "interval_merge.py"),
    ("retry(attempts", "retry_backoff.py"),
    ("encode(s: str)", "rle.py"),
)


def solve(prompt: str, workdir: Path) -> None:
    for needle, fname in BY_KEYWORD:
        if needle in prompt:
            (workdir / "solution.py").write_text(
                (HERE / fname).read_text(), encoding="utf-8")
            return
    raise LookupError(f"aucune solution de référence pour ce prompt : {prompt[:60]!r}")
