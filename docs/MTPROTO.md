# MTProto personal proxy

Feature branch: `feature/mtproto`.

The MTProto product is isolated from the VPN/Remnawave fulfillment path. It is disabled by default and becomes available only when `MTPROTO_ENABLED=true`.

## Product rules

- one credential per Telegram ID;
- credential is created on demand by default (`MTPROTO_AUTO_CREATE=false`);
- an active subscription whose plan has `mtproto_enabled=1` is eligible when it is backed by either a successful payment or an active admin `friend` grant;
- trial access by itself does not qualify;
- admins listed in `ADMIN_IDS` have an explicit testing override even without a paid/friend entitlement;
- when the qualifying entitlement expires or a friend grant is revoked, reconcile disables the credential;
- after renewal or a new qualifying friend grant, reconcile enables the same credential again;
- rotation increments a local generation and replaces the secret;
- user base secrets are not stored in SQLite. A 16-byte base secret is deterministically derived with HMAC-SHA256 from `MTPROTO_SECRET_KEY`, Telegram ID and generation.

## Real deployment with vanitoo/telegram-mtproxy

The supported real deployment uses the companion repository `vanitoo/telegram-mtproxy`.

Both Compose projects mount the same named Docker volume:

```text
warp-mtproto-control-data
```

The flow is:

```text
vpn-tg-bot
    |
    | PUT /v1/users/<telegram_id>
    v
mtproto-control
    |
    | atomically writes active_secrets.txt
    v
warp-mtproto-control-data
    |
    v
telegram-mtproxy supervisor
    |
    | validates manifest
    | restarts only child mtproto-proxy
    | starts one -S argument per active base secret
    | writes active_secrets.applied.sha256
    v
mtproto-control waits for matching SHA-256 acknowledgement
    |
    v
bot reports success
```

No Docker socket is exposed to the Telegram bot or controller.

The legacy shared `telegram-mtproxy/data/secret` stays active by default. This preserves the old shared proxy link while personal credentials are introduced.

## Fake TLS format

The existing `telegram-mtproxy` deployment uses Fake TLS. For that deployment configure:

```env
MTPROTO_SECRET_MODE=faketls
MTPROTO_FAKE_TLS_DOMAIN=www.cloudflare.com
```

The value of `MTPROTO_FAKE_TLS_DOMAIN` must be identical to `DOMAIN` in the
`telegram-mtproxy/.env` file.

For a 32-hex base secret the user-visible Telegram secret is:

```text
ee + <32 hex base secret> + hex(<Fake TLS domain>)
```

The controller still receives only the 32-hex base secret. The proxy starts that value with `-S`.

Other supported client formats are:

```text
MTPROTO_SECRET_MODE=random_padding  -> dd<32hex>
MTPROTO_SECRET_MODE=plain           -> <32hex>
```

For `vanitoo/telegram-mtproxy`, use `faketls`.

## Environment

Recommended real settings in `vpn_manager/.env`:

```env
MTPROTO_ENABLED=true
MTPROTO_DRY_RUN=false

MTPROTO_CONTROL_URL=http://mtproto-control:8080
MTPROTO_CONTROL_TOKEN=<strong random token>

MTPROTO_PUBLIC_HOST=proxy.example.com
MTPROTO_PUBLIC_PORT=443

MTPROTO_SECRET_KEY=<openssl rand -hex 32>
MTPROTO_SECRET_MODE=faketls
MTPROTO_FAKE_TLS_DOMAIN=www.cloudflare.com

MTPROTO_AUTO_CREATE=false
MTPROTO_RECONCILE_INTERVAL_SECONDS=60

MTPROTO_APPLY_MODE=watch
MTPROTO_APPLY_TIMEOUT_SECONDS=15
```

Generate stable secrets once:

```bash
openssl rand -hex 32   # MTPROTO_SECRET_KEY
openssl rand -hex 32   # MTPROTO_CONTROL_TOKEN
```

Do not rotate `MTPROTO_SECRET_KEY` casually. Changing it changes every derived personal credential.

