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

import os
import re
from pathlib import Path

import httpx

MODEL = os.environ.get("AGENT_MODEL", "mistralai/mistral-medium-3.1")
SYSTEM = "Tu écris du Python. Réponds avec un seul bloc de code, rien d'autre."


def solve(prompt: str, workdir: Path) -> None:
    code = _complete(f"{prompt}\n\nÉcris le module Python complet.")
    (workdir / "solution.py").write_text(_extract(code))


def _complete(user: str) -> str:
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
    return r.json()["choices"][0]["message"]["content"]


def _extract(text: str) -> str:
    m = re.search(r"```(?:python)?\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"
