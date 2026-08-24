"""
La moitié worker du plan de contrôle : le `Loop` consomme les intentions
déposées en base par l'API, et lui seul exécute quoi que ce soit.

Inclut la régression du bug n°5 : le verdict doit porter sur la génération
visée, pas sur la plus récente.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", ".venv", "data", "workspaces", "candidates",
        "__pycache__", ".pytest_cache"}


@pytest.fixture()
def loop(adresse_archive, tmp_path, monkeypatch):
    """Un Loop complet sur une copie jetable du dépôt, sans LLM ni sandbox."""
    root = tmp_path / "repo"
    root.mkdir()
    for item in ROOT.iterdir():
        if item.name in SKIP:
            continue
        (shutil.copytree if item.is_dir() else shutil.copy2)(item, root / item.name)

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("CROSSPATCH_ARCHIVE", adresse_archive)

    import orchestrator.loop as L
    monkeypatch.setattr(L, "ROOT", root)
    monkeypatch.setattr(L, "BACKLOG", root / "mission" / "BACKLOG.yaml")
    monkeypatch.setattr(L, "DIRS", root / "mission" / "DIRECTIONS.yaml")

    cfg = yaml.safe_load((root / "config.yaml").read_text())
    lo = L.Loop(cfg)
    return lo, L, root


def _gen(lo, **kw):
    from kernel.archive import Generation
    base = dict(run_id=lo.run_id, role_proposer="A", diff="diff-bidon")
    return lo.archive.add(Generation(**{**base, **kw}))


def test_workspaces_couvrent_toute_la_zone_modifiable(loop):
    lo, _, _ = loop
    for role in ("A", "B"):
        for rel in ("orchestrator", "api", "mcp"):
            assert (lo.ws[role] / rel).is_dir(), f"{role}/{rel} manquant"
    assert (lo.ws["A"] / "orchestrator" / "agent.py").exists()


def test_pause_et_stop_viennent_de_la_base(loop):
    lo, _, _ = loop
    assert lo.paused is False and lo.stop is False
    lo.archive.set_control("paused", "1")
    lo.archive.set_control("stop", "1")
    assert lo.paused is True and lo.stop is True


def test_verdict_ok_sur_gate_declenche_l_evaluation(loop, monkeypatch):
    lo, _, _ = loop
    gen_id = _gen(lo, status="awaiting_gate")
    vus = []
    monkeypatch.setattr(lo, "_evaluate",
                        lambda g, t, d: vus.append((g, t, d)))
    lo.archive.set_control(f"verdict:{gen_id}", "ok")
    lo._drain_control()
    assert vus == [(gen_id, "B", "diff-bidon")]      # A propose, B est la cible
    assert lo.archive.pending("verdict:") == []      # intention consommée


def test_verdict_ok_sur_promote_integre(loop, monkeypatch):
    lo, L, root = loop
    gen_id = _gen(lo, status="awaiting_human", item_id="IMP-001")
    (root / "mission" / "BACKLOG.yaml").write_text(yaml.safe_dump(
        {"items": [{"id": "IMP-001", "title": "t", "state": "submitted"}]}))
    pousses = []
    monkeypatch.setattr(L.bl, "promote",
                        lambda r, g, branch: pousses.append((g, branch)))
    monkeypatch.setattr(lo, "_adopt", lambda g, t: None)
    lo.archive.set_control(f"verdict:{gen_id}", "ok")
    lo._drain_control()
    assert lo.archive.get(gen_id)["status"] == "completed"
    assert pousses == [(gen_id, f"evolution/{lo.run_id}")]
    items = yaml.safe_load((root / "mission" / "BACKLOG.yaml").read_text())["items"]
    assert items[0]["state"] == "completed"


def test_verdict_negatif_rejette_et_ferme_l_item(loop):
    lo, _, root = loop
    gen_id = _gen(lo, status="awaiting_human", item_id="IMP-002")
    (root / "mission" / "BACKLOG.yaml").write_text(yaml.safe_dump(
        {"items": [{"id": "IMP-002", "title": "t", "state": "submitted"}]}))
    lo.archive.set_control(f"verdict:{gen_id}", "scope")
    lo._drain_control()
    assert lo.archive.get(gen_id)["status"] == "rejected"
    assert lo.archive.get(gen_id)["human_verdict"] == "scope"
    items = yaml.safe_load((root / "mission" / "BACKLOG.yaml").read_text())["items"]
    assert items[0]["state"] == "failed"


def test_bug5_le_verdict_vise_la_bonne_generation(loop, monkeypatch):
    """Régression : `recent(...)[0]` prenait la dernière ligne insérée."""
    lo, L, _ = loop
    vise = _gen(lo, status="awaiting_human", item_id="IMP-VISE")
    _gen(lo, role_proposer="B", status="proposed", item_id="IMP-AUTRE")  # plus récente
    fermes = []
    monkeypatch.setattr(L.bl, "close",
                        lambda path, item, state: fermes.append((item, state)))
    lo.archive.set_control(f"verdict:{vise}", "useless")
    lo._drain_control()
    assert fermes == [("IMP-VISE", "failed")]


def test_verdict_sur_generation_inconnue_ne_bloque_pas_la_file(loop):
    lo, _, _ = loop
    lo.archive.set_control("verdict:9999", "ok")
    lo._drain_control()
    assert lo.archive.pending("verdict:") == []
    assert any("inconnue" in e["body"] for e in lo.archive.transcript())


def test_rollback_est_consomme(loop, monkeypatch):
    lo, L, _ = loop
    gen_id = _gen(lo, status="completed")
    appels = []
    monkeypatch.setattr(L.bl, "rollback",
                        lambda r, g, t: appels.append((g, t)) or "abc123")
    lo.archive.set_control(f"rollback:{gen_id}", "1")
    lo._drain_control()
    assert appels == [(gen_id, "1")]
    assert lo.archive.pending("rollback:") == []


# --- résilience : un tour raté ne tue pas le worker ------------------------

def test_un_tour_rate_est_archive_et_la_boucle_continue(loop, monkeypatch):
    """`chat_json` peut lever si le modèle rate le JSON strict deux fois.

    CLAUDE.md le dit : « on marque la génération failed, c'est une donnée sur
    le modèle ». Encore faut-il que le worker soit encore là pour le lire.
    """
    import json
    lo, _, _ = loop
    tours = []

    def ideate_qui_rate(p, t):
        tours.append(p)
        raise json.JSONDecodeError("pas du JSON", "", 0)

    monkeypatch.setattr(lo, "_ideate", ideate_qui_rate)
    monkeypatch.setattr(lo, "_turn", lambda p, t: None)
    lo.run()

    from orchestrator.loop import MAX_ECHECS
    assert len(tours) == MAX_ECHECS               # s'arrête, ne boucle pas à vide
    fil = [e["body"] for e in lo.archive.transcript()]
    assert any("tour 0 raté (JSONDecodeError)" in b for b in fil)
    assert any("tours ratés d'affilée" in b for b in fil)


def test_le_compteur_d_echecs_se_remet_a_zero(loop, monkeypatch):
    import json
    lo, _, _ = loop
    appels = []

    def ideate(p, t):
        appels.append(p)
        if len(appels) in (1, 3):
            raise json.JSONDecodeError("raté", "", 0)
        if len(appels) >= 5:
            lo.archive.set_control("stop", "1")

    monkeypatch.setattr(lo, "_ideate", ideate)
    monkeypatch.setattr(lo, "_turn", lambda p, t: None)
    lo.run()
    assert len(appels) >= 5      # deux échecs non consécutifs n'arrêtent rien


def test_un_arret_demande_est_respecte(loop):
    lo, _, _ = loop
    lo.archive.set_control("stop", "1")
    lo.run()                      # ne doit pas boucler
    assert any("Arrêt demandé" in e["body"] for e in lo.archive.transcript())


# --- le coupe-circuit agit avant chaque dépense, pas au tour suivant -------

def test_attendre_reprise_bloque_puis_repart(loop, monkeypatch):
    lo, L, _ = loop
    lo.archive.set_control("paused", "1")
    dodos = []

    def faux_sleep(_):
        dodos.append(1)
        if len(dodos) == 2:
            lo.archive.set_control("paused", "0")   # l'humain reprend

    monkeypatch.setattr(L.time, "sleep", faux_sleep)
    assert lo._attendre_reprise() is True
    assert len(dodos) == 2


def test_attendre_reprise_rend_la_main_sur_arret(loop, monkeypatch):
    lo, L, _ = loop
    lo.archive.set_control("paused", "1")
    monkeypatch.setattr(L.time, "sleep",
                        lambda _: lo.archive.set_control("stop", "1"))
    assert lo._attendre_reprise() is False


def test_attendre_reprise_ne_bloque_pas_au_repos(loop):
    lo, _, _ = loop
    assert lo._attendre_reprise() is True


def test_l_ideation_ne_depense_rien_en_pause(loop, monkeypatch):
    """La pause doit couper avant l'appel de modèle, pas après."""
    lo, L, _ = loop
    appels = []
    monkeypatch.setattr(lo.llm, "chat_json",
                        lambda *a, **k: appels.append(1) or {})
    monkeypatch.setattr(lo, "_attendre_reprise", lambda: False)
    lo._ideate("A", "B")
    assert appels == []


