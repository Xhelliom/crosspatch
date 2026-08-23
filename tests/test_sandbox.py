"""
Bugs n°3, 4 et 6 : la clé de l'agent doit entrer dans le sandbox, la surface
E2B doit être celle du SDK réel, et le temps de sandbox doit être facturé.

Aucun sandbox n'est démarré ici : E2B est remplacé par un double. Ce qui est
vérifié, c'est ce que le Runner *demande* au SDK.
"""
from __future__ import annotations

import json
import tarfile
import io
from pathlib import Path

import pytest

from orchestrator import sandbox as sx

PAYLOAD = {"pass_rate": 0.5, "tokens_per_task": 12.0,
           "wall_time_p50": 1.5, "failures": [{"id": "rle", "why": "boom"}]}


class _Commands:
    def __init__(self, stdout, boom=None):
        self.stdout, self.boom, self.calls = stdout, boom, []

    def run(self, cmd, timeout=None, on_stdout=None, **kw):
        self.calls.append(cmd)
        if self.boom and "run.py" in cmd:
            raise self.boom
        if on_stdout:
            for line in self.stdout.splitlines():
                on_stdout(line)
        return type("R", (), {"stdout": self.stdout, "stderr": "", "exit_code": 0})()


class _Files:
    def __init__(self):
        self.written = {}

    def write(self, path, data):
        self.written[path] = data


class _Sandbox:
    """Double d'un sandbox E2B : gestionnaire de contexte, `files`, `commands`."""

    created: dict = {}
    last: "_Sandbox | None" = None

    def __init__(self, stdout, boom=None):
        self.files, self.commands = _Files(), _Commands(stdout, boom)
        self.killed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.killed = True
        return False

    @classmethod
    def make(cls, stdout, boom=None):
        class Factory:
            @staticmethod
            def create(**kw):
                cls.created.clear()
                cls.created.update(kw)
                cls.last = cls(stdout, boom)
                return cls.last
        return Factory


class _Archive:
    def __init__(self):
        self.charges, self.said = [], []

    def charge(self, gen_id, kind, usd):
        self.charges.append((kind, usd))

    def say(self, gen_id, actor, kind, body):
        self.said.append(body)


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("E2B_API_KEY", "e2b_test")
    monkeypatch.setenv("AGENT_OPENROUTER_API_KEY", "sk-agent")
    monkeypatch.delenv("AGENT_MODEL", raising=False)


def _dirs(tmp_path):
    ws, hs = tmp_path / "ws", tmp_path / "harness"
    (ws / "orchestrator").mkdir(parents=True)
    (ws / "orchestrator" / "agent.py").write_text("x")
    hs.mkdir()
    (hs / "run.py").write_text("y")
    return ws, hs


# --- bug 3 : la clé entre dans le sandbox ----------------------------------

def test_la_cle_de_l_agent_est_injectee(env, tmp_path, monkeypatch):
    monkeypatch.setattr(sx, "E2BSandbox", _Sandbox.make(json.dumps(PAYLOAD)))
    r = sx.Runner(_Archive(), template="base")
    r.evaluate(*_dirs(tmp_path))
    assert _Sandbox.created["envs"]["OPENROUTER_API_KEY"] == "sk-agent"
    assert _Sandbox.created["template"] == "base"
    assert _Sandbox.created["api_key"] == "e2b_test"


