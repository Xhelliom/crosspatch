"""Backlog géré par l'IA, arbitré par l'humain. États alignés sur A2A."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import yaml

# submitted → working → input-required → completed | failed | canceled
STATES = ("submitted", "working", "input_required",
          "completed", "failed", "canceled")


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return yaml.safe_load(path.read_text())["items"]


def save(path: Path, items: list[dict]) -> None:
    path.write_text(yaml.safe_dump({"items": items}, sort_keys=False,
                                   allow_unicode=True))


def _key(title: str) -> str:
    """Clé de déduplication : deux agents identiques ont les mêmes idées."""
    words = re.findall(r"[a-zà-ÿ]{4,}", title.lower())
    return " ".join(sorted(set(words))[:6])


def ingest(path: Path, raw_items: list[dict], author: str,
           gen_id: int | None, valid_dirs: set[str] | None = None,
           cap: int = 40) -> tuple[list[dict], int, int]:
    """Fusionne les idées produites en phase d'idéation.

    Renvoie (nouveaux, doublons, orphelins). Un fort taux de doublons entre A
    et B est le signal de convergence : ils ne voient plus rien de neuf.

    Une tâche sans direction active est écartée : c'est l'humain qui ouvre les
    terrains, l'IA qui les explore.
    """
    items = load(path)
    known = {_key(i["title"]) for i in items}
    seq = max((int(i["id"].split("-")[1]) for i in items), default=0)
    fresh, dupes, orphans = [], 0, 0

    for r in raw_items:
        if valid_dirs is not None and r.get("direction_id") not in valid_dirs:
            orphans += 1
            continue
        k = _key(r["title"])
        if k in known:
            dupes += 1
            continue
        known.add(k)
        seq += 1
        fresh.append({
            "id": f"IMP-{seq:03d}",
            "direction_id": r.get("direction_id"),
            "title": r["title"],
            "evidence": r.get("evidence", ""),
            "hypothesis": r["hypothesis"],
            "goal": r.get("goal", "A"),
            "expected_gain": float(r.get("expected_gain", 0.02)),
            "confidence": float(r.get("confidence", 0.5)),
            "cost": float(r.get("cost", 1.0)),
            "risk": r.get("risk", "low"),
            "state": "submitted",
            "author": author,
            "born_gen": gen_id,
        })

    items += fresh
    # Éviction : on ne garde pas indéfiniment des idées jamais retenues.
    live = [i for i in items if i["state"] not in ("completed", "canceled")]
    if len(live) > cap:
        live = rank(live)[:cap]
    save(path, live + [i for i in items if i["state"] == "completed"])
    return fresh, dupes, orphans


def close(path: Path, item_id: str, state: str) -> None:
    items = load(path)
    for i in items:
        if i["id"] == item_id:
            i["state"] = state
    save(path, items)


def rank(items: list[dict]) -> list[dict]:
    """gain × confiance / coût. L'agent peut proposer sa propre formule —
    c'est justement une amélioration de type B légitime."""
    live = [i for i in items if i["state"] in ("submitted", "working")]
    return sorted(
        live,
        key=lambda i: (i["expected_gain"] * i["confidence"]) / max(i["cost"], 0.1),
        reverse=True,
    )


def brief(item: dict) -> dict:
    return {k: item[k] for k in
            ("id", "title", "hypothesis", "expected_gain", "confidence", "goal")}


def tree(ws: Path) -> str:
    return "\n".join(
        sorted(p.relative_to(ws).as_posix()
               for p in ws.rglob("*.py") if "__pycache__" not in p.parts))


def render(ctx: dict) -> str:
    return json.dumps(ctx, ensure_ascii=False, indent=2, default=str)


def apply_patch(workspace: Path, diff: str) -> None:
    subprocess.run(["git", "apply", "--unidiff-zero", "-"],
                   cwd=workspace, input=diff.encode(), check=True)


def promote(root: Path, gen_id: int) -> None:
    """Commit + push. Le redéploiement recharge le code, sans hot-patch."""
    cand = root / "candidates" / str(gen_id)
    subprocess.run(["rsync", "-a", "--delete",
                    f"{cand}/orchestrator/", f"{root}/orchestrator/"], check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", f"gen {gen_id} (validée)"],
                   cwd=root, check=True)
    subprocess.run(["git", "push"], cwd=root, check=True)
