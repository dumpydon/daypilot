from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.app.config import Settings
from backend.app.mcp.gateway import MCPGateway
from mcp_servers.common.database import initialize_demo_database
from mcp_servers.common.store import DemoServiceStore


@pytest.fixture
def store(tmp_path: Path) -> DemoServiceStore:
    database_path = tmp_path / "services.db"
    initialize_demo_database(database_path, "Asia/Kolkata")
    return DemoServiceStore(database_path)


def test_seed_mail_is_deterministic_and_grounded(store: DemoServiceStore) -> None:
    search = store.search_mail("Rahul interview")
    assert search["count"] == 1
    thread = store.get_thread(search["threads"][0]["thread_id"])
    assert thread["id"] == "thread-rahul-interview"
    assert "11:00 AM IST" in thread["messages"][0]["body"]


def test_mail_draft_is_saved_but_not_sent(store: DemoServiceStore) -> None:
    draft = store.create_draft("rahul@example.test", "Thank you", "Draft body")
    persisted = store.get_message(draft["id"])
    assert draft["status"] == "saved"
    assert persisted["kind"] == "draft"
    assert persisted["recipient"] == "rahul@example.test"


def test_calendar_free_slots_and_conflicts_are_real(store: DemoServiceStore) -> None:
    timezone = ZoneInfo("Asia/Kolkata")
    start = datetime.now(timezone).replace(hour=19, minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=3)
    slots = store.find_free_slots(start.isoformat(), end.isoformat(), 90)
    assert slots["slots"]
    created = store.create_event(
        "Interview preparation",
        slots["slots"][0]["start"],
        slots["slots"][0]["end"],
    )
    assert created["status"] == "created"
    with pytest.raises(ValueError, match="conflicts"):
        store.create_event("Conflict", created["start_at"], created["end_at"])


def test_task_batch_mutates_shared_store(store: DemoServiceStore) -> None:
    before = store.list_tasks()["count"]
    created = store.create_task_batch(
        [
            {"title": "Review role", "notes": None, "due_at": None},
            {"title": "Prepare examples", "notes": None, "due_at": None},
        ]
    )
    assert created["count"] == 2
    assert store.list_tasks()["count"] == before + 2


def test_files_search_and_read_are_deterministic_and_controlled(store: DemoServiceStore) -> None:
    search = store.search_files("Find my latest resume.")
    assert search["count"] == 1
    assert search["files"][0]["id"] == "file-resume-latest"
    assert "Alex Morgan" in store.read_file("file-resume-latest")["content"]
    assert "/" not in search["files"][0]["id"]


def test_files_reject_host_paths_and_unknown_ids(store: DemoServiceStore) -> None:
    with pytest.raises(ValueError, match="controlled"):
        store.read_file("../../etc/passwd")
    with pytest.raises(ValueError, match="was not found"):
        store.get_file_metadata("file-does-not-exist")


def test_x_search_and_user_reads_are_grounded(store: DemoServiceStore) -> None:
    search = store.search_posts("MCP")
    assert search["count"] == 2
    assert all(post["status"] == "published" for post in search["posts"])
    assert store.get_user_posts("@mira_chen")["posts"][0]["id"] == "x-post-mcp-001"
    assert store.get_user_posts("nobody_here")["posts"] == []


def test_x_draft_and_publish_persist_in_the_demo_store(store: DemoServiceStore) -> None:
    draft = store.create_post_draft("A grounded DayPilot update.")
    assert draft["status"] == "draft"
    assert store.get_post(draft["id"])["status"] == "draft"
    published = store.publish_post("A reviewed DayPilot update.", draft["id"])
    assert published["id"] == draft["id"]
    assert published["status"] == "published"
    assert store.get_post(draft["id"])["text"] == "A reviewed DayPilot update."


@pytest.mark.asyncio
async def test_gateway_discovers_five_independent_mcp_servers(tmp_path: Path) -> None:
    database_path = tmp_path / "gateway.db"
    initialize_demo_database(database_path, "Asia/Kolkata")
    settings = Settings(
        database_url=f"sqlite:///{database_path}",
        openai_api_key=None,
        daypilot_timezone="Asia/Kolkata",
    )
    gateway = MCPGateway(settings)
    tools = await gateway.discover(force=True)
    assert [server["name"] for server in gateway.catalog()] == [
        "mail",
        "calendar",
        "tasks",
        "files",
        "x",
    ]
    assert len(tools) == 20
    assert all(server["connected"] for server in gateway.catalog())
