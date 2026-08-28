"""
L'agent évalué par le harness. C'est **ce fichier** que A et B se réécrivent
mutuellement — le reste de `orchestrator/` n'est que la machinerie autour.

Il est volontairement naïf : un seul appel, pas de relecture des tests, pas de
retry, pas de mémoire des échecs. C'est le headroom. Si tu l'améliores toi-même
à la main, tu retires à l'expérience ce qu'elle est censée découvrir.

Contrat attendu par le harness :
    solve(prompt: str, workdir: Path) -> None
        écrit une solution dans workdir/solution.py
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx

MODEL = os.environ.get("AGENT_MODEL", "mistralai/mistral-medium-3.1")
SYSTEM = "Tu écris du Python. Réponds avec un seul bloc de code, rien d'autre."


def solve(prompt: str, workdir: Path) -> None:
    code, usage = _complete(f"{_compress_context(prompt)}\n\nÉcris le module Python complet.")
    (workdir / "solution.py").write_text(_extract(code))
    # Le harness ne voit pas les appels réseau de l'agent : sans ce report,
    # `tokens_per_task` restait à 0.0 et les agents proposaient d'optimiser
    # un chiffre qui n'avait jamais été mesuré. C'est de l'instrumentation,
    # pas une aide à résoudre — garde-la si tu réécris ce fichier.
    (workdir / "usage.json").write_text(json.dumps(usage))


def _compress_context(prompt: str) -> str:
    """Compresse le contexte du prompt en supprimant les informations redondantes."""
    lines = prompt.split('\n')
    compressed_lines = []
    for line in lines:
        if not line.strip() or line.startswith('#'):
            continue
        if line not in compressed_lines:
            compressed_lines.append(line)
    return '\n'.join(compressed_lines)


def _complete(user: str) -> tuple[str, dict]:
    r = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        },
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"], data.get("usage") or {}


def _extract(text: str) -> str:
    m = re.search(r"```(?:python)?\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"
