from backend.app.config import get_settings
from backend.app.persistence.database import is_postgres_target
from mcp_servers.common.database import initialize_demo_database


def main() -> None:
    settings = get_settings()
    initialize_demo_database(
        settings.database_target,
        settings.daypilot_timezone,
        force_reset=True,
    )
    location = (
        "the configured PostgreSQL database"
        if is_postgres_target(settings.database_target)
        else str(settings.database_target)
    )
    print(f"Reset DayPilot demo workspace at {location}")


if __name__ == "__main__":
    main()
