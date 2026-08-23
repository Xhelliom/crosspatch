"""
IMMUABLE. Le harness charge l'agent depuis /work, lui fait résoudre chaque
tâche, et exécute les tests de référence sur sa solution.

Sortie : une seule ligne JSON en dernier, lue par l'orchestrateur.
Ne modifie jamais ce fichier pour "faire passer" une génération : ça
invalide toutes les comparaisons antérieures.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
WORK = Path("/work")
TIMEOUT = 180


def load_agent():
    sys.path.insert(0, str(WORK))
    from orchestrator.agent import solve  # noqa: E402
    return solve


def run_task(solve, task: Path) -> dict:
    prompt = (task / "prompt.txt").read_text()
    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        try:
            solve(prompt, d)
        except Exception as e:
            return {"id": task.name, "ok": False, "why": f"agent: {e!r}",
                    "elapsed": time.time() - t0}
        shutil.copy(task / "test_solution.py", d / "test_solution.py")
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", str(d)],
                           capture_output=True, timeout=TIMEOUT, cwd=d)
    return {"id": task.name, "ok": p.returncode == 0,
            "why": p.stdout.decode()[-600:] if p.returncode else "",
            "elapsed": time.time() - t0}


def main():
    solve = load_agent()
    tasks = sorted(t for t in (HERE / "tasks").iterdir() if t.is_dir())
    results = [run_task(solve, t) for t in tasks]
    times = sorted(r["elapsed"] for r in results)
    print(json.dumps({
        "pass_rate": sum(r["ok"] for r in results) / max(len(results), 1),
        "wall_time_p50": times[len(times) // 2] if times else 0.0,
        "tokens_per_task": 0.0,
        "failures": [r for r in results if not r["ok"]],
    }))


if __name__ == "__main__":
    main()
