"""
Boucle d'amélioration.

Un seul processus alterne les deux rôles. Ce ne sont pas deux daemons :
A et B sont deux historiques de conversation et deux workspaces distincts.
Ça supprime la synchro, le ping-pong infini et le transport.

Invariant central : **le processus qui tourne ne se modifie jamais lui-même.**
Il produit un candidat, le fait évaluer en sandbox, et si l'humain valide,
le candidat est poussé en git — le redéploiement fait le reste. Pas de
hot-patch : c'est la seule façon de garder un point de retour sûr.
"""
from __future__ import annotations

import json
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


class Loop:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.run_id = cfg["run_id"]
        self.archive = Archive(cfg["archive_path"])
        self.llm = LLM(cfg["model"], self.archive, cfg["max_usd"])
        self.runner = Runner(self.archive, cfg["sandbox_template"])
        self.objective = (ROOT / "mission" / "OBJECTIVE.md").read_text()
        self.baseline = guard.fingerprint(ROOT)
        self.paused = False
        self.stop = False
        self.dup_streak = 0
        self.last_failures: list[dict] = []
        # A et B partent identiques : c'est la prémisse de l'expérience.
        self.ws = {r: ROOT / "workspaces" / r for r in ("A", "B")}
        for p in self.ws.values():
            if not p.exists():
                shutil.copytree(ROOT / "orchestrator", p / "orchestrator")

    # ------------------------------------------------------------------ run
    def run(self) -> None:
        turn = 0
        while not self.stop:
            if self.paused:
                time.sleep(2)
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
        out = self.llm.chat_json([
            {"role": "system", "content": (PROMPTS / "ideator.md").read_text()},
            {"role": "user", "content": bl.render(ctx)},
        ])

        fresh, dupes, orphans = bl.ingest(
            BACKLOG, out["items"], proposer, None,
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
                         out["observations"] + "\n\n" +
                         "\n".join(f"• {i['title']} — {i['hypothesis']}" for i in fresh))

        # Deux agents identiques qui n'ont plus que des doublons ont convergé.
        total = len(out["items"])
        if total and dupes / total >= self.cfg["dup_ratio_stop"]:
            self.dup_streak += 1
            self.archive.say(None, "system", "message",
                             f"{dupes}/{total} idées déjà connues "
                             f"({self.dup_streak}e tour consécutif)")
            if self.dup_streak >= 3:
                self.archive.say(None, "system", "message",
                                 "Plus d'idées neuves. CONVERGED.")
                self.stop = True
        else:
            self.dup_streak = 0

    # ----------------------------------------------------------------- tour
    def _turn(self, proposer: str, target: str) -> None:
        """proposer lit le code de target et propose un patch. Jamais le sien."""
        items = bl.load(ROOT / "mission" / "BACKLOG.yaml")
        ranked = bl.rank(items)

        ctx = {
            "objective": self.objective,
            "target_tree": bl.tree(self.ws[target]),
            "backlog": [bl.brief(i) for i in ranked[:8]],
            "past_failures": [dict(r) for r in self.archive.failures(self.run_id)],
            "best": dict(self.archive.best(self.run_id) or {}),
        }

        proposal = self.llm.chat_json([
            {"role": "system", "content": (PROMPTS / "proposer.md").read_text()},
            {"role": "user", "content": bl.render(ctx)},
        ])

        gen = Generation(
            run_id=self.run_id, role_proposer=proposer,
            item_id=proposal.get("item_id"), diff=proposal["diff"],
            note=proposal.get("rationale", ""),
        )
        gen_id = self.archive.add(gen)
        self.archive.say(gen_id, proposer, "message", proposal["rationale"])

        # --- garde-fou avant toute exécution
        v = guard.classify(proposal["paths"], proposal["diff"])
        self.archive.update(gen_id, risk=v.risk)
        if not v.ok:
            self.archive.update(gen_id, status="rejected", note=v.reason)
            self.archive.say(gen_id, "system", "verdict", f"Refusé : {v.reason}")
            return

        # --- gate humain sur la proposition (risque medium/high seulement)
        if v.needs_human:
            self.archive.update(gen_id, status="awaiting_human")
            self.archive.say(gen_id, "system", "verdict",
                             f"En attente de validation ({v.risk}) : {v.reason}")
            return  # la reprise se fait via /control après le ✅

        self._evaluate(gen_id, target, proposal["diff"])

    # ------------------------------------------------------------- éval
    def _evaluate(self, gen_id: int, target: str, diff: str) -> None:
        self.archive.update(gen_id, status="running")
        cand = ROOT / "candidates" / str(gen_id)
        shutil.copytree(self.ws[target], cand, dirs_exist_ok=True)
        bl.apply_patch(cand, diff)

        runs = self.runner.soak(cand, ROOT / "harness",
                                n=self.cfg["soak_runs"], gen_id=gen_id)
        self.last_failures = runs[0].failures
        if any(r.crashed for r in runs):
            self.archive.update(gen_id, status="failed", note="crash du harness")
            return

        best_before = self.archive.best(self.run_id)
        before = (best_before["scores"] and
                  __import__("json").loads(best_before["scores"])["pass_rate"]
                  ) if best_before else 0.0
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

    # ------------------------------------------------- appelé par l'API
    def human_verdict(self, gen_id: int, verdict: str) -> None:
        """verdict ∈ ok | scope | risky | useless"""
        self.archive.update(gen_id, human_verdict=verdict)
        row = dict(self.archive.recent(self.run_id, 50)[0])
        item_id = row.get("item_id")
        if verdict != "ok":
            self.archive.update(gen_id, status="rejected")
            if item_id:
                bl.close(BACKLOG, item_id, "failed")
            return
        self.archive.update(gen_id, status="completed")
        if item_id:
            bl.close(BACKLOG, item_id, "completed")
        # La promotion réelle = commit + push. Le redéploiement recharge
        # le code. On ne patche jamais le processus vivant.
        bl.promote(ROOT, gen_id)


def main() -> None:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    Loop(cfg).run()


if __name__ == "__main__":
    main()
