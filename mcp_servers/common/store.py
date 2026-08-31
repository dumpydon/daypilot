from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from mcp_servers.common.database import connect, initialize_demo_database

STOP_WORDS = {
    "a",
    "an",
    "and",
    "for",
    "from",
    "last",
    "me",
    "my",
    "the",
    "to",
    "with",
}
SEARCH_NOISE = STOP_WORDS | {
    "all",
    "available",
    "does",
    "document",
    "documents",
    "about",
    "demo",
    "exist",
    "file",
    "files",
    "find",
    "latest",
    "list",
    "my",
    "no",
    "not",
    "notes",
    "read",
    "recent",
    "say",
    "show",
    "that",
    "tell",
    "the",
    "what",
    "workspace",
    "in",
}


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class DemoServiceStore:
    """SQLite-backed service state shared by short-lived stdio MCP processes."""

    def __init__(self, database_path: Path, timezone_name: str = "Asia/Kolkata") -> None:
        self.database_path = database_path
        initialize_demo_database(database_path, timezone_name)

    def search_mail(self, query: str, limit: int = 10) -> dict[str, Any]:
        limit = max(1, min(limit, 25))
        tokens = [
            token
            for token in re.findall(r"[a-z0-9@.]+", query.lower())
            if len(token) > 1 and token not in STOP_WORDS
        ]
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT
                    t.id AS thread_id,
                    t.subject,
                    t.participants,
                    t.updated_at,
                    GROUP_CONCAT(m.body, ' ') AS bodies,
                    COUNT(m.id) AS message_count
                FROM mail_threads t
                JOIN mail_messages m ON m.thread_id = t.id
                GROUP BY t.id
                ORDER BY t.updated_at DESC
                """
            ).fetchall()
        ranked: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            item = dict(row)
            haystack = " ".join([item["subject"], item["participants"], item.pop("bodies")]).lower()
            score = sum(
                3 if token in item["subject"].lower() else 1
                for token in tokens
                if token in haystack
            )
            if not tokens or score:
                ranked.append((score, item))
        ranked.sort(key=lambda value: (value[0], value[1]["updated_at"]), reverse=True)
        return {
            "query": query,
            "threads": [item for _, item in ranked[:limit]],
            "count": len(ranked[:limit]),
        }

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        with connect(self.database_path) as connection:
            thread = connection.execute(
                "SELECT * FROM mail_threads WHERE id = ?", (thread_id,)
            ).fetchone()
            if thread is None:
                raise ValueError(f"Mail thread {thread_id!r} was not found")
            messages = connection.execute(
                "SELECT * FROM mail_messages WHERE thread_id = ? ORDER BY sent_at",
                (thread_id,),
            ).fetchall()
        return {**dict(thread), "messages": [dict(message) for message in messages]}

    def get_message(self, message_id: str) -> dict[str, Any]:
        with connect(self.database_path) as connection:
            message = connection.execute(
                "SELECT * FROM mail_messages WHERE id = ?", (message_id,)
            ).fetchone()
            if message is not None:
                return {"kind": "message", **dict(message)}
            draft = connection.execute(
                "SELECT * FROM mail_drafts WHERE id = ?", (message_id,)
            ).fetchone()
        if draft is None:
            raise ValueError(f"Mail message or draft {message_id!r} was not found")
        return {"kind": "draft", **dict(draft)}

    def create_draft(self, recipient: str, subject: str, body: str) -> dict[str, Any]:
        draft_id = f"draft-{uuid4().hex[:12]}"
        created_at = datetime.now().astimezone().isoformat()
        with connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO mail_drafts(id, recipient, subject, body, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (draft_id, recipient, subject, body, created_at),
            )
            connection.commit()
        return {
            "id": draft_id,
            "recipient": recipient,
            "subject": subject,
            "body": body,
            "created_at": created_at,
            "status": "saved",
        }

    def list_events(self, start: str, end: str) -> dict[str, Any]:
        start_at = _parse_datetime(start)
        end_at = _parse_datetime(end)
        if end_at <= start_at:
            raise ValueError("Calendar range end must be after start")
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM calendar_events
                WHERE start_at < ? AND end_at > ?
                ORDER BY start_at
                """,
                (end_at.isoformat(), start_at.isoformat()),
            ).fetchall()
        return {
            "start": start_at.isoformat(),
            "end": end_at.isoformat(),
            "events": [dict(row) for row in rows],
            "count": len(rows),
        }

    def find_free_slots(
        self,
        start: str,
        end: str,
        duration_minutes: int,
    ) -> dict[str, Any]:
        start_at = _round_up(_parse_datetime(start), 15)
        end_at = _parse_datetime(end)
        if not 15 <= duration_minutes <= 480:
            raise ValueError("duration_minutes must be between 15 and 480")
        events = self.list_events(start_at.isoformat(), end_at.isoformat())["events"]
        duration = timedelta(minutes=duration_minutes)
        cursor = start_at
        slots: list[dict[str, str]] = []
        for event in events:
            event_start = _parse_datetime(event["start_at"])
            event_end = _parse_datetime(event["end_at"])
            while cursor + duration <= min(event_start, end_at) and len(slots) < 5:
                slots.append({"start": cursor.isoformat(), "end": (cursor + duration).isoformat()})
                cursor += timedelta(minutes=30)
            cursor = max(cursor, event_end)
        while cursor + duration <= end_at and len(slots) < 5:
            slots.append({"start": cursor.isoformat(), "end": (cursor + duration).isoformat()})
            cursor += timedelta(minutes=30)
        return {
            "start": start_at.isoformat(),
            "end": end_at.isoformat(),
            "duration_minutes": duration_minutes,
            "slots": slots,
            "count": len(slots),
        }

    def create_event(
        self,
        title: str,
        start: str,
        end: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        start_at = _parse_datetime(start)
        end_at = _parse_datetime(end)
        if end_at <= start_at:
            raise ValueError("Event end must be after start")
        conflicts = self.list_events(start_at.isoformat(), end_at.isoformat())["events"]
        if conflicts:
            names = ", ".join(event["title"] for event in conflicts)
            raise ValueError(f"Event conflicts with: {names}")
        event_id = f"event-{uuid4().hex[:12]}"
        result = {
            "id": event_id,
            "title": title,
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "description": description,
            "source": "daypilot",
        }
        with connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO calendar_events(id, title, start_at, end_at, description, source)
                VALUES (:id, :title, :start_at, :end_at, :description, :source)
                """,
                result,
            )
            connection.commit()
        return {**result, "status": "created"}

    def list_tasks(self) -> dict[str, Any]:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM tasks ORDER BY completed, COALESCE(due_at, '9999'), created_at"
            ).fetchall()
        tasks = [{**dict(row), "completed": bool(row["completed"])} for row in rows]
        return {"tasks": tasks, "count": len(tasks)}

    def create_task(
        self,
        title: str,
        notes: str | None = None,
        due_at: str | None = None,
    ) -> dict[str, Any]:
        normalized_due = _parse_datetime(due_at).isoformat() if due_at else None
        task_id = f"task-{uuid4().hex[:12]}"
        created_at = datetime.now().astimezone().isoformat()
        task = {
            "id": task_id,
            "title": title,
            "notes": notes,
            "due_at": normalized_due,
            "completed": False,
            "created_at": created_at,
        }
        with connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO tasks(id, title, notes, due_at, completed, created_at)
                VALUES (:id, :title, :notes, :due_at, 0, :created_at)
                """,
                task,
            )
            connection.commit()
        return {**task, "status": "created"}

    def create_task_batch(self, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        if not 1 <= len(tasks) <= 20:
            raise ValueError("A task batch must contain between 1 and 20 tasks")
        created = [
            self.create_task(
                title=str(task["title"]),
                notes=task.get("notes"),
                due_at=task.get("due_at"),
            )
            for task in tasks
        ]
        return {"tasks": created, "count": len(created), "status": "created"}

    def complete_task(self, task_id: str) -> dict[str, Any]:
        with connect(self.database_path) as connection:
            cursor = connection.execute(
                "UPDATE tasks SET completed = 1 WHERE id = ? AND completed = 0", (task_id,)
            )
            connection.commit()
            task = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise ValueError(f"Task {task_id!r} was not found")
        return {
            **dict(task),
            "completed": True,
            "changed": cursor.rowcount == 1,
            "status": "completed",
        }

    def search_files(self, query: str, limit: int = 10) -> dict[str, Any]:
        """Search only the controlled workspace-file corpus and return safe metadata."""
        limit = max(1, min(limit, 25))
        tokens = _search_tokens(query)
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT id, filename, file_type, description, modified_at, size_bytes, content
                FROM workspace_files
                ORDER BY modified_at DESC
                """
            ).fetchall()
        ranked: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            item = dict(row)
            haystack = " ".join((item["filename"], item["description"], item["content"])).lower()
            filename = item["filename"].lower()
            description = item["description"].lower()
            score = sum(
                4
                if _contains_token(token, filename)
                else 2
                if _contains_token(token, description)
                else 1
                for token in tokens
                if _contains_token(token, haystack)
            )
            if not query.strip() or (tokens and score):
                ranked.append((score, item))
        ranked.sort(key=lambda value: (value[0], value[1]["modified_at"]), reverse=True)
        files = [_file_metadata(item) for _, item in ranked[:limit]]
        return {"query": query, "files": files, "count": len(files), "source": "demo"}

    def list_files(self, query: str | None = None, limit: int = 25) -> dict[str, Any]:
        """List controlled workspace files without exposing host paths or file contents."""
        if query and query.strip():
            return self.search_files(query, limit)
        limit = max(1, min(limit, 50))
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT id, filename, file_type, description, modified_at, size_bytes
                FROM workspace_files
                ORDER BY modified_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        files = [dict(row) for row in rows]
        return {"files": files, "count": len(files), "source": "demo"}

    def get_file_metadata(self, file_id: str) -> dict[str, Any]:
        _validate_file_id(file_id)
        with connect(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT id, filename, file_type, description, modified_at, size_bytes
                FROM workspace_files WHERE id = ?
                """,
                (file_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Controlled file {file_id!r} was not found")
        return {**dict(row), "source": "demo"}

    def read_file(self, file_id: str) -> dict[str, Any]:
        _validate_file_id(file_id)
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM workspace_files WHERE id = ?", (file_id,)
            ).fetchone()
        if row is None:
            raise ValueError(f"Controlled file {file_id!r} was not found")
        item = dict(row)
        return {**_file_metadata(item), "content": item["content"], "source": "demo"}

    def search_posts(self, query: str, limit: int = 10) -> dict[str, Any]:
        """Search published posts in the fictional public X corpus."""
        limit = max(1, min(limit, 25))
        tokens = _search_tokens(query)
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM x_posts
                WHERE status = 'published'
                ORDER BY created_at DESC
                """
            ).fetchall()
        ranked: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            item = dict(row)
            haystack = " ".join((item["username"], item["display_name"], item["text"])).lower()
            score = sum(
                3 if _contains_token(token, item["text"].lower()) else 1
                for token in tokens
                if _contains_token(token, haystack)
            )
            if not query.strip() or (tokens and score):
                ranked.append((score, item))
        ranked.sort(key=lambda value: (value[0], value[1]["created_at"]), reverse=True)
        posts = [_post_payload(item) for _, item in ranked[:limit]]
        return {"query": query, "posts": posts, "count": len(posts), "source": "demo"}

    def get_post(self, post_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"x-(?:post|draft)-[a-z0-9-]+", post_id):
            raise ValueError("Post ID must be a controlled X post identifier")
        with connect(self.database_path) as connection:
            row = connection.execute("SELECT * FROM x_posts WHERE id = ?", (post_id,)).fetchone()
        if row is None:
            raise ValueError(f"X post {post_id!r} was not found in the demo store")
        return _post_payload(dict(row))

    def get_user_posts(self, username: str, limit: int = 10) -> dict[str, Any]:
        limit = max(1, min(limit, 25))
        normalized = username.strip().lstrip("@").lower()
        if not normalized:
            raise ValueError("username must not be empty")
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM x_posts
                WHERE status = 'published' AND lower(username) = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (normalized, limit),
            ).fetchall()
        posts = [_post_payload(dict(row)) for row in rows]
        return {"username": normalized, "posts": posts, "count": len(posts), "source": "demo"}

    def create_post_draft(self, text: str) -> dict[str, Any]:
        normalized = _validate_post_text(text)
        draft_id = f"x-draft-{uuid4().hex[:12]}"
        created_at = datetime.now().astimezone().isoformat()
        with connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO x_posts(
                    id, username, display_name, text, created_at, published_at, status, source
                ) VALUES (?, 'daypilot_demo', 'DayPilot demo', ?, ?, NULL, 'draft', 'demo')
                """,
                (draft_id, normalized, created_at),
            )
            connection.commit()
            row = connection.execute("SELECT * FROM x_posts WHERE id = ?", (draft_id,)).fetchone()
        return _post_payload(dict(row)) if row else {"id": draft_id, "status": "draft"}

    def publish_post(self, text: str, draft_id: str | None = None) -> dict[str, Any]:
        normalized_draft_id = draft_id.strip() if draft_id else None
        normalized_text = text.strip()
        with connect(self.database_path) as connection:
            existing = None
            if normalized_draft_id:
                if not re.fullmatch(r"x-draft-[a-z0-9-]+", normalized_draft_id):
                    raise ValueError("draft_id must identify a controlled X draft")
                existing = connection.execute(
                    "SELECT * FROM x_posts WHERE id = ? AND status = 'draft'",
                    (normalized_draft_id,),
                ).fetchone()
                if existing is None:
                    raise ValueError(f"X draft {normalized_draft_id!r} was not found")
                if not normalized_text:
                    normalized_text = existing["text"]
            normalized_text = _validate_post_text(normalized_text)
            published_at = datetime.now().astimezone().isoformat()
            if existing is not None:
                connection.execute(
                    """
                    UPDATE x_posts SET text = ?, published_at = ?, status = 'published'
                    WHERE id = ?
                    """,
                    (normalized_text, published_at, normalized_draft_id),
                )
                post_id = normalized_draft_id
            else:
                post_id = f"x-post-{uuid4().hex[:12]}"
                connection.execute(
                    """
                    INSERT INTO x_posts(
                        id, username, display_name, text, created_at, published_at, status, source
                    ) VALUES (?, 'daypilot_demo', 'DayPilot demo', ?, ?, ?, 'published', 'demo')
                    """,
                    (post_id, normalized_text, published_at, published_at),
                )
            connection.commit()
            row = connection.execute("SELECT * FROM x_posts WHERE id = ?", (post_id,)).fetchone()
        return _post_payload(dict(row)) if row else {"id": post_id, "status": "published"}


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Datetime values must include a timezone offset")
    return parsed


