from __future__ import annotations

import os


def enabled() -> bool:
    return os.getenv('SUPPORT_ENABLED', 'true').strip().lower() in {'1', 'true', 'yes', 'y', 'on'}


def group_id() -> int:
    value = os.getenv('SUPPORT_GROUP_ID', '0').strip()
    return int(value or '0')
