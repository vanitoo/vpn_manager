from __future__ import annotations

from datetime import date

APP_NAME = "KamenevaBook Bot"
APP_VERSION = "3.3.1"
BUILD_DATE = "2026-06-26"

# Version of the SQLite database structure expected by this application.
# Increase this number when migrations add/change tables, columns or indexes.
DB_SCHEMA_VERSION = 8


def version_line() -> str:
    return f"{APP_NAME} v{APP_VERSION}"
