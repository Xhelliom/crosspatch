"""
Exécution isolée des patches.

Un seul appel HTTP, donc strictement identique en local et en cloud —
c'est ce qui permet le `docker compose up` d'un côté et le `git push` de
l'autre sans deux architectures à maintenir.

Surface E2B vérifiée contre le SDK réel (e2b 2.x) :
  - `Sandbox(...)` **ne prend pas** `template` : son constructeur sert à se
    rattacher à un sandbox existant. C'est `Sandbox.create()` qui démarre.
  - `create(envs=...)` pose l'environnement pour toutes les commandes.
  - `commands.run()` **lève** `CommandExitException` sur code de retour non
    nul : sans rattrapage, un harness qui plante tue le worker.
  - `files.write(path, bytes)` accepte bien des octets.
"""
from __future__ import annotations

import io
import json
import os
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

from e2b import CommandExitException, Sandbox as E2BSandbox

# Clé distincte de celle de l'orchestrateur, et volontairement pas de repli
# sur OPENROUTER_API_KEY : on injecte un secret dans un environnement qui
# exécute du code écrit par un modèle. Plafond bas sur cette clé.
AGENT_KEY = "AGENT_OPENROUTER_API_KEY"


@dataclass
class EvalResult:
    pass_rate: float
    tokens_per_task: float
    wall_time_p50: float
    failures: list[dict]
    stdout: str
    crashed: bool = False


class Runner:
    def __init__(self, archive, template: str = "base", timeout: int = 900,
                 usd_per_hour: float = 0.0,
                 bootstrap: str = "pip install -q pytest httpx"):
        self.archive = archive
        self.template = template
        self.timeout = timeout
        # Le template `base` n'embarque ni pytest ni httpx. Le bootstrap les
        # installe au démarrage ; le mettre à "" quand on passe sur un
        # template dédié, qui économise ces ~20 s par évaluation.
        self.bootstrap = bootstrap
        self.usd_per_hour = usd_per_hour

    # ------------------------------------------------------------------
    def _agent_env(self) -> dict[str, str]:
        key = os.environ.get(AGENT_KEY)
        if not key:
            raise RuntimeError(
                f"{AGENT_KEY} manquante : l'agent évalué ne pourrait appeler "
                "aucun modèle et toutes les tâches échoueraient sans raison "
                "lisible. Utilise une clé dédiée, plafonnée, distincte de "
                "OPENROUTER_API_KEY."
            )
        env = {"OPENROUTER_API_KEY": key, "PYTHONDONTWRITEBYTECODE": "1"}
        if model := os.environ.get("AGENT_MODEL"):
            env["AGENT_MODEL"] = model
        return env

    def _log(self, gen_id: int | None):
        def emit(line: str) -> None:
            line = line.rstrip()
            # La dernière ligne du harness est le JSON de résultat : il part
            # dans `scores`, pas dans le fil de discussion.
            if not line or line.startswith("{"):
                return
            self.archive.say(gen_id, "referee", "message", line[:500])
        return emit

    def evaluate(self, workspace: Path, harness: Path,
                 gen_id: int | None = None) -> EvalResult:
        """Monte le workspace patché + le harness en lecture seule, lance la suite."""
        envs = self._agent_env()
        started = time.monotonic()
        try:
            with E2BSandbox.create(
                template=self.template,
                timeout=self.timeout,
                envs=envs,
                api_key=os.environ["E2B_API_KEY"],
            ) as sb:
                sb.files.write("/tmp/work.tar", _tar(workspace))
                sb.files.write("/tmp/harness.tar", _tar(harness))
                setup = (
                    "mkdir -p /work /harness && "
                    "tar xf /tmp/work.tar -C /work && "
                    "tar xf /tmp/harness.tar -C /harness && "
                    "chmod -R a-w /harness"
                )
                if self.bootstrap:
                    setup += f" && {self.bootstrap}"
                sb.commands.run(setup, timeout=self.timeout)

                proc = sb.commands.run(
                    "cd /work && python /harness/run.py",
                    timeout=self.timeout,
                    on_stdout=self._log(gen_id),
                )
                stdout = proc.stdout
        except CommandExitException as e:
            # Un harness qui sort en erreur est une génération ratée, pas un
            # incident d'infrastructure : on l'archive et la boucle continue.
            stdout = f"{e.stdout}\n{e.stderr}"
            self._charge(gen_id, started)
            return EvalResult(0.0, 0.0, 0.0, [], stdout, crashed=True)

        self._charge(gen_id, started)
        try:
            payload = json.loads(stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError, AttributeError):
            return EvalResult(0.0, 0.0, 0.0, [], stdout, crashed=True)

        return EvalResult(
            pass_rate=payload["pass_rate"],
            tokens_per_task=payload.get("tokens_per_task", 0.0),
            wall_time_p50=payload.get("wall_time_p50", 0.0),
            failures=payload.get("failures", []),
            stdout=stdout,
        )

    def _charge(self, gen_id: int | None, started: float) -> None:
        """Le sandbox est facturé à la seconde : le plafond `max_usd` ne
        couvrait que les tokens LLM tant que ça renvoyait 0.0."""
        self.archive.charge(gen_id, "sandbox",
                            _estimate(time.monotonic() - started,
                                      self.usd_per_hour))

    def soak(self, workspace: Path, harness: Path, n: int = 3,
             gen_id: int | None = None,
             avant_chaque=None) -> list[EvalResult]:
        """N runs avec seeds différents. Un bon score une fois, c'est du bruit.

        `avant_chaque` est appelé avant chaque démarrage de sandbox et peut
        renvoyer False pour interrompre : c'est le point où la boucle place
        son garde-fou de dépense. Sans lui, « suspendre » laisserait le soak
        en cours consommer ses N microVM.
        """
        runs = []
        for _ in range(n):
            if avant_chaque is not None and not avant_chaque():
                break
            runs.append(self.evaluate(workspace, harness, gen_id))
        return runs


def _tar(src: Path) -> bytes:
    def keep(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = Path(info.name).parts
        if "__pycache__" in parts or ".git" in parts:
            return None
        return info

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        t.add(src, arcname=".", filter=keep)
    return buf.getvalue()


def _estimate(seconds: float, usd_per_hour: float) -> float:
    return round(max(seconds, 0.0) / 3600.0 * usd_per_hour, 6)
