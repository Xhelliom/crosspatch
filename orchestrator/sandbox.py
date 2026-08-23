"""
Exécution isolée des patches.

Un seul appel HTTP, donc strictement identique en local et en cloud —
c'est ce qui permet le `docker compose up` d'un côté et le `git push` de
l'autre sans deux architectures à maintenir.
"""
from __future__ import annotations

import json
import os
import tarfile
import io
from dataclasses import dataclass
from pathlib import Path

from e2b import Sandbox as E2BSandbox


@dataclass
class EvalResult:
    pass_rate: float
    tokens_per_task: float
    wall_time_p50: float
    failures: list[dict]
    stdout: str
    crashed: bool = False


class Runner:
    def __init__(self, archive, template: str = "base", timeout: int = 900):
        self.archive = archive
        self.template = template
        self.timeout = timeout

    def evaluate(self, workspace: Path, harness: Path,
                 gen_id: int | None = None) -> EvalResult:
        """Monte le workspace patché + le harness en lecture seule, lance la suite."""
        with E2BSandbox(
            template=self.template,
            timeout=self.timeout,
            api_key=os.environ["E2B_API_KEY"],
        ) as sb:
            sb.files.write("/tmp/work.tar", _tar(workspace))
            sb.files.write("/tmp/harness.tar", _tar(harness))
            sb.commands.run(
                "mkdir -p /work /harness && "
                "tar xf /tmp/work.tar -C /work && "
                "tar xf /tmp/harness.tar -C /harness && "
                "chmod -R a-w /harness"
            )
            proc = sb.commands.run(
                "cd /work && python /harness/run.py --json",
                timeout=self.timeout,
                on_stdout=lambda l: self.archive.say(gen_id, "referee", "message", l),
            )

            # Le coût sandbox est facturé à la seconde ; on l'estime ici et on
            # réconcilie avec la facture réelle plus tard si besoin.
            self.archive.charge(gen_id, "sandbox", _estimate(proc))

            try:
                payload = json.loads(proc.stdout.strip().splitlines()[-1])
            except (json.JSONDecodeError, IndexError):
                return EvalResult(0.0, 0.0, 0.0, [], proc.stdout, crashed=True)

            return EvalResult(
                pass_rate=payload["pass_rate"],
                tokens_per_task=payload.get("tokens_per_task", 0.0),
                wall_time_p50=payload.get("wall_time_p50", 0.0),
                failures=payload.get("failures", []),
                stdout=proc.stdout,
            )

    def soak(self, workspace: Path, harness: Path, n: int = 3,
             gen_id: int | None = None) -> list[EvalResult]:
        """N runs avec seeds différents. Un bon score une fois, c'est du bruit."""
        return [self.evaluate(workspace, harness, gen_id) for _ in range(n)]


def _tar(src: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        t.add(src, arcname=".")
    return buf.getvalue()


def _estimate(proc) -> float:
    return 0.0  # TODO: brancher sur la durée réelle × tarif vCPU
