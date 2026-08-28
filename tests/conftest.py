"""
Outillage commun. L'essentiel ici est `adresse_archive` : il fait tourner la
même suite contre les deux backends de `kernel/archive.py`.

SQLite tourne toujours. Postgres tourne dès que `CROSSPATCH_TEST_PG` pointe
une base joignable — sinon les cas Postgres sont marqués `skipped`, jamais
silencieusement passés.
"""
from __future__ import annotations

import os

import pytest

DSN = os.environ.get("CROSSPATCH_TEST_PG")
TABLES = ("events", "spend", "control", "documents", "generations")


def _nettoyer(dsn: str) -> None:
    """Table rase entre deux tests.

    `lock_timeout` court exprès : si un DROP attend, c'est qu'une connexion
    a laissé une transaction ouverte — on veut l'échec immédiat et lisible,
    pas une suite qui pend.
    """
    import psycopg
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("SET lock_timeout = '5s'")
        for t in TABLES:
            c.execute(f"DROP TABLE IF EXISTS {t}")


@pytest.fixture(params=["sqlite", "postgres"])
def adresse_archive(request, tmp_path):
    """Une adresse d'archive vierge, dans l'un ou l'autre dialecte."""
    if request.param == "sqlite":
        return str(tmp_path / "archive.db")
    if not DSN:
        pytest.skip("CROSSPATCH_TEST_PG non défini — cas Postgres non exécuté")
    _nettoyer(DSN)
    return DSN
