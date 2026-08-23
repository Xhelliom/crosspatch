"""
Control plane. Tout ce dont tu as besoin depuis un téléphone passe par ici :
lire l'état, valider ou rejeter, mettre en pause, revenir en arrière.

Aucun debug ne doit dépendre d'un terminal — c'est la contrainte qui rend
le "sans PC" réel.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from kernel.archive import Archive
from orchestrator import directions as dr

ROOT = Path(__file__).resolve().parents[1]
cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
# CROSSPATCH_ARCHIVE permet de pointer une autre base sans toucher
# config.yaml : indispensable pour tester, utile pour rejouer un run.
archive = Archive(os.environ.get("CROSSPATCH_ARCHIVE") or cfg["archive_path"])
app = FastAPI(title="crosspatch")

# Pas de référence au worker : il tourne dans un autre conteneur et ne
# partage aucune mémoire avec l'API. Tout passe par la table `control` de
# l'archive — l'API écrit des intentions, le worker est seul à exécuter.
# Ne pas « corriger » ça en fusionnant les deux services : la séparation
# web / worker est ce qui permet le scale-to-zero en cloud.
VERDICTS = ("ok", "scope", "risky", "useless")


def auth(authorization: str = Header("")) -> None:
    if authorization != f"Bearer {os.environ['CONTROL_TOKEN']}":
        raise HTTPException(401, "jeton invalide")


@app.get("/state", dependencies=[Depends(auth)])
def state() -> dict:
    best = archive.best(cfg["run_id"])
    recent = archive.recent(cfg["run_id"], 10)
    return {
        "run_id": cfg["run_id"],
        "model": cfg["model"],
        "paused": archive.flag("paused"),
        "stopped": archive.flag("stop"),
        "spent_usd": round(archive.spent(), 4),
        "max_usd": cfg["max_usd"],
        "best": dict(best) if best else None,
        "acceptance_rate": archive.acceptance_rate(cfg["run_id"]),
        "awaiting_human": [
            # `stage` dit ce que « oui » veut dire ici : autoriser
            # l'évaluation d'un patch sensible, ou intégrer un patch mesuré.
            {**dict(g),
             "stage": "gate" if g["status"] == "awaiting_gate" else "promote"}
            for g in archive.awaiting(cfg["run_id"])
        ],
        "recent": [
            {k: g[k] for k in ("id", "role_proposer", "status", "risk", "note")}
            for g in recent
        ],
    }


@app.get("/backlog", dependencies=[Depends(auth)])
def backlog() -> dict:
    return yaml.safe_load((ROOT / "mission" / "BACKLOG.yaml").read_text())


DIRS = ROOT / "mission" / "DIRECTIONS.yaml"


@app.get("/directions", dependencies=[Depends(auth)])
def directions() -> dict:
    """Les grandes idées. `input_required` = proposée par un agent, à trancher."""
    return {"directions": dr.load(DIRS)}


@app.post("/directions", dependencies=[Depends(auth)])
def add_direction(body: dict) -> dict:
    """Poser une nouvelle grande idée. body: {"title": ..., "intent": ...}

    Prend effet au prochain tour d'idéation, sans redémarrer le run : c'est le
    volant de direction, contrairement à OBJECTIVE.md qui reste gelé.
    """
    if not body.get("title"):
        raise HTTPException(400, "titre manquant")
    d = dr.add(DIRS, body["title"], body.get("intent", ""), "human")
    archive.say(None, "human", "message", f"Nouvelle direction {d['id']} : {d['title']}")
    return d


@app.post("/directions/{dir_id}/verdict", dependencies=[Depends(auth)])
def direction_verdict(dir_id: str, body: dict) -> dict:
    """Trancher une direction proposée par un agent.
    body: {"verdict": "ok" | "no"} — ou {"state": "retired"} pour en clore une.
    """
    state = body.get("state") or ("active" if body.get("verdict") == "ok" else "rejected")
    d = dr.set_state(DIRS, dir_id, state)
    if not d:
        raise HTTPException(404, "direction inconnue")
    archive.say(None, "human", "verdict", f"{dir_id} → {state}")
    return d


@app.post("/verdict/{gen_id}", dependencies=[Depends(auth)])
def verdict(gen_id: int, body: dict) -> dict:
    """body: {"verdict": "ok" | "scope" | "risky" | "useless"}

    L'API ne tranche pas : elle dépose l'intention, le worker l'exécute au
    tour de boucle suivant. Sur une génération `awaiting_gate`, « ok »
    autorise l'évaluation ; sur `awaiting_human`, il intègre.
    """
    v = body.get("verdict")
    if v not in VERDICTS:
        raise HTTPException(400, "verdict inconnu")
    row = archive.get(gen_id)
    if row is None:
        raise HTTPException(404, "génération inconnue")
    if row["status"] not in ("awaiting_gate", "awaiting_human"):
        raise HTTPException(409, f"génération {gen_id} en état {row['status']}")
    archive.set_control(f"verdict:{gen_id}", v)
    archive.say(gen_id, "human", "verdict", v)
    return {"ok": True, "gen_id": gen_id, "verdict": v, "queued": True}


@app.post("/control", dependencies=[Depends(auth)])
def control(body: dict) -> dict:
    """body: {"action": "pause" | "resume" | "stop" | "rollback", "gen_id": 12}

    Toutes les actions sont des écritures en base. Le worker les relit à
    chaque itération : rien n'est exécuté depuis ce processus.
    """
    action = body.get("action")
    if action == "pause":
        archive.set_control("paused", "1")
    elif action == "resume":
        archive.set_control("paused", "0")
    elif action == "stop":
        archive.set_control("stop", "1")
    elif action == "rollback":
        gen_id = body.get("gen_id")
        if not isinstance(gen_id, int):
            raise HTTPException(400, "rollback : gen_id manquant")
        row = archive.get(gen_id)
        if row is None:
            raise HTTPException(404, "génération inconnue")
        if row["status"] != "completed":
            raise HTTPException(409, "seule une génération intégrée s'annule")
        archive.set_control(f"rollback:{gen_id}", "1")
    else:
        raise HTTPException(400, "action inconnue")
    archive.say(body.get("gen_id"), "human", "verdict", f"contrôle : {action}")
    return {"ok": True, "action": action, "queued": True}


@app.get("/transcript", dependencies=[Depends(auth)])
def transcript(after: int = 0, limit: int = 100) -> list:
    """Le fil A / B / arbitre en une fois. Le pendant non-streamé de /stream."""
    return [dict(r) for r in archive.transcript(after, limit)]


@app.get("/stream")
async def stream(after: int = 0, token: str = "") -> StreamingResponse:
    # EventSource ne permet pas d'en-tête personnalisé : jeton en query string.
    if token != os.environ["CONTROL_TOKEN"]:
        raise HTTPException(401, "jeton invalide")
    """Le fil de discussion A ↔ B ↔ arbitre, en direct."""
    async def gen():
        cursor = after
        while True:
            for row in archive.transcript(cursor):
                cursor = row["id"]
                yield f"data: {json.dumps(dict(row))}\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/")
def ui() -> FileResponse:
    return FileResponse(ROOT / "api" / "static" / "index.html")
