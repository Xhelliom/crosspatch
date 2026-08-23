"""
Étape 1 de la passation : faire tourner le harness seul, sans agents.

Avec des solutions correctes écrites à la main, `harness/run.py` doit sortir
`pass_rate: 1.0`. Si ce test échoue, aucune mesure de génération n'a de sens
et il est inutile de débugger la boucle.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REF = Path(__file__).parent / "reference"


def _workspace(tmp_path: Path, agent_src: Path) -> Path:
    """Construit un workspace jetable : `orchestrator/` + l'agent voulu."""
    ws = tmp_path / "work"
    shutil.copytree(ROOT / "orchestrator", ws / "orchestrator")
    shutil.copy(agent_src, ws / "orchestrator" / "agent.py")
    # L'agent de référence lit ses solutions à côté de lui.
    for f in REF.glob("*.py"):
        if f.name != "agent.py":
            shutil.copy(f, ws / "orchestrator" / f.name)
    return ws


def _run_harness(ws: Path) -> dict:
    env = {**os.environ, "CROSSPATCH_WORK": str(ws), "PYTHONDONTWRITEBYTECODE": "1"}
    p = subprocess.run([sys.executable, str(ROOT / "harness" / "run.py")],
                       capture_output=True, text=True, timeout=600, env=env)
    assert p.returncode == 0, f"harness sorti en {p.returncode}\n{p.stderr[-2000:]}"
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_harness_donne_1_0_sur_solutions_correctes(tmp_path):
    out = _run_harness(_workspace(tmp_path, REF / "agent.py"))
    assert out["failures"] == [], out["failures"]
    assert out["pass_rate"] == 1.0
    assert out["wall_time_p50"] >= 0.0


def test_manifest_est_le_miroir_du_disque():
    """Le manifeste et `tasks/` doivent lister exactement les mêmes tâches."""
    yaml = pytest.importorskip("yaml")
    man = yaml.safe_load((ROOT / "harness" / "manifest.yaml").read_text())
    listed = {t["id"] for t in man["tasks"]}
    on_disk = {d.name for d in (ROOT / "harness" / "tasks").iterdir() if d.is_dir()}
    assert listed == on_disk


def test_harness_survit_a_un_agent_qui_leve(tmp_path):
    """Un agent cassé donne pass_rate 0.0, pas une exception non rattrapée."""
    ws = tmp_path / "work"
    (ws / "orchestrator").mkdir(parents=True)
    (ws / "orchestrator" / "__init__.py").write_text("")
    (ws / "orchestrator" / "agent.py").write_text(
        "def solve(prompt, workdir):\n    raise RuntimeError('cassé')\n")
    out = _run_harness(ws)
    assert out["pass_rate"] == 0.0
    assert len(out["failures"]) == 3
    assert all("cassé" in f["why"] for f in out["failures"])
