"""
Le dépôt est public. Ce test est le filet : il échoue si une vraie clé se
retrouve dans un fichier suivi par git.

Il ne remplace pas `.gitignore` — il attrape le cas que `.gitignore` ne
couvre pas : une clé collée en dur dans du code ou dans un exemple.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Préfixes réels des fournisseurs, avec assez de caractères pour ne pas
# confondre avec les placeholders de `.env.example` (`sk-or-...`).
FUITES = {
    "clé OpenRouter": re.compile(r"sk-or-(?:v\d-)?[A-Za-z0-9]{20,}"),
    "clé E2B": re.compile(r"\be2b_[A-Za-z0-9]{20,}"),
    "PAT GitHub": re.compile(r"\b(?:ghp|gho|ghs|ghr)_[A-Za-z0-9]{30,}"),
    "PAT GitHub fine-grained": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}"),
    "clé Anthropic": re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}"),
    "clé OpenAI": re.compile(r"\bsk-proj-[A-Za-z0-9\-_]{20,}"),
}

BINAIRES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".pdf"}


def _fichiers_suivis() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [ROOT / n for n in out.split("\0") if n]


def test_aucune_cle_dans_les_fichiers_suivis():
    trouve = []
    for f in _fichiers_suivis():
        if f.suffix.lower() in BINAIRES or not f.is_file():
            continue
        texte = f.read_text(encoding="utf-8", errors="ignore")
        for quoi, motif in FUITES.items():
            for m in motif.finditer(texte):
                ligne = texte[:m.start()].count("\n") + 1
                trouve.append(f"{f.relative_to(ROOT)}:{ligne} — {quoi}")
    assert not trouve, "secrets dans des fichiers suivis :\n" + "\n".join(trouve)


def test_le_fichier_env_n_est_pas_suivi():
    suivis = {f.name for f in _fichiers_suivis()}
    assert ".env" not in suivis
    assert ".env.example" in suivis      # le gabarit, lui, doit rester


def test_gitignore_couvre_l_etat_local():
    ignore = (ROOT / ".gitignore").read_text()
    for motif in (".env", "data/", "workspaces/", "candidates/", "*.db"):
        assert motif in ignore, f"{motif} absent de .gitignore"


def test_dockerignore_n_embarque_pas_l_etat_local():
    """L'image part sur un registre : ni `.env`, ni archive, ni workspaces."""
    ignore = (ROOT / ".dockerignore").read_text()
    for motif in (".env", "data/", "workspaces/", "candidates/"):
        assert motif in ignore, f"{motif} absent de .dockerignore"


def test_env_example_ne_contient_que_des_gabarits():
    exemple = (ROOT / ".env.example").read_text()
    for quoi, motif in FUITES.items():
        assert not motif.search(exemple), f"{quoi} réelle dans .env.example"


# --- le jeton de push ne doit fuiter nulle part ----------------------------

def test_le_jeton_de_push_ne_passe_pas_par_argv(tmp_path, monkeypatch):
    """Un jeton dans la ligne de commande est lisible par tout `ps` du
    conteneur, et un jeton dans .git/config finit dans une couche d'image."""
    from orchestrator import backlog as bl

    vus = {}

    def faux_run(cmd, **kw):
        vus["cmd"] = cmd
        vus["env"] = kw.get("env") or {}
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bl.subprocess, "run", faux_run)
    monkeypatch.setenv("GIT_PUSH_TOKEN", "github_pat_SECRETSECRETSECRETSECRET")
    depot = tmp_path / "d"
    (depot / ".git").mkdir(parents=True)

    bl._push(depot, "evolution/run")

    assert not any("SECRET" in str(a) for a in vus["cmd"]), vus["cmd"]
    assert vus["env"].get("GIT_ASKPASS", "").endswith("crosspatch-askpass")
    assert vus["env"].get("GIT_TERMINAL_PROMPT") == "0"
    aide = (depot / ".git" / "crosspatch-askpass").read_text()
    assert "SECRET" not in aide          # le script lit l'env, il ne le contient pas


def test_l_erreur_de_push_est_expurgee(tmp_path, monkeypatch):
    """Le message d'erreur de git part dans l'archive, donc dans l'UI."""
    from orchestrator import backlog as bl
    import pytest

    monkeypatch.setattr(bl.subprocess, "run", lambda cmd, **kw:
                        subprocess.CompletedProcess(
                            cmd, 128, "",
                            "fatal: https://github_pat_SECRETSECRETSECRETSECRET@github.com refusé"))
    monkeypatch.setenv("GIT_PUSH_TOKEN", "github_pat_SECRETSECRETSECRETSECRET")
    depot = tmp_path / "d"
    (depot / ".git").mkdir(parents=True)

    with pytest.raises(RuntimeError) as e:
        bl._push(depot, "evolution/run")
    assert "SECRET" not in str(e.value)
    assert "***" in str(e.value)


def test_push_sans_jeton_reste_possible(tmp_path, monkeypatch):
    """En local, l'identification passe par la config git de la machine."""
    from orchestrator import backlog as bl
    vus = {}

    def faux_run(cmd, **kw):
        vus["env"] = kw.get("env") or {}
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bl.subprocess, "run", faux_run)
    monkeypatch.delenv("GIT_PUSH_TOKEN", raising=False)
    depot = tmp_path / "d"
    (depot / ".git").mkdir(parents=True)
    bl._push(depot, "evolution/run")
    # On n'installe pas notre askpass, et on n'écrase pas celui de la machine
    # s'il en a un : sans jeton, l'identification reste celle de l'hôte.
    assert not (depot / ".git" / "crosspatch-askpass").exists()
    assert "crosspatch-askpass" not in vus["env"].get("GIT_ASKPASS", "")