def _round_up(value: datetime, minutes: int) -> datetime:
    discard = timedelta(
        minutes=value.minute % minutes, seconds=value.second, microseconds=value.microsecond
    )
    if discard:
        value += timedelta(minutes=minutes) - discard
    return value


def _search_tokens(query: str) -> list[str]:
    tokens: list[str] = []
    for raw_token in re.findall(r"[a-z0-9_@.-]+", query.lower()):
        token = raw_token.strip(".-")
        if len(token) > 1 and token not in SEARCH_NOISE:
            tokens.append(token)
    return tokens


def _contains_token(token: str, text: str) -> bool:
    if len(token) <= 2:
        return bool(re.search(rf"(?<![a-z0-9_]){re.escape(token)}(?![a-z0-9_])", text))
    return token in text


def _file_metadata(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item[key]
        for key in ("id", "filename", "file_type", "description", "modified_at", "size_bytes")
    }


def _validate_file_id(file_id: str) -> None:
    if not re.fullmatch(r"file-[a-z0-9-]+", file_id):
        raise ValueError("File ID must be a controlled identifier; host paths are not accepted")


def _validate_post_text(text: str) -> str:
    normalized = text.strip()
    if not normalized:
        raise ValueError("Post text must not be empty")
    if len(normalized) > 280:
        raise ValueError("Post text must be 280 characters or fewer")
    return normalized


def _post_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "username": item["username"],
        "display_name": item["display_name"],
        "text": item["text"],
        "created_at": item["created_at"],
        "published_at": item["published_at"],
        "status": item["status"],
        "source": item["source"],
    }
