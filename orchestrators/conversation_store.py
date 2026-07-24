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
            # Custom titles for the sidebar. Kept separate so the turns table
            # schema stays untouched even for databases created before this
            # feature shipped.
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_meta (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            # Full result payload so the sidebar can fully restore a turn
            # (workflow metadata, sources, timings, ...). Added via migration
            # so existing databases pick the column up on first run.
            self._ensure_column(
                "conversation_turns", "result_json", "TEXT"
            )
            self._conn.commit()

    def _ensure_column(self, table: str, column: str, decl: str) -> None:
        """Add ``column`` to ``table`` if it does not already exist."""
        assert self._conn is not None
        try:
            cols = {
                row[1]
                for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
        except Exception:  # noqa: BLE001 - treat lookup failure as "missing"
            cols = set()
        if column in cols:
            return
        try:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        except Exception as exc:  # noqa: BLE001 - column may already exist concurrently
            print(f"[conversation] migrate {table}.{column} skipped: {exc}")

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
                if "conversation_meta" in existing_tables:
                    self._conn.execute(
                        "DELETE FROM conversation_meta WHERE conversation_id = ?", (cid,)
                    )
                self._conn.commit()
            except Exception as exc:  # noqa: BLE001 - eviction is best effort
                print(f"[conversation] delete_checkpoint failed: {exc}")

    # ------------------------------------------------------------------
    # Sidebar listing & history retrieval
    # ------------------------------------------------------------------
    def list_conversations(self) -> List[Dict[str, Any]]:
        """Return conversations ordered by most recent activity.

        Each entry carries a display ``title`` (custom title when present,
        otherwise the first turn's query), the number of recorded turns, and
        the first/last activity timestamps.
        """
        if not self.enabled or not self._conn:
            return []
        with self._lock:
            try:
                rows = self._conn.execute(
                    """
                    SELECT
                        t.conversation_id AS conversation_id,
                        (SELECT query FROM conversation_turns
                           WHERE conversation_id = t.conversation_id
                           ORDER BY turn_index ASC LIMIT 1) AS first_query,
                        COUNT(*) AS turn_count,
                        MIN(t.created_at) AS created_at,
                        MAX(t.created_at) AS last_activity
                    FROM conversation_turns t
                    GROUP BY t.conversation_id
                    ORDER BY last_activity DESC
                    """
                ).fetchall()
            except Exception as exc:  # noqa: BLE001
                print(f"[conversation] list_conversations failed: {exc}")
                return []
            titles = self._load_titles()
        result: List[Dict[str, Any]] = []
        for row in rows:
            cid = row["conversation_id"]
            first_query = row["first_query"] or ""
            has_title = cid in titles
            title = titles[cid] if has_title else _derive_title(first_query)
            result.append({
                "conversation_id": cid,
                "title": title or "新会话",
                "custom_title": has_title,
                "turn_count": int(row["turn_count"] or 0),
                "created_at": row["created_at"],
                "last_activity": row["last_activity"],
            })
        return result

    def get_all_turns(self, conversation_id: str) -> List[Dict[str, Any]]:
        """Return every turn of a conversation, oldest first."""
        if not self.enabled or not conversation_id or not self._conn:
            return []
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT turn_index, query, answer, time_constraint, topic_reset, "
                    "      result_json, created_at "
                    "FROM conversation_turns WHERE conversation_id = ? "
                    "ORDER BY turn_index ASC",
                    (str(conversation_id),),
                ).fetchall()
            except Exception:  # noqa: BLE001
                return []
        return [_row_to_turn(r) for r in rows]

    def get_conversation_title(self, conversation_id: str) -> Optional[str]:
        """Return the custom title for a conversation, if any."""
        if not self.enabled or not conversation_id or not self._conn:
            return None
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT title FROM conversation_meta WHERE conversation_id = ?",
                    (str(conversation_id),),
                ).fetchone()
            except Exception:  # noqa: BLE001
                return None
        return row["title"] if row and row["title"] else None

    def set_conversation_title(self, conversation_id: str, title: str) -> bool:
        """Upsert a custom title for a conversation. Returns success."""
        if not self.enabled or not conversation_id or not self._conn:
            return False
        cleaned = (title or "").strip()
        if not cleaned:
            return False
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO conversation_meta (conversation_id, title, updated_at) "
                    "VALUES (?, ?, datetime('now')) "
                    "ON CONFLICT(conversation_id) DO UPDATE SET "
                    "title = excluded.title, updated_at = datetime('now')",
                    (str(conversation_id), cleaned[:200]),
                )
                self._conn.commit()
            except Exception as exc:  # noqa: BLE001
                print(f"[conversation] set_conversation_title failed: {exc}")
                return False
        return True

    def clear_conversation_title(self, conversation_id: str) -> None:
        """Remove a custom title so the sidebar falls back to the first query."""
        if not self.enabled or not conversation_id or not self._conn:
            return
        with self._lock:
            try:
                self._conn.execute(
                    "DELETE FROM conversation_meta WHERE conversation_id = ?",
                    (str(conversation_id),),
                )
                self._conn.commit()
            except Exception as exc:  # noqa: BLE001
                print(f"[conversation] clear_conversation_title failed: {exc}")

    def _load_titles(self) -> Dict[str, str]:
        assert self._conn is not None
        try:
            rows = self._conn.execute(
                "SELECT conversation_id, title FROM conversation_meta "
                "WHERE title IS NOT NULL AND title <> ''"
            ).fetchall()
        except Exception:  # noqa: BLE001
            return {}
        return {row["conversation_id"]: row["title"] for row in rows if row["title"]}

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
        result: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Append a turn for the conversation and return its 1-based index.

        When ``result`` is supplied it is persisted (in compacted form) so the
        sidebar can fully restore the turn — workflow metadata, sources,
        retrieved documents, timings and warnings alike.
        """
        if not self.enabled or not conversation_id or not self._conn:
            return 0
        constraint_json = self._serialize_time_constraint(time_constraint)
        result_json = _compact_result_json(result) if result else None
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
                    "(conversation_id, turn_index, query, answer, time_constraint, "
                    " topic_reset, result_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(conversation_id),
                        next_index,
                        query,
                        answer,
                        constraint_json,
                        1 if topic_reset else 0,
                        result_json,
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
                    "SELECT turn_index, query, answer, time_constraint, topic_reset, "
                    "      result_json, created_at "
                    "FROM conversation_turns WHERE conversation_id = ? "
                    "ORDER BY turn_index DESC LIMIT ?",
                    (str(conversation_id), int(window)),
                ).fetchall()
            except Exception:  # noqa: BLE001
                return []
        turns = [_row_to_turn(r) for r in rows]
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


# Cap how much of a turn's full result is persisted. The compacted payload keeps
# everything the sidebar needs to fully reconstruct a turn (workflow metadata,
# sources, retrieved docs, timings, warnings) while preventing unbounded growth
# from large extracted page bodies.
_MAX_RESULT_JSON_BYTES = 512 * 1024
_MAX_STRING_FIELD_CHARS = 4000
_LONG_STRING_FIELDS = ("content", "full_content", "text", "body", "raw", "snippet")


def _compact_result_json(result: Optional[Dict[str, Any]]) -> Optional[str]:
    """Serialize ``result`` to JSON, trimming oversized fields as needed."""
    if result is None:
        return None
    try:
        compacted = _compact_value(result)
        encoded = json.dumps(compacted, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None
    if len(encoded.encode("utf-8")) <= _MAX_RESULT_JSON_BYTES:
        return encoded
    # Too large: progressively drop the heaviest known fields and retry.
    trimmed = _compact_value(result)
    for _ in range(3):
        trimmed = _drop_heaviest(trimmed)
        try:
            encoded = json.dumps(trimmed, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return None
        if len(encoded.encode("utf-8")) <= _MAX_RESULT_JSON_BYTES:
            return encoded
    return encoded[:_MAX_RESULT_JSON_BYTES]


def _compact_value(value: Any) -> Any:
    """Recursively shorten over-long strings and keep the structure intact."""
    if isinstance(value, dict):
        return {str(k): _compact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_compact_value(item) for item in value]
    if isinstance(value, str):
        if len(value) > _MAX_STRING_FIELD_CHARS:
            return value[:_MAX_STRING_FIELD_CHARS] + "…"
        return value
    return value


def _drop_heaviest(value: Any) -> Any:
    """Remove the bulkiest content-bearing fields to fit the size budget."""
    if isinstance(value, dict):
        cleaned = {}
        for k, v in value.items():
            key = str(k).lower()
            if key in _LONG_STRING_FIELDS and isinstance(v, str) and len(v) > 200:
                cleaned[k] = v[:200] + "…"
            else:
                cleaned[k] = _drop_heaviest(v)
        return cleaned
    if isinstance(value, list):
        return [_drop_heaviest(item) for item in value]
    return value


def _row_to_turn(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert a stored turn row into the sidebar-friendly shape."""
    turn = dict(row)
    raw = turn.pop("result_json", None)
    turn["result"] = None
    if raw:
        try:
            turn["result"] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            turn["result"] = None
    return turn


def _derive_title(first_query: Optional[str], *, limit: int = 40) -> str:
    """Collapse a query into a compact sidebar title."""
    if not first_query:
        return "新会话"
    cleaned = " ".join(str(first_query).split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


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
