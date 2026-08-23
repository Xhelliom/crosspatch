"""
Bug bloquant n°1 : l'API et le worker sont deux conteneurs sans mémoire
partagée. L'API écrit des intentions en base, le worker les exécute.

Ces tests vérifient les deux moitiés séparément, comme en production :
jamais un objet `Loop` n'est visible depuis l'API.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("CROSSPATCH_ARCHIVE", str(tmp_path / "archive.db"))
    monkeypatch.setenv("CONTROL_TOKEN", "jeton-de-test")
    for mod in ("api.main",):
        import sys
        sys.modules.pop(mod, None)
    from fastapi.testclient import TestClient
    import api.main as main
    return TestClient(main.app), main.archive


H = {"Authorization": "Bearer jeton-de-test"}


def test_auth_exigee(api):
    client, _ = api
    assert client.get("/state").status_code == 401


def test_pause_et_reprise_passent_par_la_base(api):
    client, archive = api
    assert client.get("/state", headers=H).json()["paused"] is False
    assert client.post("/control", json={"action": "pause"}, headers=H).status_code == 200
    assert archive.flag("paused") is True
    assert client.get("/state", headers=H).json()["paused"] is True
    client.post("/control", json={"action": "resume"}, headers=H)
    assert archive.flag("paused") is False


def test_stop_est_persistant(api):
    client, archive = api
    client.post("/control", json={"action": "stop"}, headers=H)
    assert archive.flag("stop") is True
    assert client.get("/state", headers=H).json()["stopped"] is True


def test_action_inconnue(api):
    client, _ = api
    assert client.post("/control", json={"action": "danser"},
                       headers=H).status_code == 400


def test_verdict_est_mis_en_file_pas_execute(api):
    from kernel.archive import Generation
    client, archive = api
    gen_id = archive.add(Generation(run_id="r", role_proposer="A",
                                    status="awaiting_human", diff="x"))
    r = client.post(f"/verdict/{gen_id}", json={"verdict": "ok"}, headers=H)
    assert r.status_code == 200 and r.json()["queued"] is True
    # L'API n'a rien exécuté : le statut ne bouge qu'une fois le worker passé.
    assert archive.get(gen_id)["status"] == "awaiting_human"
    assert archive.pending("verdict:") == [(f"verdict:{gen_id}", "ok")]


def test_verdict_sur_generation_inconnue(api):
    client, _ = api
    assert client.post("/verdict/4242", json={"verdict": "ok"},
                       headers=H).status_code == 404


def test_verdict_sur_mauvais_etat(api):
    from kernel.archive import Generation
    client, archive = api
    gen_id = archive.add(Generation(run_id="r", role_proposer="A", status="running"))
    assert client.post(f"/verdict/{gen_id}", json={"verdict": "ok"},
                       headers=H).status_code == 409


def test_state_distingue_gate_et_promote(api):
    from kernel.archive import Generation
    client, archive = api
    import yaml
    run = yaml.safe_load((ROOT / "config.yaml").read_text())["run_id"]
    g1 = archive.add(Generation(run_id=run, role_proposer="A", status="awaiting_gate"))
    g2 = archive.add(Generation(run_id=run, role_proposer="B", status="awaiting_human"))
    waiting = client.get("/state", headers=H).json()["awaiting_human"]
    stages = {w["id"]: w["stage"] for w in waiting}
    assert stages == {g1: "gate", g2: "promote"}


def test_rollback_refuse_une_generation_non_integree(api):
    from kernel.archive import Generation
    client, archive = api
    gen_id = archive.add(Generation(run_id="r", role_proposer="A", status="failed"))
    r = client.post("/control", json={"action": "rollback", "gen_id": gen_id},
                    headers=H)
    assert r.status_code == 409


def test_rollback_est_mis_en_file(api):
    from kernel.archive import Generation
    client, archive = api
    gen_id = archive.add(Generation(run_id="r", role_proposer="A", status="completed"))
    r = client.post("/control", json={"action": "rollback", "gen_id": gen_id},
                    headers=H)
    assert r.status_code == 200
    assert archive.pending("rollback:") == [(f"rollback:{gen_id}", "1")]