def test_la_proposition_ne_depense_rien_en_pause(loop, monkeypatch):
    lo, _, _ = loop
    appels = []
    monkeypatch.setattr(lo.llm, "chat_json",
                        lambda *a, **k: appels.append(1) or {})
    monkeypatch.setattr(lo, "_attendre_reprise", lambda: False)
    lo._turn("A", "B")
    assert appels == []


def test_un_soak_interrompu_n_est_pas_archive_comme_resultat(loop, monkeypatch):
    """Un score partiel n'est pas comparable aux autres générations."""
    lo, L, root = loop
    gen_id = _gen(lo, status="running")
    (root / "candidates").mkdir(exist_ok=True)
    # Le sujet du test est l'interruption du soak, pas l'application du patch.
    monkeypatch.setattr(L.bl, "apply_patch", lambda ws, diff: (True, ""))
    monkeypatch.setattr(lo, "_attendre_reprise", lambda: True)
    monkeypatch.setattr(type(lo.runner), "soak",
                        lambda self, ws, h, n, gen_id=None, avant_chaque=None:
                        [_Eval()])          # un seul run sur les trois demandés
    lo._evaluate(gen_id, "B", "--- a/x\n+++ b/x\n")
    row = lo.archive.get(gen_id)
    assert row["status"] == "failed"
    assert "interrompu" in row["note"]


class _Eval:
    pass_rate = 1.0
    tokens_per_task = 0.0
    wall_time_p50 = 0.0
    failures: list = []
    crashed = False
