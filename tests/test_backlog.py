"""
Application de patch, promotion et annulation.

Le point sensible est `apply_patch` : un diff mal formé est le cas *normal*
avec un modèle milieu de gamme, et il ne doit jamais faire remonter
d'exception jusqu'à la boucle.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from orchestrator import backlog as bl

DIFF_OK = """--- a/orchestrator/agent.py
+++ b/orchestrator/agent.py
@@ -1 +1 @@
-ancien
+nouveau
"""


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "orchestrator").mkdir(parents=True)
    (ws / "orchestrator" / "agent.py").write_text("ancien\n")
    return ws


def test_apply_patch_hors_depot_git(tmp_path):
    """`git apply` ne réclame pas de dépôt : un dossier nu suffit."""
    ws = _ws(tmp_path)
    assert not (ws / ".git").exists()
    ok, err = bl.apply_patch(ws, DIFF_OK)
    assert ok, err
    assert (ws / "orchestrator" / "agent.py").read_text() == "nouveau\n"


def test_apply_patch_diff_casse_ne_leve_pas(tmp_path):
    ok, err = bl.apply_patch(_ws(tmp_path), "ceci n'est pas un diff\n")
    assert ok is False
    assert err  # le message de git repart en contexte au tour suivant


def test_apply_patch_cible_absente_ne_leve_pas(tmp_path):
    diff = DIFF_OK.replace("agent.py", "inexistant.py")
    ok, err = bl.apply_patch(_ws(tmp_path), diff)
    assert ok is False and err


def test_apply_patch_sans_saut_de_ligne_final(tmp_path):
    ok, err = bl.apply_patch(_ws(tmp_path), DIFF_OK.rstrip("\n"))
    assert ok, err


def test_apply_patch_cree_un_fichier(tmp_path):
    diff = ("--- /dev/null\n+++ b/orchestrator/outil.py\n"
            "@@ -0,0 +1 @@\n+def f(): ...\n")
    ok, err = bl.apply_patch(_ws(tmp_path), diff)
    assert ok, err
    assert (tmp_path / "ws" / "orchestrator" / "outil.py").exists()


# --- miroir (remplace rsync -a --delete) -----------------------------------

def test_mirror_copie_ajoute_et_supprime(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "sous").mkdir(parents=True)
    (src / "a.py").write_text("A")
    (src / "sous" / "b.py").write_text("B")
    (dst / "sous").mkdir(parents=True)
    (dst / "a.py").write_text("vieux")
    (dst / "obsolete.py").write_text("à supprimer")
    bl._mirror(src, dst)
    assert (dst / "a.py").read_text() == "A"
    assert (dst / "sous" / "b.py").read_text() == "B"
    assert not (dst / "obsolete.py").exists()


def test_mirror_ignore_pycache(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "__pycache__").mkdir(parents=True)
    (src / "__pycache__" / "x.pyc").write_bytes(b"\x00")
    (src / "a.py").write_text("A")
    bl._mirror(src, dst)
    assert (dst / "a.py").exists() and not (dst / "__pycache__").exists()


# --- promotion et annulation ----------------------------------------------

@pytest.fixture()
def depot(tmp_path):
    """Un dépôt local avec un remote nu : `push` doit marcher pour de vrai."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    root = tmp_path / "root"
    (root / "orchestrator").mkdir(parents=True)
    (root / "orchestrator" / "agent.py").write_text("v1\n")
    for cmd in (["git", "init", "-q", "-b", "main"],
                ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"],
                ["git", "remote", "add", "origin", str(remote)],
                ["git", "add", "-A"],
                ["git", "commit", "-qm", "base"]):
        subprocess.run(cmd, cwd=root, check=True)
    cand = root / "candidates" / "7" / "orchestrator"
    cand.mkdir(parents=True)
    (cand / "agent.py").write_text("v2\n")
    return root, remote


