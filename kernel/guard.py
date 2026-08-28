"""
NOYAU IMMUABLE. Aucun agent ne peut proposer de patch sur ce fichier.

C'est le seul endroit qui décide ce qui est modifiable. Si tu ne devais
relire qu'un fichier avant de lancer un run autonome, c'est celui-ci.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

# Chemins que les agents ne peuvent jamais modifier, même indirectement.
PROTECTED = (
    "kernel/",
    "harness/",
    "mission/OBJECTIVE.md",
    "mission/DIRECTIONS.yaml",
    ".github/",
    "Dockerfile",
    "docker-compose.yml",
    ".env",
)

# Zone explicitement ouverte à l'auto-amélioration (Goal B).
MUTABLE = (
    "orchestrator/",
    "api/",
    "mcp/",
)

# Motifs qui trahissent une tentative de contournement plutôt qu'une
# amélioration. Volontairement grossiers : un faux positif coûte un tour,
# un faux négatif coûte l'expérience entière.
#
# Resserrés, jamais supprimés. Les versions larges (`subprocess`, `eval\(`,
# `BUDGET` insensible à la casse) remontaient en revue humaine des patches
# parfaitement légitimes — au point que tout y passait, ce qui revient à
# n'avoir plus de filtre du tout :
#
#   - `subprocess` : DIR-001 demande explicitement de donner à l'agent un
#     retour d'exécution avant qu'il rende sa réponse. Ça *impose* de lancer
#     un processus. Ce qui est dangereux n'est pas l'appel, c'est la chaîne
#     shell interprétée — donc `shell=True`, pas `subprocess`.
#   - `eval\(` attrapait `ast.literal_eval(`, qui est la parade, pas l'abus.
#   - `BUDGET` en IGNORECASE attrapait toute prose française contenant
#     « budget » — le sujet même de DIR-002.
# Chaque motif porte la phrase que l'humain lira au moment de trancher.
# Ce n'est pas de la décoration : la question arrive sur un téléphone, et
# `motifs à revoir : ['^[+-].*\\b(?:AGENT_)?MODEL\\b']` n'est pas une
# question — c'est un `repr` de regex. On ne peut pas répondre oui ou non à
# ça sans aller lire le diff à la main, ce qui est exactement ce que le
# pilotage au téléphone ne permet pas.
SUSPICIOUS = (
    (re.compile(r"\bPROTECTED\b"),
     "touche à la liste des chemins protégés"),
    (re.compile(r"\bpass_rate\s*=\s*[\d.]"),
     "écrit un score en dur"),
    (re.compile(r"\bos\.system\s*\("),
     "lance une commande système"),
    (re.compile(r"shell\s*=\s*True"),
     "exécute une chaîne shell interprétée"),
    (re.compile(r"(?<![\w.])(?:eval|exec)\s*\("),
     "évalue du code à la volée (eval/exec)"),
    (re.compile(r"\b(?:BUDGET|max_usd|max_spend)\b"),
     "touche au plafond de dépense"),
    (re.compile(r"\.\./"),
     "remonte hors de son répertoire (../)"),
    (re.compile(r"\bCONTROL_TOKEN\b"),
     "touche au jeton de contrôle"),
    # Le modèle des agents est milieu de gamme *délibérément* : le headroom
    # est le sujet de l'expérience. Un patch d'une ligne sur `MODEL` ferait
    # bondir le pass_rate sans rien améliorer. Non interdit — c'est une
    # décision humaine, pas la leur — mais jamais silencieux.
    # Ancré sur `^[+-]` : `agent.py` contient déjà `"model": MODEL`, et sans
    # cet ancrage tout patch de `_complete` remonterait sur son contexte.
    (re.compile(r"^[+-].*\b(?:AGENT_)?MODEL\b", re.M),
     "touche au choix du modèle de l'agent"),
)


@dataclass
class Verdict:
    ok: bool
    reason: str = ""
    risk: str = "low"          # low | medium | high
    needs_human: bool = False


def classify(paths: list[str], diff_text: str) -> Verdict:
    """Décide si un patch est recevable, et s'il doit remonter à l'humain."""
    for p in paths:
        norm = p.lstrip("./")
        if any(norm.startswith(x) for x in PROTECTED):
            return Verdict(False, f"chemin protégé : {p}", "high", True)
        if not any(norm.startswith(x) for x in MUTABLE):
            return Verdict(False, f"hors zone modifiable : {p}", "medium", True)

    hits = [quoi for motif, quoi in SUSPICIOUS if motif.search(diff_text)]
    if hits:
        return Verdict(True, " ; ".join(hits), "high", True)

    # Toucher au prompt d'un agent ou à la boucle = revue humaine.
    sensitive = ("orchestrator/loop.py", "orchestrator/prompts/")
    if any(p.lstrip("./").startswith(s) for p in paths for s in sensitive):
        return Verdict(True, "modifie la boucle ou les prompts", "medium", True)

    return Verdict(True, "", "low", False)


def fingerprint(root: Path) -> str:
    """Empreinte des zones protégées. Vérifiée à chaque génération.

    Si elle bouge sans qu'un humain ait poussé un commit, on arrête tout :
    quelque chose a écrit là où il ne devait pas.
    """
    h = hashlib.sha256()
    for rel in PROTECTED:
        target = root / rel
        files = sorted(target.rglob("*")) if target.is_dir() else [target]
        for f in files:
            if f.is_file():
                h.update(f.relative_to(root).as_posix().encode())
                h.update(f.read_bytes())
    return h.hexdigest()
