from __future__ import annotations

import os
import platform
import shutil
import sqlite3
from pathlib import Path


def _fmt_bytes(value: int | float) -> str:
    n = float(value)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if n < 1024 or unit == 'TB':
            return f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} TB'


def get_system_info(db_path: str, log_file: str) -> str:
    lines = []
    lines.append(f'OS: {platform.system()} {platform.release()}')
    lines.append(f'Python: {platform.python_version()}')
    lines.append(f'Host: {platform.node() or "unknown"}')
    lines.append(f'PID: {os.getpid()}')
    try:
        import psutil  # type: ignore
        vm = psutil.virtual_memory()
        lines.append(f'RAM: {_fmt_bytes(vm.used)} / {_fmt_bytes(vm.total)} ({vm.percent}%)')
        cpu = psutil.cpu_percent(interval=0.1)
        lines.append(f'CPU: {cpu}%')
        proc = psutil.Process(os.getpid())
        lines.append(f'Process RAM: {_fmt_bytes(proc.memory_info().rss)}')
    except Exception:
        lines.append('RAM/CPU: psutil not installed')
    try:
        disk = shutil.disk_usage(Path(db_path).resolve().anchor or '.')
        lines.append(f'Disk: {_fmt_bytes(disk.used)} / {_fmt_bytes(disk.total)}')
    except Exception:
        pass
    db = Path(db_path)
    lines.append(f'DB: {db_path}')
    lines.append(f'DB size: {_fmt_bytes(db.stat().st_size) if db.exists() else "not found"}')
    try:
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        for table in ['users', 'plans', 'subscriptions', 'payments', 'trials']:
            try:
                cur.execute(f'SELECT COUNT(*) FROM {table}')
                lines.append(f'{table}: {cur.fetchone()[0]}')
            except Exception:
                lines.append(f'{table}: n/a')
        con.close()
    except Exception as exc:
        lines.append(f'DB read error: {type(exc).__name__}: {exc}')
    log = Path(log_file)
    lines.append(f'Log: {log_file}')
    lines.append(f'Log size: {_fmt_bytes(log.stat().st_size) if log.exists() else "not found"}')
    return '\n'.join(lines)
