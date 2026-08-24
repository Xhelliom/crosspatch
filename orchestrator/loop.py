"""
Boucle d'amélioration.

Un seul processus alterne les deux rôles. Ce ne sont pas deux daemons :
A et B sont deux historiques de conversation et deux workspaces distincts.
Ça supprime la synchro, le ping-pong infini et le transport.

Invariant central : **le processus qui tourne ne se modifie jamais lui-même.**
Il produit un candidat, le fait évaluer en sandbox, et si l'humain valide,
le candidat est poussé en git — le redéploiement fait le reste. Pas de
hot-patch : c'est la seule façon de garder un point de retour sûr.

Le worker est **la seule chose qui exécute**. L'API tourne dans un autre
conteneur et n'a aucune mémoire partagée avec lui : elle dépose des
intentions dans la table `control`, relues ici à chaque itération.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import yaml

from kernel import guard
from kernel.archive import Archive, Generation
from orchestrator import backlog as bl
from orchestrator import directions as dr
from orchestrator.llm import LLM, Budget
from orchestrator.sandbox import Runner

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "orchestrator" / "prompts"
BACKLOG = ROOT / "mission" / "BACKLOG.yaml"
DIRS = ROOT / "mission" / "DIRECTIONS.yaml"

IDLE = 2.0        # seconde(s) entre deux relectures du plan de contrôle
MAX_ECHECS = 3    # tours ratés d'affilée avant de rendre la main


class Loop:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.run_id = cfg["run_id"]
        self.archive = Archive(
            os.environ.get("CROSSPATCH_ARCHIVE") or cfg["archive_path"])
        self.llm = LLM(cfg["model"], self.archive, cfg["max_usd"])
        self.runner = Runner(
            self.archive, cfg["sandbox_template"],
            usd_per_hour=cfg.get("sandbox_usd_per_hour", 0.0),
            bootstrap=cfg.get("sandbox_bootstrap", ""))
        self.objective = (ROOT / "mission" / "OBJECTIVE.md").read_text()
        self.baseline = guard.fingerprint(ROOT)
        self.dup_streak = 0
        self.echecs = 0
        self.last_failures: list[dict] = []
        # A et B partent identiques : c'est la prémisse de l'expérience.
        self.ws = {r: ROOT / "workspaces" / r for r in ("A", "B")}
        for p in self.ws.values():
            self._seed_workspace(p)

    def _seed_workspace(self, dest: Path) -> None:
        """Le workspace est le miroir exact de la zone modifiable.

        Il doit couvrir tout `guard.MUTABLE`, sinon un patch parfaitement
        légitime sur `api/` ou `mcp/` est accepté par le garde-fou puis
        échoue à s'appliquer, faute de fichier cible.
        """
        for rel in guard.MUTABLE:
            src = ROOT / rel.rstrip("/")
            target = dest / rel.rstrip("/")
            if src.is_dir() and not target.exists():
                shutil.copytree(src, target,
                                ignore=shutil.ignore_patterns("__pycache__"))

    # ---------------------------------------------------- plan de contrôle
    @property
    def paused(self) -> bool:
        return self.archive.flag("paused")

    @property
    def stop(self) -> bool:
        return self.archive.flag("stop")

    def _attendre_reprise(self) -> bool:
        """Bloque tant que l'humain a suspendu. False si l'arrêt est demandé.

        Appelé **avant chaque dépense** — un appel de modèle, un sandbox — et
        pas seulement en tête de boucle. Sans ça, « suspendre » n'agit qu'au
        tour suivant : le soak en cours brûlerait ses trois microVM et le tour
        ses appels de modèle. Un coupe-circuit qui laisse finir la dépense en
        cours n'en est pas un.
        """
        while self.paused and not self.stop:
            time.sleep(IDLE)
        return not self.stop

    def _drain_control(self) -> None:
        """Consomme les intentions déposées par l'API depuis l'autre conteneur."""
        for key, verdict in self.archive.pending("verdict:"):
            gen_id = int(key.split(":", 1)[1])
            try:
                self.human_verdict(gen_id, verdict)
            except Exception as e:                      # noqa: BLE001
                self.archive.say(gen_id, "system", "error",
                                 f"verdict {verdict} impossible : {e}")
                self.archive.update(gen_id, status="failed", note=f"verdict: {e}")
            finally:
                self.archive.clear_control(key)

        for key, target in self.archive.pending("rollback:"):
            gen_id = int(key.split(":", 1)[1])
            try:
                bl.rollback(ROOT, gen_id, target)
                self.archive.say(gen_id, "system", "verdict",
                                 f"génération {gen_id} annulée (git revert)")
            except Exception as e:                      # noqa: BLE001
                self.archive.say(gen_id, "system", "error", f"rollback : {e}")
            finally:
                self.archive.clear_control(key)

    # ------------------------------------------------------------------ run
    def run(self) -> None:
        turn = 0
        while True:
            self._drain_control()
            if self.stop:
                self.archive.say(None, "system", "message", "Arrêt demandé.")
                return
            if self.paused:
                time.sleep(IDLE)
                continue

            # Rien ne se propose tant qu'un arbitrage est en attente : sinon
            # A et B continuent de patcher un code que l'humain n'a pas encore
            # laissé avancer, produisent des doublons et brûlent du budget.
            if self.archive.awaiting(self.run_id):
                time.sleep(IDLE)
                continue

            # Le noyau n'a pas bougé ? Sinon quelqu'un a écrit où il ne fallait pas.
            if guard.fingerprint(ROOT) != self.baseline:
                self.archive.say(None, "system", "error",
                                 "Empreinte du noyau modifiée — arrêt.")
                return

            if self.archive.plateau(self.run_id, self.cfg["plateau_window"]):
                self.archive.say(None, "system", "message",
                                 "Plateau atteint. CONVERGED.")
                return

            proposer = "A" if turn % 2 == 0 else "B"
            target = "B" if proposer == "A" else "A"
            try:
                self._ideate(proposer, target)
                self._turn(proposer, target)
            except Budget as e:
                self.archive.say(None, "system", "error", str(e))
                return
            except Exception as e:                       # noqa: BLE001
                # Un modèle moyen rate parfois le JSON strict, et le réseau
                # tombe. C'est une donnée sur le modèle, pas un incident à
                # masquer — mais ça ne doit pas tuer le worker en silence :
                # je pilote depuis le téléphone, le seul canal de diagnostic
                # est le fil.
                self.echecs += 1
                self.archive.say(None, "system", "error",
                                 f"tour {turn} raté ({type(e).__name__}) : {e} "
                                 f"[{self.echecs}/{MAX_ECHECS}]")
                if self.echecs >= MAX_ECHECS:
                    self.archive.say(None, "system", "error",
                                     f"{MAX_ECHECS} tours ratés d'affilée — arrêt.")
                    return
                turn += 1
                continue
            self.echecs = 0
            turn += 1

    # ------------------------------------------------------------ idéation
    def _ideate(self, proposer: str, target: str) -> None:
        """Le proposeur regarde ce qui a raté et écrit ses propres idées.

        C'est ici que le backlog naît. Rien n'est seedé à la main : si les
        agents ne trouvent rien à proposer, c'est un résultat, pas une panne.
        """
        last = self.archive.best(self.run_id)
        scores = json.loads(last["scores"]) if last and last["scores"] else {}
        live_dirs = dr.active(DIRS)
        ctx = {
            "objective": self.objective,
            "directions": [dr.brief(d) for d in live_dirs],
            "agent_source": (self.ws[target] / "orchestrator" / "agent.py").read_text(),
            "failures": self.last_failures,
            "metrics": {**scores,
                        "acceptance_rate": self.archive.acceptance_rate(self.run_id)},
            "backlog": [bl.brief(i) for i in bl.load(BACKLOG)],
            "rejected": [
                {"note": r["note"], "verdict": r["human_verdict"]}
                for r in self.archive.failures(self.run_id)
            ],
        }
        if not self._attendre_reprise():
            return
        out = self.llm.chat_json([
            {"role": "system", "content": (PROMPTS / "ideator.md").read_text()},
            {"role": "user", "content": bl.render(ctx)},
        ])

        fresh, dupes, orphans = bl.ingest(
            BACKLOG, out.get("items") or [], proposer, None,
            valid_dirs={d["id"] for d in live_dirs})

        for nd in out.get("new_directions", []):
            d = dr.add(DIRS, nd["title"], nd.get("intent", ""), proposer)
            self.archive.say(None, "system", "verdict",
                             f"{proposer} propose une direction — "
                             f"{d['id']} : {d['title']} (en attente)")
        if orphans:
            self.archive.say(None, "system", "message",
                             f"{orphans} tâche(s) écartée(s) : hors direction active")
        self.archive.say(None, proposer, "message",
                         out.get("observations", "") + "\n\n" +
                         "\n".join(f"• {i['title']} — {i['hypothesis']}" for i in fresh))

        # Deux agents identiques qui n'ont plus que des doublons ont convergé.
        total = len(out.get("items") or [])
        if total and dupes / total >= self.cfg["dup_ratio_stop"]:
            self.dup_streak += 1
            self.archive.say(None, "system", "message",
                             f"{dupes}/{total} idées déjà connues "
                             f"({self.dup_streak}e tour consécutif)")
            if self.dup_streak >= 3:
                self.archive.say(None, "system", "message",
                                 "Plus d'idées neuves. CONVERGED.")
                self.archive.set_control("stop", "1")
        else:
            self.dup_streak = 0

    # ----------------------------------------------------------------- tour
    def _turn(self, proposer: str, target: str) -> None:
        """proposer lit le code de target et propose un patch. Jamais le sien."""
        items = bl.load(BACKLOG)
        ranked = bl.rank(items)

        ctx = {
            "objective": self.objective,
            "target_tree": bl.tree(self.ws[target]),
            "backlog": [bl.brief(i) for i in ranked[:8]],
            "past_failures": [dict(r) for r in self.archive.failures(self.run_id)],
            "best": dict(self.archive.best(self.run_id) or {}),
        }

        if not self._attendre_reprise():
            return
        proposal = self.llm.chat_json([
            {"role": "system", "content": (PROMPTS / "proposer.md").read_text()},
            {"role": "user", "content": bl.render(ctx)},
        ])

        # `item_id: null` + "CONVERGED" est une réponse valide du proposeur.
        if not proposal.get("diff"):
            self.archive.say(None, proposer, "message",
                             proposal.get("rationale", "aucun patch proposé"))
            return

        gen = Generation(
            run_id=self.run_id, role_proposer=proposer,
            item_id=proposal.get("item_id"), diff=proposal["diff"],
            note=proposal.get("rationale", ""),
        )
        gen_id = self.archive.add(gen)
        self.archive.say(gen_id, proposer, "message", proposal.get("rationale", ""))

        # --- garde-fou avant toute exécution
        v = guard.classify(proposal.get("paths") or [], proposal["diff"])
        self.archive.update(gen_id, risk=v.risk)
        if not v.ok:
            self.archive.update(gen_id, status="rejected", note=v.reason)
            self.archive.say(gen_id, "system", "verdict", f"Refusé : {v.reason}")
            return

        # --- gate humain sur la proposition (risque medium/high seulement)
        if v.needs_human:
            self.archive.update(gen_id, status="awaiting_gate", note=v.reason)
            self.archive.say(gen_id, "system", "verdict",
                             f"En attente d'autorisation ({v.risk}) : {v.reason}")
            return  # un verdict `ok` déclenchera l'évaluation

        self._evaluate(gen_id, target, proposal["diff"])

    # ------------------------------------------------------------- éval
    def _evaluate(self, gen_id: int, target: str, diff: str) -> None:
        self.archive.update(gen_id, status="running")
        cand = ROOT / "candidates" / str(gen_id)
        shutil.rmtree(cand, ignore_errors=True)
        shutil.copytree(self.ws[target], cand,
                        ignore=shutil.ignore_patterns("__pycache__"))

        ok, err = bl.apply_patch(cand, diff)
        if not ok:
            # Un diff mal formé est le cas *attendu* avec un modèle moyen,
            # pas une exception : il marque la génération et repart en
            # contexte à l'idéation suivante.
            self.archive.update(gen_id, status="failed", note=f"git apply : {err}")
            self.archive.say(gen_id, "system", "error", f"patch inapplicable : {err}")
            return

        runs = self.runner.soak(cand, ROOT / "harness",
                                n=self.cfg["soak_runs"], gen_id=gen_id,
                                avant_chaque=self._attendre_reprise)
        if len(runs) < self.cfg["soak_runs"]:
            # Interrompu par un arrêt : le score partiel n'est pas comparable
            # aux autres générations, on ne l'archive pas comme un résultat.
            self.archive.update(gen_id, status="failed",
                                note="soak interrompu — arrêt demandé")
            self.archive.say(gen_id, "system", "message",
                             "Évaluation interrompue avant la fin du soak.")
            return
        self.last_failures = runs[0].failures if runs else []
        if any(r.crashed for r in runs):
            self.archive.update(gen_id, status="failed", note="crash du harness")
            self.archive.say(gen_id, "system", "error", "crash du harness")
            return

        best_before = self.archive.best(self.run_id)
        before = (json.loads(best_before["scores"]).get("pass_rate", 0.0)
                  if best_before and best_before["scores"] else 0.0)
        after = min(r.pass_rate for r in runs)   # le pire des N, pas la moyenne

        scores = {
            "pass_rate": after,
            "tokens_per_task": sum(r.tokens_per_task for r in runs) / len(runs),
            "wall_time_p50": sum(r.wall_time_p50 for r in runs) / len(runs),
            "delta": after - before,
        }
        self.archive.update(gen_id, scores=scores)
        self.archive.say(gen_id, "referee", "score",
                         f"pass_rate {before:.3f} → {after:.3f} sur "
                         f"{self.cfg['soak_runs']} runs")

        if after < before:
            self.archive.update(gen_id, status="failed", note="régression")
            return

        # Gain confirmé : gate humain final avant promotion.
        self.archive.update(gen_id, status="awaiting_human")

    # ------------------------------------------- déclenché par l'API via la base
    def human_verdict(self, gen_id: int, verdict: str) -> None:
        """verdict ∈ ok | scope | risky | useless

        Deux étapes portent un verdict, et `ok` n'y veut pas dire la même
        chose : sur `awaiting_gate` il autorise l'évaluation, sur
        `awaiting_human` il intègre un patch déjà mesuré.
        """
        row = self.archive.get(gen_id)
        if row is None:
            raise LookupError(f"génération {gen_id} inconnue")
        self.archive.update(gen_id, human_verdict=verdict)
        item_id = row["item_id"]

        if verdict != "ok":
            self.archive.update(gen_id, status="rejected")
            if item_id:
                bl.close(BACKLOG, item_id, "failed")
            return

        if row["status"] == "awaiting_gate":
            target = "B" if row["role_proposer"] == "A" else "A"
            self.archive.say(gen_id, "system", "verdict",
                             "Autorisé par l'humain — évaluation en cours.")
            self._evaluate(gen_id, target, row["diff"])
            return

        self.archive.update(gen_id, status="completed")
        if item_id:
            bl.close(BACKLOG, item_id, "completed")
        # La promotion réelle = commit + push. Le redéploiement recharge
        # le code. On ne patche jamais le processus vivant.
        target = "B" if row["role_proposer"] == "A" else "A"
        bl.promote(ROOT, gen_id, branch=f"evolution/{self.run_id}")
        # Le workspace patché devient la nouvelle base de l'agent cible.
        self._adopt(gen_id, target)

    def _adopt(self, gen_id: int, target: str) -> None:
        cand = ROOT / "candidates" / str(gen_id)
        if not cand.exists():
            return
        shutil.rmtree(self.ws[target], ignore_errors=True)
        shutil.copytree(cand, self.ws[target],
                        ignore=shutil.ignore_patterns("__pycache__"))


def main() -> None:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    Loop(cfg).run()


if __name__ == "__main__":
    main()