def test_pas_de_repli_sur_la_cle_de_l_orchestrateur(tmp_path, monkeypatch):
    """Un secret injecté dans un environnement qui exécute du code généré
    doit être une clé dédiée et plafonnée, jamais celle de l'orchestrateur."""
    monkeypatch.setenv("E2B_API_KEY", "e2b_test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-orchestrateur")
    monkeypatch.delenv("AGENT_OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(sx, "E2BSandbox", _Sandbox.make(json.dumps(PAYLOAD)))
    with pytest.raises(RuntimeError, match="AGENT_OPENROUTER_API_KEY"):
        sx.Runner(_Archive()).evaluate(*_dirs(tmp_path))


# --- bug 4 : surface E2B --------------------------------------------------

def test_le_bootstrap_installe_pytest_et_httpx(env, tmp_path, monkeypatch):
    """Le template `base` n'a ni l'un ni l'autre."""
    monkeypatch.setattr(sx, "E2BSandbox", _Sandbox.make(json.dumps(PAYLOAD)))
    sx.Runner(_Archive(), bootstrap="pip install -q pytest httpx").evaluate(
        *_dirs(tmp_path))
    setup = _Sandbox.last.commands.calls[0]
    assert "pip install -q pytest httpx" in setup
    assert "chmod -R a-w /harness" in setup      # harness monté en lecture seule


def test_bootstrap_vide_pour_un_template_dedie(env, tmp_path, monkeypatch):
    monkeypatch.setattr(sx, "E2BSandbox", _Sandbox.make(json.dumps(PAYLOAD)))
    sx.Runner(_Archive(), bootstrap="").evaluate(*_dirs(tmp_path))
    assert "pip install" not in _Sandbox.last.commands.calls[0]


def test_le_workspace_et_le_harness_sont_televerses(env, tmp_path, monkeypatch):
    monkeypatch.setattr(sx, "E2BSandbox", _Sandbox.make(json.dumps(PAYLOAD)))
    sx.Runner(_Archive()).evaluate(*_dirs(tmp_path))
    written = _Sandbox.last.files.written
    assert set(written) == {"/tmp/work.tar", "/tmp/harness.tar"}
    assert all(isinstance(v, bytes) for v in written.values())


def test_le_sandbox_est_toujours_ferme(env, tmp_path, monkeypatch):
    monkeypatch.setattr(sx, "E2BSandbox", _Sandbox.make(json.dumps(PAYLOAD)))
    sx.Runner(_Archive()).evaluate(*_dirs(tmp_path))
    assert _Sandbox.last.killed is True


def test_sortie_en_erreur_ne_tue_pas_le_worker(env, tmp_path, monkeypatch):
    """`commands.run` lève CommandExitException sur code non nul."""
    boom = sx.CommandExitException(stderr="pytest introuvable", stdout="",
                                   exit_code=127, error="oops")
    monkeypatch.setattr(sx, "E2BSandbox", _Sandbox.make("", boom))
    res = sx.Runner(_Archive()).evaluate(*_dirs(tmp_path))
    assert res.crashed is True and res.pass_rate == 0.0
    assert "pytest introuvable" in res.stdout


def test_sortie_non_json_est_un_crash(env, tmp_path, monkeypatch):
    monkeypatch.setattr(sx, "E2BSandbox", _Sandbox.make("bruit sans json"))
    res = sx.Runner(_Archive()).evaluate(*_dirs(tmp_path))
    assert res.crashed is True


def test_resultat_lu_sur_la_derniere_ligne(env, tmp_path, monkeypatch):
    monkeypatch.setattr(sx, "E2BSandbox",
                        _Sandbox.make("bruit\n" + json.dumps(PAYLOAD)))
    res = sx.Runner(_Archive()).evaluate(*_dirs(tmp_path))
    assert res.pass_rate == 0.5 and res.failures[0]["id"] == "rle"
    assert res.crashed is False


def test_le_json_de_resultat_ne_pollue_pas_le_fil(env, tmp_path, monkeypatch):
    monkeypatch.setattr(sx, "E2BSandbox",
                        _Sandbox.make("collecting...\n" + json.dumps(PAYLOAD)))
    arch = _Archive()
    sx.Runner(arch).evaluate(*_dirs(tmp_path))
    assert arch.said == ["collecting..."]


# --- bug 6 : le sandbox est facturé ---------------------------------------

def test_le_temps_de_sandbox_est_compte(env, tmp_path, monkeypatch):
    monkeypatch.setattr(sx, "E2BSandbox", _Sandbox.make(json.dumps(PAYLOAD)))
    arch = _Archive()
    sx.Runner(arch, usd_per_hour=3600.0).evaluate(*_dirs(tmp_path))
    kind, usd = arch.charges[0]
    assert kind == "sandbox" and usd > 0.0


def test_estimation_proportionnelle_a_la_duree():
    assert sx._estimate(3600, 0.10) == pytest.approx(0.10)
    assert sx._estimate(0, 0.10) == 0.0


def test_charge_meme_quand_ca_plante(env, tmp_path, monkeypatch):
    boom = sx.CommandExitException(stderr="x", stdout="", exit_code=1, error="e")
    monkeypatch.setattr(sx, "E2BSandbox", _Sandbox.make("", boom))
    arch = _Archive()
    sx.Runner(arch, usd_per_hour=3600.0).evaluate(*_dirs(tmp_path))
    assert arch.charges and arch.charges[0][0] == "sandbox"


# --- archivage ------------------------------------------------------------

def test_le_tar_exclut_git_et_pycache(tmp_path):
    src = tmp_path / "src"
    (src / "__pycache__").mkdir(parents=True)
    (src / "__pycache__" / "a.pyc").write_bytes(b"\x00")
    (src / ".git").mkdir()
    (src / ".git" / "config").write_text("secret")
    (src / "agent.py").write_text("x")
    names = tarfile.open(fileobj=io.BytesIO(sx._tar(src))).getnames()
    assert "./agent.py" in names
    assert not any("__pycache__" in n or ".git" in n for n in names)
