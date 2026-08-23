"""
Serveur MCP : ce que Claude Code voit du système.

Volontairement en lecture seule sauf `control` et `verdict`. Un agent qui
peut lire l'état, les logs et le backlog peut diagnostiquer 90 % des
problèmes ; lui donner plus n'ajoute que du risque.

À protéger derrière Tailscale — ce serveur peut arrêter un run.
"""
from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

API = os.environ.get("CROSSPATCH_API", "http://localhost:8080")
TOKEN = os.environ["CONTROL_TOKEN"]
mcp = FastMCP("crosspatch")


def _get(path: str, **params):
    r = httpx.get(f"{API}{path}", params=params,
                  headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict):
    r = httpx.post(f"{API}{path}", json=body,
                   headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30)
    r.raise_for_status()
    return r.json()


@mcp.tool()
def get_state() -> dict:
    """Génération courante, meilleur score, dépense, items en attente de validation."""
    return _get("/state")


@mcp.tool()
def get_backlog() -> dict:
    """Le backlog géré par les agents, avec états et priorités."""
    return _get("/backlog")


@mcp.tool()
def get_directions() -> dict:
    """Les grandes idées, avec leur état. `input_required` attend un arbitrage."""
    return _get("/directions")


@mcp.tool()
def add_direction(title: str, intent: str = "") -> dict:
    """Poser une nouvelle grande idée. Prend effet au prochain tour d'idéation."""
    return _post("/directions", {"title": title, "intent": intent})


@mcp.tool()
def set_direction_state(dir_id: str, state: str) -> dict:
    """Trancher ou clore une direction. state : active | rejected | retired."""
    return _post(f"/directions/{dir_id}/verdict", {"state": state})


@mcp.tool()
def get_transcript(after_id: int = 0, limit: int = 100) -> list:
    """Le fil A ↔ B ↔ arbitre. Utile pour comprendre pourquoi une génération a échoué."""
    return _get("/transcript", after=after_id, limit=limit)


@mcp.tool()
def set_verdict(gen_id: int, verdict: str) -> dict:
    """Valider ou rejeter une génération. verdict : ok | scope | risky | useless."""
    return _post(f"/verdict/{gen_id}", {"verdict": verdict})


@mcp.tool()
def control(action: str, gen_id: int | None = None) -> dict:
    """action : pause | resume | stop | rollback."""
    return _post("/control", {"action": action, "gen_id": gen_id})


if __name__ == "__main__":
    mcp.run()
