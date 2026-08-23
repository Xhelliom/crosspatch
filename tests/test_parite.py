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
    # `/healthz` est de l'infrastructure, pas une fonction de pilotage :
    # elle s'adresse à kubelet et n'expose aucune information de run.
    hors_perimetre = {"/", "/stream", "/healthz", "/openapi.json", "/docs",
                      "/redoc", "/docs/oauth2-redirect"}
    orphelins = routes_api - couverts - hors_perimetre
    assert not orphelins, f"endpoints sans outil MCP : {orphelins}"


def test_chaque_outil_documente_ce_qu_il_fait(serveur_mcp):
    outils = asyncio.run(serveur_mcp.server.list_tools())
    muets = [t.name for t in outils if not (t.description or "").strip()]
    assert not muets, muets


def _chemins_ui() -> set[str]:
    """Les chemins HTTP appelés par l'UI, y compris en gabarit littéral."""
    html = (ROOT / "api" / "static" / "index.html").read_text()
    bruts = re.findall(r'(?:get|post|fetch|EventSource)\(\s*[`"\']([^`"\']*)', html)
    chemins = set()
    for b in bruts:
        b = re.sub(r"\$\{[^}]*\}", "{}", b.split("?")[0])
        if not b.startswith("/"):
            continue
        # `post("/verdict/" + id, …)` : la barre finale marque un paramètre
        # concaténé, donc le même endpoint que `/verdict/{gen_id}`.
        if b.endswith("/") and len(b) > 1:
            b += "{}"
        chemins.add(_generique(b))
    return chemins


def test_l_ui_n_appelle_que_des_endpoints_existants(routes_api):
    manquants = {c for c in _chemins_ui()
                 if c not in routes_api and c + "/{}" not in routes_api}
    assert not manquants, f"l'UI appelle des endpoints absents : {manquants}"


def test_l_ui_couvre_les_memes_fonctions_que_le_mcp(serveur_mcp):
    """Invariant 6 : ce qui se pilote depuis le téléphone se pilote aussi
    depuis une session Claude Code, et réciproquement."""
    src = (ROOT / "mcp" / "server.py").read_text()
    par_mcp = {_generique(m) for m in re.findall(r'_(?:get|post)\(f?"([^"]+)"', src)}
    par_ui = _chemins_ui()
    # `/transcript` côté MCP, `/stream` côté UI : même fil, deux transports.
    equivalents = {"/transcript", "/stream"}
    manque_ui = par_mcp - par_ui - equivalents
    manque_mcp = par_ui - par_mcp - equivalents - {"/"}
    assert not manque_ui, f"exposé au MCP mais absent de l'UI : {manque_ui}"
    assert not manque_mcp, f"exposé à l'UI mais absent du MCP : {manque_mcp}"
