"""
Bug n°7 : les heuristiques de `guard` remontaient trop de faux positifs.

Le motif n'est pas supprimé, il est resserré. Ces tests fixent les deux
bords : ce qui doit continuer de passer, ce qui doit continuer d'alerter.
"""
from __future__ import annotations

from kernel import guard


def _diff(body: str) -> str:
    return f"--- a/orchestrator/agent.py\n+++ b/orchestrator/agent.py\n{body}\n"


# --- ce qui doit rester bloqué avant toute exécution ------------------------

def test_chemin_protege_refuse():
    v = guard.classify(["kernel/archive.py"], _diff("+x = 1"))
    assert not v.ok and v.risk == "high"


def test_harness_refuse():
    assert not guard.classify(["harness/run.py"], _diff("+x = 1")).ok


def test_hors_zone_modifiable_refuse():
    assert not guard.classify(["tests/test_guard.py"], _diff("+x = 1")).ok


def test_zone_modifiable_acceptee():
    for p in ("orchestrator/agent.py", "api/main.py", "mcp/server.py"):
        assert guard.classify([p], _diff("+x = 1")).ok, p


# --- ce qui doit continuer d'alerter ---------------------------------------

def test_score_ecrit_en_dur():
    v = guard.classify(["orchestrator/agent.py"], _diff("+pass_rate = 1.0"))
    assert v.needs_human and v.risk == "high"


def test_shell_true():
    v = guard.classify(["orchestrator/agent.py"],
                       _diff("+subprocess.run(cmd, shell=True)"))
    assert v.needs_human


def test_os_system():
    assert guard.classify(["orchestrator/agent.py"],
                          _diff("+os.system('rm -rf /')")).needs_human


def test_eval_nu():
    assert guard.classify(["orchestrator/agent.py"],
                          _diff("+r = eval(payload)")).needs_human


def test_plafond_de_depense():
    assert guard.classify(["orchestrator/agent.py"],
                          _diff("+self.max_usd = 10_000")).needs_human


def test_echappement_de_chemin():
    assert guard.classify(["orchestrator/agent.py"],
                          _diff("+open('../../kernel/guard.py', 'w')")).needs_human


def test_jeton_de_controle():
    assert guard.classify(["orchestrator/agent.py"],
                          _diff("+print(os.environ['CONTROL_TOKEN'])")).needs_human


# --- les faux positifs que le resserrement doit avoir supprimés ------------

def test_auto_verification_par_subprocess_passe():
    """DIR-001 : donner un retour d'exécution à l'agent impose de lancer
    un processus. La forme liste, sans shell, ne doit pas alerter."""
    v = guard.classify(["orchestrator/agent.py"], _diff(
        "+import subprocess\n"
        "+p = subprocess.run([sys.executable, '-m', 'pytest', '-q'],\n"
        "+                   capture_output=True, timeout=60)"))
    assert v.ok and not v.needs_human, v.reason


def test_literal_eval_passe():
    v = guard.classify(["orchestrator/agent.py"],
                       _diff("+import ast\n+data = ast.literal_eval(txt)"))
    assert v.ok and not v.needs_human, v.reason


def test_prose_francaise_sur_le_budget_passe():
    """DIR-002 parle de coût : « budget » en prose ne doit pas alerter."""
    v = guard.classify(["orchestrator/agent.py"],
                       _diff("+# Réduit le budget de tokens par tâche."))
    assert v.ok and not v.needs_human, v.reason


def test_execute_dans_un_mot_passe():
    v = guard.classify(["orchestrator/agent.py"],
                       _diff("+# on execute() rien ici\n+result = self._execute(x)"))
    assert v.ok and not v.needs_human, v.reason


# --- la boucle et les prompts restent sous revue ---------------------------

def test_toucher_a_la_boucle_demande_une_revue():
    v = guard.classify(["orchestrator/loop.py"], _diff("+x = 1"))
    assert v.ok and v.needs_human and v.risk == "medium"


def test_toucher_aux_prompts_demande_une_revue():
    v = guard.classify(["orchestrator/prompts/ideator.md"], _diff("+consigne"))
    assert v.ok and v.needs_human
