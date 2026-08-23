"""NOYAU IMMUABLE. Archive des générations et compteur de budget."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS generations (
  id            INTEGER PRIMARY KEY,
  run_id        TEXT NOT NULL,
  parent_id     INTEGER,
  role_proposer TEXT NOT NULL,       -- 'A' ou 'B'
  item_id       TEXT,                -- item du backlog traité
  diff          TEXT,
  scores        TEXT,                -- json {pass_rate, tokens, wall_time}
  status        TEXT NOT NULL,       -- proposed|awaiting_gate|running|
                                     -- awaiting_human|completed|failed|rejected
  risk          TEXT DEFAULT 'low',
  human_verdict TEXT,                -- ok | scope | risky | useless
  note          TEXT,
  created_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS spend (
  id       INTEGER PRIMARY KEY,
  gen_id   INTEGER,
  kind     TEXT,                     -- llm | sandbox
  usd      REAL NOT NULL,
  at       REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS control (
  key   TEXT PRIMARY KEY,        -- paused | stop | verdict:<gen> | rollback:<gen>
  value TEXT NOT NULL,
  at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id     INTEGER PRIMARY KEY,
  gen_id INTEGER,
  actor  TEXT,                       -- A | B | referee | human | system
  kind   TEXT,                       -- message | score | verdict | error
  body   TEXT,
  at     REAL NOT NULL
);
"""


@dataclass
class Generation:
    run_id: str
    role_proposer: str
    parent_id: int | None = None
    item_id: str | None = None
    diff: str = ""
    scores: dict = field(default_factory=dict)
    status: str = "proposed"
    risk: str = "low"
    human_verdict: str | None = None
    note: str = ""
    id: int | None = None


