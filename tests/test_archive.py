"""
Le contrat de `kernel/archive.py`, vérifié **à l'identique** sur SQLite et
sur Postgres.

Le SQL est écrit une fois et traduit : c'est exactement le genre d'endroit
où une différence de dialecte passe inaperçue jusqu'au jour où le cluster
est en route. Toute requête ajoutée à l'archive doit apparaître ici.
"""
from __future__ import annotations

import json

import pytest

from kernel.archive import Archive, Generation, est_postgres


@pytest.fixture()
def archive(adresse_archive):
    a = Archive(adresse_archive)
    yield a
    a.db.close()


def _gen(a, **kw):
    base = dict(run_id="R", role_proposer="A")
    return a.add(Generation(**{**base, **kw}))


# --- détection de dialecte -------------------------------------------------

def test_reconnait_une_dsn():
    assert est_postgres("postgresql://u@h/db")
    assert est_postgres("postgres://u@h/db")
    assert not est_postgres("data/archive.db")
    assert not est_postgres("/var/lib/crosspatch/archive.db")


# --- générations -----------------------------------------------------------

def test_add_renvoie_un_identifiant_croissant(archive):
    a, b = _gen(archive), _gen(archive)
    assert isinstance(a, int) and b > a


def test_get_renvoie_la_ligne_visee(archive):
    g1 = _gen(archive, note="première")
    _gen(archive, note="seconde")
    assert archive.get(g1)["note"] == "première"
    assert archive.get(9999) is None


def test_update_et_scores_json(archive):
    g = _gen(archive)
    archive.update(g, status="completed", scores={"pass_rate": 0.66, "delta": 0.33})
    row = archive.get(g)
    assert row["status"] == "completed"
    assert json.loads(row["scores"])["pass_rate"] == 0.66


def test_best_trie_sur_le_pass_rate_dans_le_json(archive):
    """`json_extract` n'existe pas en Postgres : c'est la requête qui casse
    en premier si la traduction de dialecte est fausse."""
    faible = _gen(archive)
    fort = _gen(archive)
    archive.update(faible, status="completed", scores={"pass_rate": 0.20})
    archive.update(fort, status="completed", scores={"pass_rate": 0.80})
    assert archive.best("R")["id"] == fort


def test_best_ignore_les_non_completes(archive):
    g = _gen(archive, status="awaiting_human")
    archive.update(g, scores={"pass_rate": 1.0})
    assert archive.best("R") is None


def test_best_supporte_un_scores_vide(archive):
    """Un `scores` à `{}` ne doit pas faire exploser le cast jsonb."""
    g = _gen(archive)
    archive.update(g, status="completed")
    assert archive.best("R")["id"] == g


def test_recent_est_antichronologique(archive):
    ids = [_gen(archive) for _ in range(3)]
    assert [r["id"] for r in archive.recent("R", 10)] == list(reversed(ids))


def test_recent_respecte_la_limite(archive):
    for _ in range(5):
        _gen(archive)
    assert len(archive.recent("R", 2)) == 2


def test_failures_ne_prend_que_les_echecs(archive):
    _gen(archive, status="completed")
    rate = _gen(archive, status="failed")
    refuse = _gen(archive, status="rejected")
    assert {r["id"] for r in archive.failures("R")} == {rate, refuse}


def test_awaiting_couvre_les_deux_gates(archive):
    g1 = _gen(archive, status="awaiting_gate")
    g2 = _gen(archive, status="awaiting_human")
    _gen(archive, status="running")
    assert [r["id"] for r in archive.awaiting("R")] == [g1, g2]


def test_acceptance_rate(archive):
    _gen(archive, status="completed")
    _gen(archive, status="failed")
    _gen(archive, role_proposer="B", status="rejected")
    taux = archive.acceptance_rate("R")
    assert taux["overall"] == pytest.approx(1 / 3)
    assert taux["A"] == pytest.approx(0.5)
    assert taux["B"] == 0.0
    assert taux["n"] == 3


