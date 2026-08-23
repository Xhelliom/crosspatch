"""
Invariant n°6 : parité UI ↔ MCP ↔ HTTP.

« Une fonctionnalité qui n'existe que d'un côté est un bug. » Ce test rend
l'invariant exécutable au lieu de le laisser à la vigilance : tout chemin
appelé par le serveur MCP doit exister dans l'API, et tout endpoint de
pilotage doit avoir un outil MCP.
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _generique(path: str) -> str:
    """`/verdict/{gen_id}` et `/verdict/{id}` désignent le même endpoint."""
    return re.sub(r"\{[^}]+\}", "{}", path)


@pytest.fixture(scope="module")
def serveur_mcp(tmp_path_factory):
    import os
    os.environ.setdefault("CONTROL_TOKEN", "jeton-de-test")
    spec = importlib.util.spec_from_file_location(
        "crosspatch_mcp", ROOT / "mcp" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def routes_api():
    import os
    os.environ.setdefault("CONTROL_TOKEN", "jeton-de-test")
    os.environ.setdefault("CROSSPATCH_ARCHIVE",
                          str(tmp := Path("/tmp/crosspatch-parite.db")))
    tmp.unlink(missing_ok=True)
    import api.main as main
    return {_generique(r.path) for r in main.app.routes if hasattr(r, "path")}


def test_le_serveur_mcp_charge(serveur_mcp):
    outils = asyncio.run(serveur_mcp.server.list_tools())
    assert {t.name for t in outils} >= {
        "get_state", "get_backlog", "get_directions", "add_direction",
        "set_direction_state", "get_transcript", "set_verdict", "control"}


def test_chaque_appel_mcp_vise_un_endpoint_existant(serveur_mcp, routes_api):
    src = (ROOT / "mcp" / "server.py").read_text()
    appeles = {_generique(m) for m in re.findall(r'_(?:get|post)\(f?"([^"]+)"', src)}
    assert appeles, "aucun appel HTTP détecté dans mcp/server.py"
    manquants = appeles - routes_api
    assert not manquants, f"outils MCP sans endpoint : {manquants}"


def test_chaque_endpoint_de_pilotage_a_un_outil_mcp(routes_api, serveur_mcp):
    """`/` et `/stream` sont des chemins d'UI : le fil est exposé par
    `/transcript`, qui a bien son outil."""
    src = (ROOT / "mcp" / "server.py").read_text()
    couverts = {_generique(m) for m in re.findall(r'_(?:get|post)\(f?"([^"]+)"', src)}
    hors_perimetre = {"/", "/stream", "/openapi.json", "/docs", "/redoc",
                      "/docs/oauth2-redirect"}
    orphelins = routes_api - couverts - hors_perimetre
    assert not orphelins, f"endpoints sans outil MCP : {orphelins}"


def test_chaque_outil_documente_ce_qu_il_fait(serveur_mcp):
    outils = asyncio.run(serveur_mcp.server.list_tools())
    muets = [t.name for t in outils if not (t.description or "").strip()]
    assert not muets, muets