def test_promote_pousse_sur_evolution_jamais_sur_main(depot):
    root, remote = depot
    bl.promote(root, 7, branch="evolution/2026-08-23-001")
    assert (root / "orchestrator" / "agent.py").read_text() == "v2\n"
    refs = subprocess.run(["git", "branch", "--format=%(refname:short)"],
                          cwd=remote, capture_output=True, text=True).stdout.split()
    assert refs == ["evolution/2026-08-23-001"], refs


def test_rollback_revert_et_repousse(depot):
    root, _ = depot
    bl.promote(root, 7, branch="evolution/run")
    sha = bl.rollback(root, 7, branch="evolution/run")
    assert sha
    assert (root / "orchestrator" / "agent.py").read_text() == "v1\n"
    log = subprocess.run(["git", "log", "--format=%s"], cwd=root,
                         capture_output=True, text=True).stdout
    assert log.startswith('Revert "gen 7 (validée)"')


def test_rollback_sans_commit_correspondant(depot):
    root, _ = depot
    with pytest.raises(LookupError):
        bl.rollback(root, 999)


# --- ingestion du backlog --------------------------------------------------

def test_ingest_ecarte_les_taches_hors_direction(tmp_path):
    path = tmp_path / "BACKLOG.yaml"
    path.write_text(yaml.safe_dump({"items": []}))
    fresh, dupes, orphans = bl.ingest(
        path,
        [{"title": "Rejouer les tests avant de rendre", "hypothesis": "h",
          "direction_id": "DIR-001"},
         {"title": "Refaire le CSS de l'UI", "hypothesis": "h",
          "direction_id": "DIR-404"}],
        "A", None, valid_dirs={"DIR-001"})
    assert len(fresh) == 1 and orphans == 1 and dupes == 0
    assert fresh[0]["id"] == "IMP-001"


def test_ingest_dedoublonne(tmp_path):
    path = tmp_path / "BACKLOG.yaml"
    path.write_text(yaml.safe_dump({"items": []}))
    idea = {"title": "Relire les tests avant de rendre la solution",
            "hypothesis": "h", "direction_id": "DIR-001"}
    bl.ingest(path, [idea], "A", None, valid_dirs={"DIR-001"})
    fresh, dupes, _ = bl.ingest(path, [idea], "B", None, valid_dirs={"DIR-001"})
    assert fresh == [] and dupes == 1