def test_acceptance_rate_ignore_la_reference(archive):
    """La mesure de référence n'est la proposition de personne : la compter
    comme acceptée gonflerait le taux dès le premier tour."""
    _gen(archive, role_proposer="baseline", status="completed")
    _gen(archive, status="failed")
    taux = archive.acceptance_rate("R")
    assert taux["overall"] == 0.0
    assert taux["n"] == 1


def test_acceptance_rate_sans_donnees(archive):
    assert archive.acceptance_rate("R") == {"overall": None, "A": None,
                                            "B": None, "n": 0}


def test_plateau(archive):
    for _ in range(5):
        g = _gen(archive)
        archive.update(g, status="completed", scores={"pass_rate": 0.5})
    assert archive.plateau("R", window=5) is True


def test_plateau_faux_si_ca_bouge(archive):
    for v in (0.1, 0.4, 0.5, 0.6, 0.9):
        g = _gen(archive)
        archive.update(g, status="completed", scores={"pass_rate": v})
    assert archive.plateau("R", window=5) is False


def test_plateau_faux_si_pas_assez_de_generations(archive):
    g = _gen(archive)
    archive.update(g, status="completed", scores={"pass_rate": 0.5})
    assert archive.plateau("R", window=5) is False


# --- budget ----------------------------------------------------------------

def test_spend_cumule(archive):
    g = _gen(archive)
    archive.charge(g, "llm", 0.25)
    archive.charge(g, "sandbox", 0.10)
    assert archive.spent() == pytest.approx(0.35)


def test_spend_vide_vaut_zero(archive):
    assert archive.spent() == 0.0


def test_par_nature_ventile_et_compte(archive):
    """`n` est le nombre de microVM E2B démarrées, pas leur coût."""
    g = _gen(archive)
    archive.charge(g, "llm", 0.25)
    archive.charge(g, "sandbox", 0.10)
    archive.charge(g, "sandbox", 0.20)
    v = archive.par_nature()
    assert v["llm"] == {"n": 1, "usd": pytest.approx(0.25)}
    assert v["sandbox"] == {"n": 2, "usd": pytest.approx(0.30)}


def test_par_nature_vide(archive):
    assert archive.par_nature() == {}


def test_par_nature_filtre_sur_le_run(adresse_archive):
    """L'idéation dépense avant qu'une génération existe : `gen_id` est
    NULL, `run_id` ne l'est pas. C'est ce qui rend la facture attribuable."""
    a = Archive(adresse_archive, run_id="R1")
    a.charge(None, "llm", 0.10)          # idéation : aucune génération
    a.charge(None, "sandbox", 0.01)
    b = Archive(adresse_archive, run_id="R2")
    b.charge(None, "llm", 0.50)
    assert a.par_nature("R1") == {"llm": {"n": 1, "usd": pytest.approx(0.10)},
                                  "sandbox": {"n": 1, "usd": pytest.approx(0.01)}}
    assert a.par_nature("R2") == {"llm": {"n": 1, "usd": pytest.approx(0.50)}}
    # sans filtre : le périmètre du plafond, tous runs confondus
    assert a.par_nature()["llm"]["usd"] == pytest.approx(0.60)
    assert a.spent() == pytest.approx(0.61)
    a.db.close(); b.db.close()


# --- documents partagés ----------------------------------------------------

def test_document_absent_vaut_none(archive):
    assert archive.document("BACKLOG.yaml") is None


def test_document_ecrit_puis_relu(archive):
    archive.ecrire_document("BACKLOG.yaml", "items: []\n")
    assert archive.document("BACKLOG.yaml") == "items: []\n"


def test_document_remplace_sans_doubler(adresse_archive):
    """L'API et le worker lisent le même document : une écriture remplace,
    elle n'empile pas une seconde ligne que la lecture tirerait au hasard."""
    a = Archive(adresse_archive)
    a.ecrire_document("BACKLOG.yaml", "un")
    a.ecrire_document("BACKLOG.yaml", "deux")
    assert a.document("BACKLOG.yaml") == "deux"
    n = a.db.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
    assert n == 1
    a.db.close()


