from __future__ import annotations

import os
import sqlite3
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS demo_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mail_threads (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    participants TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mail_messages (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES mail_threads(id),
    sender TEXT NOT NULL,
    recipients TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    sent_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mail_drafts (
    id TEXT PRIMARY KEY,
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calendar_events (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    description TEXT,
    source TEXT NOT NULL DEFAULT 'demo'
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    notes TEXT,
    due_at TEXT,
    completed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspace_files (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    description TEXT NOT NULL,
    modified_at TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    content TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS x_posts (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    display_name TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    published_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('published', 'draft')),
    source TEXT NOT NULL DEFAULT 'demo'
);
"""


def database_path_from_env() -> Path:
    explicit = os.getenv("DAYPILOT_DATABASE_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    database_url = os.getenv("DATABASE_URL", "sqlite:///./data/daypilot.db")
    if not database_url.startswith("sqlite:///"):
        raise RuntimeError("Demo MCP servers require a sqlite:/// DATABASE_URL")
    raw_path = database_url.removeprefix("sqlite:///")
    path = Path(raw_path)
    if not path.is_absolute():
        project_root = Path(__file__).resolve().parents[2]
        path = project_root / path
    return path.resolve()


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialize_demo_database(
    database_path: Path,
    timezone_name: str = "Asia/Kolkata",
    *,
    force_reset: bool = False,
) -> None:
    with connect(database_path) as connection:
        connection.executescript(SCHEMA)
        seeded = connection.execute(
            "SELECT value FROM demo_metadata WHERE key = 'seed_version'"
        ).fetchone()
        if seeded and not force_reset:
            _seed_extensions(connection, timezone_name)
            return
        for table in (
            "mail_messages",
            "mail_threads",
            "mail_drafts",
            "calendar_events",
            "tasks",
            "workspace_files",
            "x_posts",
            "demo_metadata",
        ):
            connection.execute(f"DELETE FROM {table}")
        _seed(connection, timezone_name)
        connection.commit()


def ensure_demo_database_schema(database_path: Path) -> None:
    """Create local service tables without inserting fictional demo records."""
    with connect(database_path) as connection:
        connection.executescript(SCHEMA)
        connection.commit()


def _seed(connection: sqlite3.Connection, timezone_name: str) -> None:
    timezone = ZoneInfo(timezone_name)
    now = datetime.now(timezone).replace(second=0, microsecond=0)
    today = now.date()
    tomorrow = today + timedelta(days=1)
    tomorrow_label = tomorrow.strftime("%A, %B %-d")

    threads = [
        (
            "thread-rahul-interview",
            "Interview confirmed — Backend Engineer",
            "rahul.kapoor@northstar.example,alex.morgan@example.com",
            (now - timedelta(hours=3)).isoformat(),
        ),
        (
            "thread-platform-review",
            "Project Meridian — platform review notes",
            "maya.chen@acme.example,alex.morgan@example.com",
            (now - timedelta(days=1)).isoformat(),
        ),
        (
            "thread-design-sync",
            "Design systems sync moved to Friday",
            "noah.williams@acme.example,alex.morgan@example.com",
            (now - timedelta(days=2)).isoformat(),
        ),
        (
            "thread-newsletter",
            "The engineering briefing — August edition",
            "briefing@newsletter.example,alex.morgan@example.com",
            (now - timedelta(days=4)).isoformat(),
        ),
    ]
    connection.executemany(
        "INSERT INTO mail_threads(id, subject, participants, updated_at) VALUES (?, ?, ?, ?)",
        threads,
    )

    messages = [
        (
            "msg-rahul-001",
            "thread-rahul-interview",
            "rahul.kapoor@northstar.example",
            "alex.morgan@example.com",
            "Interview confirmed — Backend Engineer",
            (
                f"Hi Alex,\n\nYour Backend Engineer interview is confirmed for {tomorrow_label} "
                "at 11:00 AM IST. We’ll meet over Northstar Meet for 60 minutes. The conversation "
                "will cover API design, reliability trade-offs, and a recent project you led.\n\n"
                "Meeting: https://meet.northstar.example/interview-4821\n\nBest,\nRahul Kapoor\n"
                "Technical Recruiting, Northstar Labs"
            ),
            (now - timedelta(hours=4)).isoformat(),
        ),
        (
            "msg-rahul-002",
            "thread-rahul-interview",
            "alex.morgan@example.com",
            "rahul.kapoor@northstar.example",
            "Re: Interview confirmed — Backend Engineer",
            "Thanks Rahul — confirmed. I’m looking forward to speaking with the team tomorrow.",
            (now - timedelta(hours=3)).isoformat(),
        ),
        (
            "msg-platform-001",
            "thread-platform-review",
            "maya.chen@acme.example",
            "alex.morgan@example.com",
            "Project Meridian — platform review notes",
            (
                "The review went well. Please add the queue backpressure decision "
                "to the architecture notes."
            ),
            (now - timedelta(days=1)).isoformat(),
        ),
        (
            "msg-design-001",
            "thread-design-sync",
            "noah.williams@acme.example",
            "alex.morgan@example.com",
            "Design systems sync moved to Friday",
            "The component review is now Friday at 3 PM. No action needed today.",
            (now - timedelta(days=2)).isoformat(),
        ),
        (
            "msg-newsletter-001",
            "thread-newsletter",
            "briefing@newsletter.example",
            "alex.morgan@example.com",
            "The engineering briefing — August edition",
            (
                "This month: database indexing, incident retrospectives, "
                "and five open-source releases."
            ),
            (now - timedelta(days=4)).isoformat(),
        ),
    ]
    connection.executemany(
        """
        INSERT INTO mail_messages(
            id, thread_id, sender, recipients, subject, body, sent_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        messages,
    )

    morning_start = datetime.combine(tomorrow, time(9, 0), timezone)
    interview_start = datetime.combine(tomorrow, time(11, 0), timezone)
    today_sync = datetime.combine(today, time(10, 0), timezone)
    events = [
        (
            "event-morning-team-sync",
            "Platform team sync",
            morning_start.isoformat(),
            (morning_start + timedelta(hours=1)).isoformat(),
            "Weekly engineering sync",
            "seed",
        ),
        (
            "event-rahul-interview",
            "Northstar Labs — Backend Engineer interview",
            interview_start.isoformat(),
            (interview_start + timedelta(hours=1)).isoformat(),
            "Interview with Rahul Kapoor · Northstar Meet",
            "seed",
        ),
        (
            "event-today-product-sync",
            "Product delivery sync",
            today_sync.isoformat(),
            (today_sync + timedelta(minutes=30)).isoformat(),
            "Milestone review",
            "seed",
        ),
    ]
    connection.executemany(
        """
        INSERT INTO calendar_events(id, title, start_at, end_at, description, source)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        events,
    )

    task_rows = [
        (
            "task-expense-report",
            "Submit monthly expense report",
            "Attach travel receipts",
            datetime.combine(tomorrow, time(17, 0), timezone).isoformat(),
            0,
            (now - timedelta(days=2)).isoformat(),
        ),
        (
            "task-review-pr",
            "Review queue backpressure PR",
            "Focus on retry semantics",
            datetime.combine(today, time(17, 30), timezone).isoformat(),
            0,
            (now - timedelta(days=1)).isoformat(),
        ),
        (
            "task-book-dentist",
            "Book dentist appointment",
            None,
            None,
            0,
            (now - timedelta(days=3)).isoformat(),
        ),
    ]
    connection.executemany(
        """
        INSERT INTO tasks(id, title, notes, due_at, completed, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        task_rows,
    )
    connection.executemany(
        "INSERT INTO demo_metadata(key, value) VALUES (?, ?)",
        [
            ("seed_version", "2"),
            ("anchor_date", today.isoformat()),
            ("timezone", timezone_name),
        ],
    )
    _seed_extensions(connection, timezone_name)


def _seed_extensions(connection: sqlite3.Connection, timezone_name: str) -> None:
    """Add the small Files/X demo corpus without resetting existing demo state."""
    timezone = ZoneInfo(timezone_name)
    now = datetime.now(timezone).replace(second=0, microsecond=0)
    files = [
        (
            "file-resume-latest",
            "Alex Morgan — Resume.md",
            "text/markdown",
            "Current resume for Alex Morgan, a backend and platform engineer.",
            (now - timedelta(days=2)).isoformat(),
            (
                "# Alex Morgan\n\n"
                "Backend and platform engineer focused on reliable APIs, workflow orchestration, "
                "and developer tooling.\n\n"
                "## Selected work\n"
                "- Built event-driven services with explicit reliability and audit boundaries.\n"
                "- Led cross-functional launches from technical brief through customer rollout.\n"
                "- Interested in practical MCP and human-in-the-loop agent systems."
            ),
        ),
        (
            "file-interview-prep",
            "Interview preparation notes.md",
            "text/markdown",
            "Preparation notes for backend and platform engineering conversations.",
            (now - timedelta(days=4)).isoformat(),
            (
                "# Interview preparation\n\n"
                "Focus on API design, reliability trade-offs, and one recent project with "
                "measurable impact. Prepare a concise story about queue backpressure, testing "
                "strategy, and how operational feedback changed the design.\n\n"
                "Questions to ask: how does the team measure service health, and how are "
                "architecture decisions recorded?"
            ),
        ),
        (
            "file-daypilot-project-brief",
            "DayPilot project brief.md",
            "text/markdown",
            "Product brief describing DayPilot's MCP-powered operations workflow.",
            (now - timedelta(days=6)).isoformat(),
            (
                "# DayPilot project brief\n\n"
                "DayPilot is an MCP-powered personal operations agent. It gathers grounded "
                "context from connected services, builds an auditable plan, and pauses before "
                "external changes.\n\n"
                "The initial capability domains are communication, scheduling, task execution, "
                "private workspace documents, and public social context. Reads can run "
                "automatically; writes require explicit human approval and are verified after "
                "execution."
            ),
        ),
        (
            "file-daypilot-launch-notes",
            "DayPilot launch notes.md",
            "text/markdown",
            "Launch notes for the DayPilot local demo and its approval boundary.",
            (now - timedelta(days=1)).isoformat(),
            (
                "# DayPilot launch notes\n\n"
                "The demo should explain one simple promise: DayPilot turns a goal into grounded, "
                "reviewable action across a personal workspace.\n\n"
                "Launch message: connect the facts first, show the proposed changes clearly, and "
                "keep the user in control of every write. The public story should highlight MCP "
                "interoperability, practical orchestration, and approval-gated publishing rather "
                "than autonomous side effects.\n\n"
                "Next step: share a short update about the launch only after its wording has been "
                "reviewed."
            ),
        ),
        (
            "file-launch-meeting-notes",
            "Product launch meeting notes.md",
            "text/markdown",
            "Notes from the fictional product launch planning meeting.",
            (now - timedelta(days=8)).isoformat(),
            (
                "# Product launch meeting\n\n"
                "The team agreed to keep the local demo small, believable, and easy to reset. "
                "The launch walkthrough should connect a project brief to mail context, available "
                "calendar time, open tasks, and a draft public update.\n\n"
                "Any public post remains a proposal until a person approves the exact text."
            ),
        ),
    ]
    connection.executemany(
        """
        INSERT OR IGNORE INTO workspace_files(
            id, filename, file_type, description, modified_at, size_bytes, content
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [(*row[:5], len(row[5].encode("utf-8")), row[5]) for row in files],
    )

    posts = [
        (
            "x-post-mcp-001",
            "mira_chen",
            "Mira Chen",
            (
                "MCP is most useful when each capability stays independently discoverable and "
                "the agent can explain which tool grounded an answer."
            ),
            (now - timedelta(hours=5)).isoformat(),
        ),
        (
            "x-post-ops-002",
            "jonas_reid",
            "Jonas Reid",
            (
                "MCP workflows are easier to trust when writes are approval-gated: read the "
                "workspace, propose the change, then verify what actually happened."
            ),
            (now - timedelta(days=1, hours=2)).isoformat(),
        ),
        (
            "x-post-launch-003",
            "lena_ops",
            "Lena Ortiz",
            (
                "A small launch demo can say a lot when its context is real, its tools are "
                "visible, "
                "and its public updates remain reviewable."
            ),
            (now - timedelta(days=2)).isoformat(),
        ),
        (
            "x-post-unrelated-004",
            "arun_builds",
            "Arun Shah",
            "A quiet weekend is a good reminder to make space for focused work and long walks.",
            (now - timedelta(days=3)).isoformat(),
        ),
    ]
    connection.executemany(
        """
        INSERT OR IGNORE INTO x_posts(
            id, username, display_name, text, created_at, published_at, status, source
        ) VALUES (?, ?, ?, ?, ?, ?, 'published', 'demo')
        """,
        [(*post, post[4]) for post in posts],
    )
    connection.execute(
        "INSERT OR REPLACE INTO demo_metadata(key, value) VALUES ('seed_version', '2')"
    )
