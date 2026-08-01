"""Forward-only SQL migrations with a tracked history.

Numbered .sql files in backend/migrations/ are applied in filename order and
recorded in a schema_migrations table, so each runs exactly once. Idempotent:
re-running applies only new files. Runs automatically on API startup (main.py)
and can be run standalone:  python -m app.migrate

This replaces the scattered ALTER statements that relied on create_all + ad-hoc
psql; it gives existing PostgreSQL installations a proper, ordered upgrade path.
"""
from __future__ import annotations
from pathlib import Path

from sqlalchemy import text

from app.db.session import engine

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _statements(sql: str) -> list[str]:
    # drop comment-only lines, then split on ';'
    body = "\n".join(ln for ln in sql.splitlines() if not ln.strip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def run() -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP DEFAULT now())"))
        applied = {r[0] for r in conn.execute(text("SELECT version FROM schema_migrations"))}

    if not MIGRATIONS_DIR.is_dir():
        print("no migrations directory"); return
    pending = [f for f in sorted(MIGRATIONS_DIR.glob("*.sql")) if f.name not in applied]
    for f in pending:
        with engine.begin() as conn:
            for stmt in _statements(f.read_text(encoding="utf-8")):
                conn.execute(text(stmt))
            conn.execute(text("INSERT INTO schema_migrations (version) VALUES (:v)"),
                         {"v": f.name})
        print(f"applied migration {f.name}")
    print(f"migrations up to date ({len(applied) + len(pending)} total)")


if __name__ == "__main__":
    run()
