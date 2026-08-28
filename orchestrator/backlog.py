"""Backlog géré par l'IA, arbitré par l'humain. États alignés sur A2A."""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import yaml

from kernel.guard import MUTABLE

# Les dossiers que `promote` recopie du candidat vers le dépôt : exactement
# la zone que le garde-fou déclare modifiable, pas un octet de plus.
MUTABLE_DIRS = tuple(m.rstrip("/") for m in MUTABLE)

# submitted → working → input-required → completed | failed | canceled
STATES = ("submitted", "working", "input_required",
          "completed", "failed", "canceled")


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return yaml.safe_load(path.read_text())["items"]


def save(path: Path, items: list[dict]) -> None:
    path.write_text(yaml.safe_dump({"items": items}, sort_keys=False,
                                   allow_unicode=True))


def _key(title: str) -> str:
    """Clé de déduplication : deux agents identiques ont les mêmes idées."""
    words = re.findall(r"[a-zà-ÿ]{4,}", title.lower())
    return " ".join(sorted(set(words))[:6])


def ingest(path: Path, raw_items: list[dict], author: str,
           gen_id: int | None, valid_dirs: set[str] | None = None,
           cap: int = 40) -> tuple[list[dict], int, int]:
    """Fusionne les idées produites en phase d'idéation.

    Renvoie (nouveaux, doublons, orphelins). Un fort taux de doublons entre A
    et B est le signal de convergence : ils ne voient plus rien de neuf.

    Une tâche sans direction active est écartée : c'est l'humain qui ouvre les
    terrains, l'IA qui les explore.
    """
    items = load(path)
    known = {_key(i["title"]) for i in items}
    seq = max((int(i["id"].split("-")[1]) for i in items), default=0)
    fresh, dupes, orphans = [], 0, 0

    for r in raw_items:
        if valid_dirs is not None and r.get("direction_id") not in valid_dirs:
            orphans += 1
            continue
        k = _key(r["title"])
        if k in known:
            dupes += 1
            continue
        known.add(k)
        seq += 1
        fresh.append({
            "id": f"IMP-{seq:03d}",
            "direction_id": r.get("direction_id"),
            "title": r["title"],
            "evidence": r.get("evidence", ""),
            "hypothesis": r["hypothesis"],
            "goal": r.get("goal", "A"),
            "expected_gain": float(r.get("expected_gain", 0.02)),
            "confidence": float(r.get("confidence", 0.5)),
            "cost": float(r.get("cost", 1.0)),
            "risk": r.get("risk", "low"),
            "state": "submitted",
            "author": author,
            "born_gen": gen_id,
        })

    items += fresh
    # Éviction : on ne garde pas indéfiniment des idées jamais retenues.
    live = [i for i in items if i["state"] not in ("completed", "canceled")]
    if len(live) > cap:
        live = rank(live)[:cap]
    save(path, live + [i for i in items if i["state"] == "completed"])
    return fresh, dupes, orphans


def close(path: Path, item_id: str, state: str) -> None:
    items = load(path)
    for i in items:
        if i["id"] == item_id:
            i["state"] = state
    save(path, items)


def rank(items: list[dict]) -> list[dict]:
    """gain × confiance / coût. L'agent peut proposer sa propre formule —
    c'est justement une amélioration de type B légitime."""
    live = [i for i in items if i["state"] in ("submitted", "working")]
    return sorted(
        live,
        key=lambda i: (i["expected_gain"] * i["confidence"]) / max(i["cost"], 0.1),
        reverse=True,
    )


def brief(item: dict) -> dict:
    return {k: item[k] for k in
            ("id", "title", "hypothesis", "expected_gain", "confidence", "goal")}


def sources(ws: Path) -> dict[str, str]:
    """Le contenu des fichiers de la zone modifiable, pas leurs noms.

    Ça n'a l'air que d'un détail de contexte, c'en est un de correction : on
    demande au proposeur un diff unifié, donc des lignes de contexte exactes.
    Tant qu'il ne recevait que l'arborescence, il inventait le code cible et
    `git apply` refusait chaque patch — pas parce que le modèle est moyen,
    mais parce que la tâche était impossible.
    """
    return {p.relative_to(ws).as_posix(): p.read_text()
            for p in sorted(ws.rglob("*.py")) if "__pycache__" not in p.parts}