class Archive:
    def __init__(self, path: str = "data/archive.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        # Deux conteneurs écrivent dans ce fichier : l'API à chaque requête,
        # le worker en continu. Le délai d'attente sur verrou est explicite
        # plutôt que laissé à la valeur par défaut — c'est une hypothèse de
        # l'architecture, elle doit être lisible ici.
        #
        # Pas de WAL : il réclame de la mémoire partagée entre processus, ce
        # que certains montages (bind mount Docker Desktop, NFS) ne rendent
        # pas correctement. Le mode par défaut tient la charge mesurée.
        self.db.execute("PRAGMA busy_timeout = 15000")
        self.db.executescript(SCHEMA)
        self.db.commit()

    # --- générations -----------------------------------------------------
    def add(self, g: Generation) -> int:
        d = asdict(g)
        d.pop("id")
        d["scores"] = json.dumps(d["scores"])
        cols = ",".join(d)
        cur = self.db.execute(
            f"INSERT INTO generations ({cols},created_at) "
            f"VALUES ({','.join('?' * len(d))},?)",
            (*d.values(), time.time()),
        )
        self.db.commit()
        return cur.lastrowid

    def update(self, gen_id: int, **fields) -> None:
        if "scores" in fields:
            fields["scores"] = json.dumps(fields["scores"])
        sets = ",".join(f"{k}=?" for k in fields)
        self.db.execute(
            f"UPDATE generations SET {sets} WHERE id=?", (*fields.values(), gen_id)
        )
        self.db.commit()

    def get(self, gen_id: int) -> sqlite3.Row | None:
        """La génération visée, pas la plus récente.

        `recent(...)[0]` renvoyait la dernière ligne insérée, qui n'est pas
        celle dont on valide le verdict dès qu'un tour s'est intercalé.
        """
        return self.db.execute(
            "SELECT * FROM generations WHERE id=?", (gen_id,)
        ).fetchone()

    def awaiting(self, run_id: str) -> list[sqlite3.Row]:
        """Tout ce qui attend un arbitrage humain, du plus ancien au plus récent.

        Deux étapes distinctes se retrouvent ici : `awaiting_gate` demande
        l'autorisation d'évaluer un patch que le garde-fou a jugé sensible,
        `awaiting_human` demande l'intégration d'un patch déjà mesuré.
        """
        return self.db.execute(
            "SELECT * FROM generations WHERE run_id=? AND status IN "
            "('awaiting_gate','awaiting_human') ORDER BY id",
            (run_id,),
        ).fetchall()

    def best(self, run_id: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM generations WHERE run_id=? AND status='completed' "
            "ORDER BY json_extract(scores,'$.pass_rate') DESC LIMIT 1",
            (run_id,),
        ).fetchone()

    def recent(self, run_id: str, n: int = 20) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM generations WHERE run_id=? ORDER BY id DESC LIMIT ?",
            (run_id, n),
        ).fetchall()

    def failures(self, run_id: str, n: int = 10) -> list[sqlite3.Row]:
        """Les échecs repartent en contexte : c'est ce qui évite de reboucler."""
        return self.db.execute(
            "SELECT * FROM generations WHERE run_id=? "
            "AND status IN ('failed','rejected') ORDER BY id DESC LIMIT ?",
            (run_id, n),
        ).fetchall()

    def acceptance_rate(self, run_id: str, window: int = 10) -> dict:
        """Part des propositions qui passent le harness sans régression.

        C'est la métrique de DIR-003 : elle mesure la qualité de ce qu'un agent
        transmet à l'autre, pas seulement le résultat final. Calculée ici, dans
        le noyau, pour la même raison que le reste — aucun agent ne doit pouvoir
        influencer la façon dont on le note.

        `rejected` et `failed` comptent tous deux comme non acceptés : un patch
        refusé par le garde-fou est un patch qu'il ne fallait pas proposer.
        """
        rows = self.db.execute(
            "SELECT role_proposer, status FROM generations "
            "WHERE run_id=? AND status IN "
            "('completed','failed','rejected') ORDER BY id DESC LIMIT ?",
            (run_id, window),
        ).fetchall()
        if not rows:
            return {"overall": None, "A": None, "B": None, "n": 0}

        def rate(subset):
            return (sum(r["status"] == "completed" for r in subset) / len(subset)
                    if subset else None)

        return {
            "overall": rate(rows),
            "A": rate([r for r in rows if r["role_proposer"] == "A"]),
            "B": rate([r for r in rows if r["role_proposer"] == "B"]),
            "n": len(rows),
        }

    def plateau(self, run_id: str, window: int = 5, eps: float = 0.01) -> bool:
        rows = self.db.execute(
            "SELECT json_extract(scores,'$.pass_rate') AS p FROM generations "
            "WHERE run_id=? AND status='completed' ORDER BY id DESC LIMIT ?",
            (run_id, window),
        ).fetchall()
        vals = [r["p"] for r in rows if r["p"] is not None]
        return len(vals) >= window and (max(vals) - min(vals)) < eps

    # --- plan de contrôle ------------------------------------------------
    # L'API et le worker sont deux conteneurs distincts : ils ne partagent
    # aucune mémoire, seulement cette base. L'API écrit des intentions, le
    # worker est la seule chose qui exécute. Ne pas fusionner les deux
    # services pour contourner ça : la séparation web / worker est ce qui
    # permet le scale-to-zero en cloud.

    def set_control(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO control (key,value,at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, at=excluded.at",
            (key, value, time.time()),
        )
        self.db.commit()

    def get_control(self, key: str, default: str | None = None) -> str | None:
        r = self.db.execute(
            "SELECT value FROM control WHERE key=?", (key,)
        ).fetchone()
        return r["value"] if r else default

    def flag(self, key: str) -> bool:
        return self.get_control(key, "0") == "1"

    def pending(self, prefix: str) -> list[tuple[str, str]]:
        """Les intentions déposées par l'API et pas encore consommées."""
        return [(r["key"], r["value"]) for r in self.db.execute(
            "SELECT key,value FROM control WHERE key LIKE ? ORDER BY at",
            (f"{prefix}%",),
        ).fetchall()]

    def clear_control(self, key: str) -> None:
        self.db.execute("DELETE FROM control WHERE key=?", (key,))
        self.db.commit()

    # --- budget ----------------------------------------------------------
    def charge(self, gen_id: int | None, kind: str, usd: float) -> None:
        self.db.execute(
            "INSERT INTO spend (gen_id,kind,usd,at) VALUES (?,?,?,?)",
            (gen_id, kind, usd, time.time()),
        )
        self.db.commit()

    def spent(self) -> float:
        r = self.db.execute("SELECT COALESCE(SUM(usd),0) AS t FROM spend").fetchone()
        return r["t"]

    # --- fil de discussion ----------------------------------------------
    def say(self, gen_id: int | None, actor: str, kind: str, body: str) -> None:
        self.db.execute(
            "INSERT INTO events (gen_id,actor,kind,body,at) VALUES (?,?,?,?,?)",
            (gen_id, actor, kind, body, time.time()),
        )
        self.db.commit()

    def transcript(self, after_id: int = 0, limit: int = 200) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?", (after_id, limit)
        ).fetchall()
