"""Conversation session store with LangGraph checkpoint persistence.

Provides a process-level singleton :class:`ConversationManager` that owns:

* a SQLite-backed LangGraph checkpointer (``SqliteSaver``) keyed by
  ``conversation_id`` -> ``thread_id`` so ReAct loop state (messages,
  evidence_pool, verdicts, ...) survives across requests;
* a ``conversation_turns`` table recording every query/answer pair (regardless
  of execution path) plus the parsed time constraint, used both as an audit log
  and as the source of inherited context for follow-up turns;
* LRU eviction of the oldest conversations once ``max_threads`` is exceeded.

The manager degrades gracefully: if the checkpointer dependency or the
database is unavailable, ``enabled`` flips to ``False`` and the rest of the
application falls back to stateless single-turn behaviour.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional


class ConversationManager:
    """Owns the checkpointer and the conversation_turns audit table."""

    def __init__(
        self,
        db_path: str,
        *,
        enabled: bool = True,
        history_window: int = 5,
        max_threads: int = 200,
    ) -> None:
        self.db_path = db_path
        self.enabled = bool(enabled)
        self.history_window = max(1, int(history_window))
        self.max_threads = max(1, int(max_threads))
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._saver: Optional[Any] = None

        if not self.enabled:
            return

        try:
            directory = os.path.dirname(os.path.abspath(db_path))
            os.makedirs(directory, exist_ok=True)
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=10000")
            self._init_tables()
            from langgraph.checkpoint.sqlite import SqliteSaver

            self._saver = SqliteSaver(self._conn)
        except Exception as exc:  # noqa: BLE001 - degrade to stateless on any failure
            print(f"[conversation] checkpointer disabled: {exc}")
            self.enabled = False
            self._conn = None
            self._saver = None

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------
    def _init_tables(self) -> None:
        assert self._conn is not None
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    conversation_id TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    query TEXT NOT NULL,
                    answer TEXT,
                    time_constraint TEXT,
                    topic_reset INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_turns_conv "
                "ON conversation_turns(conversation_id, turn_index)"
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Checkpointer access
    # ------------------------------------------------------------------
    @property
    def saver(self) -> Optional[Any]:
        return self._saver if self.enabled else None

    def has_checkpoint(self, conversation_id: str) -> bool:
        """Return True when a LangGraph checkpoint exists for the thread."""
        if not self.enabled or not self._saver or not conversation_id:
            return False
        try:
            config = {"configurable": {"thread_id": str(conversation_id)}}
            return self._saver.get_tuple(config) is not None
        except Exception:  # noqa: BLE001 - treat lookup failure as "no checkpoint"
            return False

    def delete_checkpoint(self, conversation_id: str) -> None:
        """Remove the checkpoint rows and recorded turns for a conversation."""
        if not self.enabled or not conversation_id:
            return
        cid = str(conversation_id)
        with self._lock:
            assert self._conn is not None
            existing_tables = {
                row[0]
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            try:
                # LangGraph checkpoint tables are best-effort: they are created
                # lazily on first checkpoint write and may not exist yet.
                if "checkpoints" in existing_tables:
                    self._conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (cid,))
                if "checkpoint_writes" in existing_tables:
                    self._conn.execute("DELETE FROM checkpoint_writes WHERE thread_id = ?", (cid,))
                self._conn.execute(
                    "DELETE FROM conversation_turns WHERE conversation_id = ?", (cid,)
                )
                self._conn.commit()
            except Exception as exc:  # noqa: BLE001 - eviction is best effort
                print(f"[conversation] delete_checkpoint failed: {exc}")

    # ------------------------------------------------------------------
    # Turn recording
    # ------------------------------------------------------------------
    def record_turn(
        self,
        conversation_id: str,
        query: str,
        answer: str,
        time_constraint: Optional[Any] = None,
        *,
        topic_reset: bool = False,
    ) -> int:
        """Append a turn for the conversation and return its 1-based index."""
        if not self.enabled or not conversation_id or not self._conn:
            return 0
        constraint_json = self._serialize_time_constraint(time_constraint)
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(turn_index), 0) AS m "
                    "FROM conversation_turns WHERE conversation_id = ?",
                    (str(conversation_id),),
                ).fetchone()
                next_index = int(row["m"]) + 1
                self._conn.execute(
                    "INSERT INTO conversation_turns "
                    "(conversation_id, turn_index, query, answer, time_constraint, topic_reset) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(conversation_id),
                        next_index,
                        query,
                        answer,
                        constraint_json,
                        1 if topic_reset else 0,
                    ),
                )
                self._conn.commit()
            except Exception as exc:  # noqa: BLE001 - recording is best effort
                print(f"[conversation] record_turn failed: {exc}")
                return 0
        self.evict_if_needed()
        return next_index

    def get_recent_turns(self, conversation_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Return the most recent turns (oldest first within the window)."""
        if not self.enabled or not conversation_id or not self._conn:
            return []
        window = limit if limit is not None else self.history_window
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT turn_index, query, answer, time_constraint, topic_reset, created_at "
                    "FROM conversation_turns WHERE conversation_id = ? "
                    "ORDER BY turn_index DESC LIMIT ?",
                    (str(conversation_id), int(window)),
                ).fetchall()
            except Exception:  # noqa: BLE001
                return []
        turns = [dict(r) for r in rows]
        turns.reverse()
        return turns

    def get_last_turn(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        recent = self.get_recent_turns(conversation_id, limit=1)
        return recent[-1] if recent else None

    def get_inherited_time_constraint(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Walk backwards to the most recent turn carrying a time constraint."""
        if not self.enabled or not conversation_id or not self._conn:
            return None
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT time_constraint FROM conversation_turns "
                    "WHERE conversation_id = ? AND time_constraint IS NOT NULL "
                    "ORDER BY turn_index DESC LIMIT 1",
                    (str(conversation_id),),
                ).fetchone()
            except Exception:  # noqa: BLE001
                return None
        if not row or not row["time_constraint"]:
            return None
        try:
            return json.loads(row["time_constraint"])
        except (json.JSONDecodeError, TypeError):
            return None

    def last_turn_is_topic_reset(self, conversation_id: str) -> bool:
        last = self.get_last_turn(conversation_id)
        return bool(last and last.get("topic_reset"))

    # ------------------------------------------------------------------
    # LRU governance
    # ------------------------------------------------------------------
    def evict_if_needed(self) -> None:
        """Evict least-recently-used conversations above ``max_threads``."""
        if not self.enabled or not self._conn:
            return
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT conversation_id, MAX(created_at) AS last_activity "
                    "FROM conversation_turns GROUP BY conversation_id "
                    "ORDER BY last_activity ASC"
                ).fetchall()
            except Exception:  # noqa: BLE001
                return
            if len(rows) <= self.max_threads:
                return
            evict_count = len(rows) - self.max_threads
            victims = [r["conversation_id"] for r in rows[:evict_count]]
        for victim in victims:
            self.delete_checkpoint(victim)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _serialize_time_constraint(time_constraint: Optional[Any]) -> Optional[str]:
        if time_constraint is None:
            return None
        if is_dataclass(time_constraint) and not isinstance(time_constraint, type):
            data = asdict(time_constraint)
        elif isinstance(time_constraint, dict):
            data = time_constraint
        else:
            return None
        # Only record when an actual time range was detected.
        if not data.get("days"):
            return None
        try:
            return json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError):
            return None


_singleton: Optional[ConversationManager] = None
_singleton_lock = threading.Lock()


def _load_config() -> Dict[str, Any]:
    config_path = os.environ.get("NLP_CONFIG_PATH", "config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001 - missing/invalid config -> stateless
        return {}


def get_conversation_manager() -> ConversationManager:
    """Return the process-level :class:`ConversationManager` singleton."""
    global _singleton
    if _singleton is not None:
        return _singleton
    with _singleton_lock:
        if _singleton is not None:
            return _singleton
        config = _load_config()
        conv_cfg = config.get("conversation") or {}
        _singleton = ConversationManager(
            db_path=conv_cfg.get("checkpoint_path", "./checkpoints/conversations.sqlite"),
            enabled=conv_cfg.get("enabled", True),
            history_window=conv_cfg.get("history_window", 5),
            max_threads=conv_cfg.get("max_threads", 200),
        )
    return _singleton


def reset_conversation_manager() -> None:
    """Reset the singleton (for tests)."""
    global _singleton
    with _singleton_lock:
        if _singleton is not None and _singleton._conn is not None:
            try:
                _singleton._conn.close()
            except Exception:  # noqa: BLE001
                pass
        _singleton = None