def render(ctx: dict) -> str:
    return json.dumps(ctx, ensure_ascii=False, indent=2, default=str)


def diff_de(ws: Path, rel: str, apres: str) -> str:
    """Diff unifié entre le fichier tel qu'il est et la version réécrite.

    Le proposeur renvoie le fichier entier, pas un diff : un modèle milieu
    de gamme se trompe sur les compteurs de hunk *et* recopie le contexte à
    95 %, ce qui suffit à faire refuser chaque `git apply`. Ce n'est pas ce
    qu'on cherche à mesurer — on veut la qualité de l'idée, pas la fidélité
    de la transcription. Le diff est donc calculé ici, où il est exact par
    construction, et tout l'aval (garde-fou, archive, promotion) continue de
    ne voir qu'un diff. Chaîne vide si le chemin sort du workspace ou si le
    fichier réécrit est identique — dans les deux cas il n'y a rien à mesurer.
    """
    # `rel` sort du modèle : sans cette borne, un `../` suffirait à lire un
    # fichier de l'hôte et à le recopier dans le diff, donc dans l'archive
    # et dans l'UI. Le garde-fou ne passe qu'après, il est trop tard.
    cible = (ws / rel).resolve()
    if not cible.is_relative_to(ws.resolve()):
        return ""
    avant = cible.read_text() if cible.is_file() else ""
    lignes = difflib.unified_diff(avant.splitlines(True), apres.splitlines(True),
                                  f"a/{rel}", f"b/{rel}")
    # Une ligne de diff sans fin de ligne est nécessairement la dernière d'un
    # des deux côtés : `unified_diff` n'émet pas le marqueur que git attend
    # là, et sans lui le patch décrit un fichier qui n'existe pas.
    return "".join(l if l.endswith("\n") else l + "\n\\ No newline at end of file\n"
                   for l in lignes)


def apply_patch(workspace: Path, diff: str) -> tuple[bool, str]:
    """Applique un diff unifié. Renvoie (ok, stderr) — ne lève jamais.

    `check=True` faisait remonter une `CalledProcessError` jusqu'à `run()`,
    qui ne rattrape que `Budget` : le premier diff mal formé tuait le worker.
    Or un modèle milieu de gamme en produit régulièrement — c'est le cas
    normal, et le message de git est une donnée utile au tour suivant.

    `--unidiff-zero` parce que le modèle produit des offsets de ligne faux ;
    `git apply` ne réclame pas de dépôt git, un simple dossier suffit.
    """
    if not diff.endswith("\n"):
        diff += "\n"          # git apply refuse un diff sans fin de ligne
    p = subprocess.run(["git", "apply", "--unidiff-zero", "-"],
                       cwd=workspace, input=diff.encode(),
                       capture_output=True)
    return p.returncode == 0, p.stderr.decode()[-800:]


def _mirror(src: Path, dst: Path) -> None:
    """Recopie src sur dst à l'identique, y compris les suppressions.

    Remplace `rsync -a --delete` : une dépendance système de moins, et
    surtout quelque chose de testable sans conteneur.
    """
    if not src.exists():
        return
    dst.mkdir(parents=True, exist_ok=True)
    keep: set[Path] = set()
    for f in src.rglob("*"):
        if "__pycache__" in f.parts:
            continue
        rel = f.relative_to(src)
        keep.add(rel)
        out = dst / rel
        if f.is_dir():
            out.mkdir(parents=True, exist_ok=True)
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, out)
    for f in sorted(dst.rglob("*"), reverse=True):
        rel = f.relative_to(dst)
        if "__pycache__" in f.parts or rel in keep:
            continue
        shutil.rmtree(f, ignore_errors=True) if f.is_dir() else f.unlink()


def _git() -> list[str]:
    return ["git", *_identite()]


