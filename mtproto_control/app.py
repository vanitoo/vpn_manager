from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from aiohttp import web

DATA_DIR = Path(os.getenv('MTPROTO_CONTROL_DATA_DIR', '/data'))
STATE_FILE = DATA_DIR / 'users.json'
SECRETS_FILE = DATA_DIR / 'active_secrets.txt'
ACK_FILE = Path(os.getenv('MTPROTO_APPLY_ACK_FILE', str(DATA_DIR / 'active_secrets.applied.sha256')))
TOKEN = os.getenv('MTPROTO_CONTROL_TOKEN', '').strip()
APPLY_COMMAND = os.getenv('MTPROTO_APPLY_COMMAND', '').strip()
APPLY_MODE = os.getenv('MTPROTO_APPLY_MODE', 'command').strip().lower() or 'command'
APPLY_TIMEOUT = max(1.0, float(os.getenv('MTPROTO_APPLY_TIMEOUT_SECONDS', '15')))
DRY_RUN = os.getenv('MTPROTO_CONTROL_DRY_RUN', '').strip().lower() in {'1', 'true', 'yes', 'on'}
SECRET_RE = re.compile(r'^[0-9a-fA-F]{32}$')
LOCK = asyncio.Lock()


def _authorized(request: web.Request) -> bool:
    if not TOKEN:
        return True
    return request.headers.get('Authorization', '') == f'Bearer {TOKEN}'


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if not _authorized(request):
        raise web.HTTPUnauthorized()
    return await handler(request)


def _load_state() -> dict[str, dict[str, Any]]:
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): value for key, value in data.items() if isinstance(value, dict)}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + '.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


def _active_secrets(state: dict[str, dict[str, Any]]) -> list[str]:
    return [
        str(value.get('secret') or '').lower()
        for _, value in sorted(state.items(), key=lambda item: int(item[0]))
        if value.get('enabled') and SECRET_RE.fullmatch(str(value.get('secret') or ''))
    ]


def _persist_state(state: dict[str, dict[str, Any]]) -> None:
    _atomic_write(STATE_FILE, json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    active = _active_secrets(state)
    _atomic_write(SECRETS_FILE, ''.join(f'{secret}\n' for secret in active))


def _manifest_digest() -> str:
    try:
        data = SECRETS_FILE.read_bytes()
    except FileNotFoundError:
        data = b''
    return hashlib.sha256(data).hexdigest()


def _ack_digest() -> str:
    try:
        return ACK_FILE.read_text(encoding='utf-8').strip().lower()
    except (FileNotFoundError, OSError):
        return ''


async def _wait_for_manifest_ack() -> tuple[bool, str]:
    expected = _manifest_digest()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + APPLY_TIMEOUT
    while loop.time() < deadline:
        actual = _ack_digest()
        if actual == expected:
            return True, 'manifest-applied'
        await asyncio.sleep(0.2)
    actual = _ack_digest() or 'missing'
    return False, f'MTProxy did not acknowledge manifest {expected[:12]} (ack={actual[:12]})'


async def _apply_backend() -> tuple[bool, str]:
    if DRY_RUN:
        return True, 'dry-run'

    if APPLY_MODE == 'watch':
        return await _wait_for_manifest_ack()

    if APPLY_MODE != 'command':
        return False, f'unsupported MTPROTO_APPLY_MODE={APPLY_MODE!r}'

    if not APPLY_COMMAND:
        return False, 'MTPROTO_APPLY_COMMAND is empty'

    env = os.environ.copy()
    env['MTPROTO_SECRETS_FILE'] = str(SECRETS_FILE)
    proc = await asyncio.create_subprocess_shell(
        APPLY_COMMAND,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        message = (stderr or stdout).decode('utf-8', errors='replace')[-1000:]
        return False, f'apply command exited {proc.returncode}: {message}'
    return True, 'applied'


async def health(request: web.Request) -> web.Response:
    state = _load_state()
    active = sum(1 for value in state.values() if value.get('enabled'))
    configured = bool(DRY_RUN or APPLY_MODE == 'watch' or (APPLY_MODE == 'command' and APPLY_COMMAND))
    applied = True
    if not DRY_RUN and APPLY_MODE == 'watch':
        applied = _manifest_digest() == _ack_digest()

    return web.json_response({
        'ok': bool(configured and applied),
        'mode': 'dry-run' if DRY_RUN else APPLY_MODE,
        'configured': configured,
        'applied': applied,
        'active': active,
    })


async def put_user(request: web.Request) -> web.Response:
    telegram_id = request.match_info['telegram_id']
    try:
        numeric_id = int(telegram_id)
    except ValueError:
        raise web.HTTPBadRequest(text='invalid telegram_id')
    if numeric_id <= 0:
        raise web.HTTPBadRequest(text='invalid telegram_id')

    try:
        payload = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text='invalid json')
    if int(payload.get('telegram_id') or 0) != numeric_id:
        raise web.HTTPBadRequest(text='telegram_id mismatch')

    secret = str(payload.get('secret') or '').strip().lower()
    if not SECRET_RE.fullmatch(secret):
        raise web.HTTPBadRequest(text='secret must be exactly 32 hexadecimal characters')
    enabled = bool(payload.get('enabled'))

    async with LOCK:
        old_state = _load_state()
        new_state = json.loads(json.dumps(old_state))
        new_state[str(numeric_id)] = {'secret': secret, 'enabled': enabled}
        _persist_state(new_state)

        ok, detail = await _apply_backend()
        if not ok:
            _persist_state(old_state)
            # Best effort: ask the backend to restore the previous manifest too.
            await _apply_backend()
            raise web.HTTPServiceUnavailable(text=detail)

    return web.json_response({
        'ok': True,
        'telegram_id': numeric_id,
        'enabled': enabled,
        'active': len(_active_secrets(new_state)),
    })


async def list_users(request: web.Request) -> web.Response:
    state = _load_state()
    # Never expose secrets from diagnostics.
    rows = [
        {'telegram_id': int(key), 'enabled': bool(value.get('enabled'))}
        for key, value in sorted(state.items(), key=lambda item: int(item[0]))
    ]
    return web.json_response({'users': rows})


def create_app() -> web.Application:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Rebuild the public manifest from controller state on every start. This makes
    # recovery deterministic if the manifest was deleted while users.json survived.
    _persist_state(_load_state())

    app = web.Application(middlewares=[auth_middleware])
    app.router.add_get('/health', health)
    app.router.add_get('/v1/users', list_users)
    app.router.add_put('/v1/users/{telegram_id}', put_user)
    return app


if __name__ == '__main__':
    web.run_app(
        create_app(),
        host=os.getenv('MTPROTO_CONTROL_HOST', '0.0.0.0'),
        port=int(os.getenv('MTPROTO_CONTROL_PORT', '8080')),
    )
