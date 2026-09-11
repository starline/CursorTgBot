from __future__ import annotations

import sqlite3
from pathlib import Path

SessionKey = tuple[int, int]  # (chat_id, thread_id); thread_id=0 for DM / no topics


class SessionStore:
    """Persist (chat_id, thread_id) → local agent_id across bot restarts."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS topic_sessions (
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                agent_id TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (chat_id, thread_id)
            )
            """
        )
        self._conn.commit()

    def get_agent_id(self, key: SessionKey) -> str | None:
        chat_id, thread_id = key
        row = self._conn.execute(
            "SELECT agent_id FROM topic_sessions WHERE chat_id = ? AND thread_id = ?",
            (chat_id, thread_id),
        ).fetchone()
        return str(row[0]) if row else None

    def set_agent_id(self, key: SessionKey, agent_id: str) -> None:
        chat_id, thread_id = key
        self._conn.execute(
            """
            INSERT INTO topic_sessions (chat_id, thread_id, agent_id, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(chat_id, thread_id) DO UPDATE SET
                agent_id = excluded.agent_id,
                updated_at = datetime('now')
            """,
            (chat_id, thread_id, agent_id),
        )
        self._conn.commit()

    def clear(self, key: SessionKey) -> None:
        chat_id, thread_id = key
        self._conn.execute(
            "DELETE FROM topic_sessions WHERE chat_id = ? AND thread_id = ?",
            (chat_id, thread_id),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