def test_le_backlog_est_propre_a_son_run(adresse_archive):
    """Les workspaces repartent neufs quand le run change ; le backlog aussi,
    sinon `known` n'est pas vide au premier tour et le run démarre en fausse
    convergence."""
    from orchestrator import backlog as bl
    a = Archive(adresse_archive, run_id="R1")
    b = Archive(adresse_archive, run_id="R2")
    bl.save(a, [{"id": "IMP-001", "title": "idee du run 1", "state": "submitted"}])
    assert bl.load(b) == []
    assert [i["id"] for i in bl.load(a)] == ["IMP-001"]
    a.db.close(); b.db.close()


# --- plan de contrôle ------------------------------------------------------

def test_control_ecrit_puis_relit(archive):
    archive.set_control("paused", "1")
    assert archive.get_control("paused") == "1"
    assert archive.flag("paused") is True


def test_control_est_idempotent(archive):
    """L'API peut réécrire la même clé : ON CONFLICT doit mettre à jour."""
    archive.set_control("paused", "1")
    archive.set_control("paused", "0")
    assert archive.flag("paused") is False
    assert archive.pending("paused") == [("paused", "0")]


def test_control_absent(archive):
    assert archive.get_control("inconnu") is None
    assert archive.get_control("inconnu", "défaut") == "défaut"
    assert archive.flag("inconnu") is False


def test_pending_filtre_par_prefixe(archive):
    archive.set_control("verdict:1", "ok")
    archive.set_control("verdict:2", "risky")
    archive.set_control("rollback:3", "1")
    assert dict(archive.pending("verdict:")) == {"verdict:1": "ok",
                                                 "verdict:2": "risky"}
    assert dict(archive.pending("rollback:")) == {"rollback:3": "1"}


def test_clear_control(archive):
    archive.set_control("verdict:1", "ok")
    archive.clear_control("verdict:1")
    assert archive.pending("verdict:") == []


# --- fil de discussion -----------------------------------------------------

def test_transcript_est_chronologique(archive):
    for i in range(3):
        archive.say(None, "A", "message", f"m{i}")
    corps = [e["body"] for e in archive.transcript()]
    assert corps == ["m0", "m1", "m2"]


def test_transcript_reprend_apres_un_curseur(archive):
    for i in range(4):
        archive.say(None, "A", "message", f"m{i}")
    tout = archive.transcript()
    suite = archive.transcript(after_id=tout[1]["id"])
    assert [e["body"] for e in suite] == ["m2", "m3"]


def test_transcript_accepte_un_gen_id_nul(archive):
    archive.say(None, "system", "error", "sans génération")
    assert archive.transcript()[0]["gen_id"] is None


def test_reference(archive):
    """`best()` peut renvoyer la référence — c'est voulu. `reference()`
    permet à l'UI de ne pas l'afficher comme un record."""
    assert archive.reference("R") is None
    g = _gen(archive, role_proposer="baseline")
    archive.update(g, status="completed", scores={"pass_rate": 0.4})
    _gen(archive, status="completed")
    assert archive.reference("R")["id"] == g


def test_pipeline_dit_ou_meurent_les_propositions(archive):
    """Un acceptance_rate à 0 ne dit pas si les idées sont mauvaises ou si
    aucun patch n'atteint le harness."""
    g = _gen(archive, role_proposer="baseline")
    archive.update(g, status="completed", scores={"pass_rate": 0.4})
    archive.update(_gen(archive), status="failed", note="git apply : corrupt patch")
    archive.update(_gen(archive), status="failed", note="crash du harness : boum")
    archive.update(_gen(archive), status="failed", note="régression")
    archive.update(_gen(archive), status="rejected", note="chemin protégé")
    archive.update(_gen(archive), status="completed", note="")
    archive.update(_gen(archive), status="awaiting_human", note="")

    p = archive.pipeline("R")

    assert p == {"proposees": 6, "inapplicables": 1, "harness_casse": 1,
                 "regressions": 1, "refusees": 1, "acceptees": 1,
                 "en_attente": 1}
