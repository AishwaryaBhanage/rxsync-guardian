"""Smoke test: the local Postgres from docker-compose is reachable."""

import os

import psycopg
from dotenv import load_dotenv

load_dotenv()


def test_select_one() -> None:
    dsn = os.environ.get("DATABASE_URL")
    assert dsn, "DATABASE_URL is not set -- copy .env.example to .env"
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1
