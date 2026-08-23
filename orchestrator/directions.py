"""
Les grandes idées : le niveau entre la mission gelée et les tâches générées.

L'humain en pose la plupart et peut en ajouter à chaud. L'IA peut en proposer,
mais une direction proposée attend un ✅ — c'est la frontière entre « force de
proposition » et « s'autoriser un nouvel axe ».

Ce fichier est écrit par l'API, jamais par un patch d'agent : `DIRECTIONS.yaml`
est dans PROTECTED côté guard.
"""
from __future__ import annotations

from pathlib import Path

import yaml

STATES = ("active", "input_required", "rejected", "retired")


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return yaml.safe_load(path.read_text()).get("directions") or []


def save(path: Path, dirs: list[dict]) -> None:
    path.write_text(
        yaml.safe_dump({"directions": dirs}, sort_keys=False, allow_unicode=True)
    )


def active(path: Path) -> list[dict]:
    return [d for d in load(path) if d["state"] == "active"]


def _next_id(dirs: list[dict]) -> str:
    n = max((int(d["id"].split("-")[1]) for d in dirs), default=0) + 1
    return f"DIR-{n:03d}"


def add(path: Path, title: str, intent: str, author: str) -> dict:
    """author='human' → active d'emblée. author='A'/'B' → attend un verdict."""
    dirs = load(path)
    d = {
        "id": _next_id(dirs),
        "title": title,
        "intent": intent,
        "state": "active" if author == "human" else "input_required",
        "author": author,
    }
    dirs.append(d)
    save(path, dirs)
    return d


def set_state(path: Path, dir_id: str, state: str) -> dict | None:
    if state not in STATES:
        raise ValueError(f"état inconnu : {state}")
    dirs = load(path)
    for d in dirs:
        if d["id"] == dir_id:
            d["state"] = state
            save(path, dirs)
            return d
    return None


def brief(d: dict) -> dict:
    return {"id": d["id"], "title": d["title"], "intent": d["intent"].strip()}