The controller port is not published on the host. It is reachable only inside the
`vpn_manager` Compose network through `http://mtproto-control:8080`.

## telegram-mtproxy settings

Keep the existing proxy settings and add/confirm:

```env
DOMAIN=www.cloudflare.com
LEGACY_SECRET_ENABLED=true
SECRET_RELOAD_INTERVAL=1
```

The Compose project mounts:

```text
warp-mtproto-control-data -> /control
```

The supervisor reads:

```text
/control/active_secrets.txt
```

and writes an acknowledgement after successful child-process startup:

```text
/control/active_secrets.applied.sha256
```

## Startup order

Update and start the real proxy first:

```bash
cd /opt/telegram-mtproxy
git pull --ff-only origin main
docker compose up -d --build --force-recreate mtproxy
docker compose logs --tail=100 mtproxy
```

Then update and start the bot/controller:

```bash
cd /opt/vpn_manager
git switch feature/mtproto
git pull origin feature/mtproto
docker compose up -d --build --force-recreate
docker compose logs --tail=100 mtproto-control
docker compose logs --tail=100 vpn-tg-bot
```

Both projects must show the same named volume:

```bash
docker volume inspect warp-mtproto-control-data
```

## Admin flow

Use:

```text
/mtproto_admin
```

The screen shows controller health, secret mode, current credential counters and every tariff.

For a service/friend tariff, `MTProto ON` means that a user who receives that tariff through the admin `friend` grant can create their own personal MTProto code without making a payment.

A manual reconcile is also available.

## User flow

Eligible users can use:

```text
/mtproto
```

Eligible users include:

- users with an active paid subscription on a plan with MTProto enabled;
- users with an active admin-issued friend grant on a plan with MTProto enabled;
- admins via the explicit testing override.

The user presses:

```text
🚀 Создать личный Telegram Proxy
```

The bot returns:

```text
https://t.me/proxy?server=<host>&port=<port>&secret=<faketls secret>
```

`🔄 Сменить код` replaces only that user's base secret.

## Controller API

Health:

```http
GET /health
Authorization: Bearer <MTPROTO_CONTROL_TOKEN>
```

A real healthy watch-mode response contains:

```json
{
  "ok": true,
  "mode": "watch",
  "configured": true,
  "applied": true,
  "active": 1
}
```

Upsert / enable / disable:

```http
PUT /v1/users/<telegram_id>
Authorization: Bearer <MTPROTO_CONTROL_TOKEN>
Content-Type: application/json
```

Body:

```json
{
  "telegram_id": 123456789,
  "secret": "0123456789abcdef0123456789abcdef",
  "enabled": true
}
```

The operation is idempotent by Telegram ID.

The controller never returns full secrets from diagnostic endpoints.

## Failure semantics

A controller mutation is considered successful only after the MTProxy supervisor writes an acknowledgement containing the SHA-256 of the exact active-secret manifest.

If acknowledgement does not arrive within `MTPROTO_APPLY_TIMEOUT_SECONDS`:

1. the controller returns HTTP 503;
2. controller state and `active_secrets.txt` are rolled back;
3. the bot records an error instead of reporting a successful credential;
4. a later reconcile retries the operation.

When managed secrets change, only the child `mtproto-proxy` process is restarted. Existing MTProxy connections can briefly reconnect, but the Docker container itself is not recreated.

## Reconcile semantics

The reconcile loop runs every `MTPROTO_RECONCILE_INTERVAL_SECONDS` (minimum 30 seconds):

1. if `MTPROTO_AUTO_CREATE=true`, create rows for all currently eligible paid, friend-grant and admin users;
2. eligible row + not active -> enable at controller;
3. no entitlement + active/error/provisioning row -> disable at controller;
4. controller failures are recorded in `mtproto_access.last_error` and retried on later runs.

The database row is retained when access expires or a friend grant is revoked. This lets a renewed/re-granted customer reuse the same Telegram proxy link automatically.