def _identite() -> list[str]:
    """L'identité du commit, passée en `-c` plutôt qu'écrite dans .git/config.

    Un conteneur frais n'a pas d'identité git : sans ça, `git commit` échoue
    sur « Please tell me who you are » et la promotion casse au moment
    précis où l'humain vient de dire oui.
    """
    nom = os.environ.get("GIT_AUTHOR_NAME", "crosspatch worker")
    mail = os.environ.get("GIT_AUTHOR_EMAIL", "worker@crosspatch.local")
    return ["-c", f"user.name={nom}", "-c", f"user.email={mail}"]


def _redact(text: str, token: str | None) -> str:
    return text.replace(token, "***") if token else text


def _https(url: str) -> str:
    """`git@host:chemin` ou `ssh://git@host/chemin` → `https://host/chemin`.

    Le conteneur n'a pas de client ssh et n'aurait aucune clé à lui donner :
    en Docker comme en cluster, la seule identité disponible est le PAT.
    Pousser sur le remote tel quel donnait « cannot run ssh », au moment
    précis où l'humain venait de dire oui.
    """
    url = url.removeprefix("ssh://")
    if url.startswith("git@"):
        host, _, chemin = url[4:].partition(":")
        return f"https://{host}/{chemin}"
    return url


def _push(root: Path, branch: str) -> None:
    """Pousse HEAD sur `branch`, en gardant le jeton hors de portée.

    Le jeton ne doit apparaître ni dans `argv` (lisible par n'importe quel
    `ps` dans le conteneur), ni dans `.git/config` (qui finirait dans une
    couche d'image). `GIT_ASKPASS` le passe par un canal que git seul lit,
    et le message d'erreur est expurgé avant d'entrer dans l'archive.
    """
    token = os.environ.get("GIT_PUSH_TOKEN")
    env = dict(os.environ)
    cible = "origin"
    if token:
        askpass = root / ".git" / "crosspatch-askpass"
        askpass.write_text('#!/bin/sh\nprintf %s "$GIT_PUSH_TOKEN"\n')
        askpass.chmod(0o700)
        env.update(GIT_ASKPASS=str(askpass), GIT_TERMINAL_PROMPT="0")
        # L'URL est calculée ici, pas écrite dans .git/config : `GIT_ASKPASS`
        # ne sert qu'en HTTPS, et le remote du dépôt est souvent en ssh.
        origine = subprocess.run(["git", "remote", "get-url", "origin"],
                                 cwd=root, capture_output=True, text=True)
        cible = _https(origine.stdout.strip()) or "origin"

    p = subprocess.run(["git", "push", cible, f"HEAD:refs/heads/{branch}"],
                       cwd=root, env=env, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"git push : {_redact(p.stderr, token)[-500:]}")


def promote(root: Path, gen_id: int, branch: str = "evolution/main") -> None:
    """Commit + push. Le redéploiement recharge le code, sans hot-patch.

    Le push vise une branche `evolution/*`, jamais `main` : le worker pousse
    du code écrit par un modèle, il ne doit pas pouvoir déplacer la branche
    de référence.
    """
    cand = root / "candidates" / str(gen_id)
    for rel in MUTABLE_DIRS:
        _mirror(cand / rel, root / rel)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run([*_git(), "commit", "-m", f"gen {gen_id} (validée)"],
                   cwd=root, check=True)
    _push(root, branch)


def find_commit(root: Path, gen_id: int) -> str | None:
    """Le commit produit par `promote` pour cette génération."""
    p = subprocess.run(
        ["git", "log", "--format=%H %s", "-n", "200"],
        cwd=root, capture_output=True, text=True, check=True)
    for line in p.stdout.splitlines():
        sha, _, subject = line.partition(" ")
        if subject.startswith(f"gen {gen_id} "):
            return sha
    return None


def rollback(root: Path, gen_id: int, branch: str = "evolution/main") -> str:
    """Annule une génération promue, par `git revert` — jamais par reset.

    L'historique reste vrai : on voit qu'une génération a été intégrée puis
    annulée, ce qui est précisément ce qu'on veut pouvoir relire.
    """
    sha = find_commit(root, gen_id)
    if sha is None:
        raise LookupError(f"aucun commit trouvé pour la génération {gen_id}")
    subprocess.run([*_git(), "revert", "--no-edit", sha], cwd=root, check=True)
    _push(root, branch)
    return sha
