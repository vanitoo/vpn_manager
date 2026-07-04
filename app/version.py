from __future__ import annotations

APP_NAME = "Remnawave VPN Bot"
APP_VERSION = "0.1.0"
BUILD_DATE = "2026-07-05"

# Version of the SQLite database structure expected by this application.
DB_SCHEMA_VERSION = 1


def version_line() -> str:
    return f"{APP_NAME} v{APP_VERSION}"