def test_promote_sans_identite_git_configuree(tmp_path, monkeypatch):
    """Un conteneur frais n'a ni user.name ni user.email.

    Sans identité passée en `-c`, `git commit` échoue sur « Please tell me
    who you are » — au moment précis où l'humain vient de valider.
    """
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    root = tmp_path / "root"
    (root / "orchestrator").mkdir(parents=True)
    (root / "orchestrator" / "agent.py").write_text("v1\n")

    # Environnement vierge : aucune config git globale, aucune injectée.
    faux_home = tmp_path / "home"
    faux_home.mkdir()
    for k in list(os.environ):
        if k.startswith("GIT_CONFIG"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(faux_home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(faux_home / "absent"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(faux_home / "absent"))
    monkeypatch.delenv("GIT_PUSH_TOKEN", raising=False)

    for cmd in (["git", "init", "-q", "-b", "main"],
                ["git", "remote", "add", "origin", str(remote)]):
        subprocess.run(cmd, cwd=root, check=True)
    # Le commit de base doit lui aussi passer sans identité : on le fait via
    # la même mécanique, sinon le dépôt de test n'a pas de HEAD.
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run([*bl._git(), "commit", "-qm", "base"], cwd=root, check=True)

    cand = root / "candidates" / "3" / "orchestrator"
    cand.mkdir(parents=True)
    (cand / "agent.py").write_text("v2\n")

    bl.promote(root, 3, branch="evolution/run")     # ne doit pas lever

    auteur = subprocess.run(["git", "log", "-1", "--format=%an <%ae>"], cwd=root,
                            capture_output=True, text=True).stdout.strip()
    assert auteur == "crosspatch worker <worker@crosspatch.local>"


def test_sources_donne_le_contenu_pas_les_noms(tmp_path):
    """Le proposeur doit écrire un diff unifié : sans le contenu réel, il
    invente les lignes de contexte et `git apply` refuse chaque patch. Ce
    test verrouille la cause du premier run où aucune génération n'a pu
    être mesurée."""
    ws = _ws(tmp_path)
    (ws / "orchestrator" / "__pycache__").mkdir()
    (ws / "orchestrator" / "__pycache__" / "agent.py").write_text("compilé\n")

    src = bl.sources(ws)

    assert src == {"orchestrator/agent.py": "ancien\n"}


def test_diff_de_est_applicable(tmp_path):
    """Le proposeur renvoie le fichier entier : le diff qu'on en tire doit
    passer `git apply`, là où le modèle échouait à l'écrire lui-même."""
    ws = _ws(tmp_path)
    diff = bl.diff_de(ws, "orchestrator/agent.py", "ancien\nnouveau\n")

    ok, err = bl.apply_patch(ws, diff)

    assert ok, err
    assert (ws / "orchestrator" / "agent.py").read_text() == "ancien\nnouveau\n"


def test_diff_de_sans_saut_de_ligne_final(tmp_path):
    """`unified_diff` ne sait pas écrire « \\ No newline at end of file » :
    sans normalisation le diff produit est refusé par git."""
    ws = _ws(tmp_path)
    (ws / "orchestrator" / "agent.py").write_text("ancien")

    ok, err = bl.apply_patch(ws, bl.diff_de(ws, "orchestrator/agent.py", "neuf"))

    assert ok, err


def test_diff_de_refuse_de_sortir_du_workspace(tmp_path):
    """`path` sort du modèle : un `../` recopierait un fichier de l'hôte
    dans le diff, donc dans l'archive et l'UI. Le garde-fou passe après."""
    (tmp_path / "secret.txt").write_text("clé\n")
    ws = _ws(tmp_path)

    assert bl.diff_de(ws, "../secret.txt", "autre\n") == ""


def test_diff_de_fichier_inchange_ne_produit_rien(tmp_path):
    """Un fichier réécrit à l'identique est un tour sans proposition."""
    ws = _ws(tmp_path)

    assert bl.diff_de(ws, "orchestrator/agent.py", "ancien\n") == ""


def test_https_convertit_les_remotes_ssh():
    """Le conteneur n'a pas de client ssh et aucune clé à lui donner : sans
    conversion, `promote` meurt sur « cannot run ssh » juste après le oui."""
    assert bl._https("git@github.com:o/r.git") == "https://github.com/o/r.git"
    assert bl._https("ssh://git@gitlab.com:o/r.git") == "https://gitlab.com/o/r.git"
    # Déjà en HTTPS, ou chemin local : on n'y touche pas.
    assert bl._https("https://github.com/o/r.git") == "https://github.com/o/r.git"
    assert bl._https("/tmp/remote.git") == "/tmp/remote.git"


def test_push_avec_jeton_vise_le_remote_converti(depot, monkeypatch):
    """Le jeton implique HTTPS : `GIT_ASKPASS` ne sert à rien en ssh."""
    root, remote = depot
    subprocess.run(["git", "remote", "set-url", "origin",
                    "git@github.com:o/r.git"], cwd=root, check=True)
    monkeypatch.setenv("GIT_PUSH_TOKEN", "jeton-bidon")
    vus = {}

    vrai = subprocess.run

    def espion(cmd, **kw):
        if cmd[:2] == ["git", "push"]:
            vus["cible"] = cmd[2]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return vrai(cmd, **kw)

    monkeypatch.setattr(bl.subprocess, "run", espion)
    bl.promote(root, 7, branch="evolution/x")

    assert vus["cible"] == "https://github.com/o/r.git"
