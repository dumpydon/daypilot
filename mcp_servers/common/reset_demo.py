from backend.app.config import get_settings
from mcp_servers.common.database import initialize_demo_database


def main() -> None:
    settings = get_settings()
    initialize_demo_database(
        settings.database_path,
        settings.daypilot_timezone,
        force_reset=True,
    )
    print(f"Reset DayPilot demo workspace at {settings.database_path}")


if __name__ == "__main__":
    main()
