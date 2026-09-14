# MTProto personal proxy

Feature branch: `feature/mtproto`.

The MTProto product is isolated from the VPN/Remnawave fulfillment path. It is disabled by default and becomes available only when `MTPROTO_ENABLED=true`.

## Product rules

- one credential per Telegram ID;
- credential is created on demand by default (`MTPROTO_AUTO_CREATE=false`);
- only an active **paid** subscription whose plan has `mtproto_enabled=1` is eligible;
- trials and admin/friend grants do not qualify without a matching successful payment;
- when the paid entitlement expires, reconcile disables the credential;
- after renewal, reconcile enables the same credential again;
- rotation increments a local generation and replaces the secret;
- user secrets are not stored in SQLite. A 16-byte base secret is deterministically derived with HMAC-SHA256 from `MTPROTO_SECRET_KEY`, Telegram ID and generation;
- `dd` random-padding prefix is added only to the client-visible secret when `MTPROTO_RANDOM_PADDING=true`. The controller receives the 32-hex-character base secret expected by MTProxy.

Telegram's official MTProxy supports multiple `-S` secrets, so a controller can map one Telegram user to one server secret. The bot does not assume how the existing proxy container applies those secrets; that part is deliberately behind the controller API.

## Environment

```env
MTPROTO_ENABLED=true
MTPROTO_CONTROL_URL=http://mtproto-control:8080
MTPROTO_CONTROL_TOKEN=change-me
MTPROTO_PUBLIC_HOST=proxy.example.com
MTPROTO_PUBLIC_PORT=443
MTPROTO_SECRET_KEY=<openssl rand -hex 32>
MTPROTO_RANDOM_PADDING=true
MTPROTO_AUTO_CREATE=false
MTPROTO_DRY_RUN=false
MTPROTO_RECONCILE_INTERVAL_SECONDS=60
```

Generate the master key once and keep it stable:

```bash
openssl rand -hex 32
```

Changing `MTPROTO_SECRET_KEY` changes every derived user secret, so do not rotate it casually.

## Admin flow

Use:

```text
/mtproto_admin
```

The screen shows controller health, current credential counters and every tariff. Toggle MTProto per tariff there.

A manual reconcile is also available. It is useful after changing tariff eligibility.

## User flow

When MTProto is globally enabled, active VPN users see `🛡 Telegram Proxy` and can also use:

```text
/mtproto
```

Eligible users get `🚀 Создать личный Telegram Proxy`. The bot returns a Telegram proxy deep link:

```text
https://t.me/proxy?server=<host>&port=<port>&secret=<secret>
```

`🔄 Сменить код` rotates only that user's secret.

## Controller API contract

The bot calls a small internal HTTP controller placed next to the existing MTProxy service. Keep it private; do not expose it to the Internet.

### Health

```http
GET /health
Authorization: Bearer <MTPROTO_CONTROL_TOKEN>
```

Expected successful response:

```json
{"ok": true}
```

### Upsert / enable / disable credential

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

The operation must be **idempotent by Telegram ID**.

- `enabled=true`: ensure this Telegram ID has exactly the supplied secret active;
- if the same Telegram ID previously had another secret, replace it atomically;
- `enabled=false`: remove/disable this Telegram ID's secret without affecting other users;
- return HTTP `200`, `201` or `204` on success.

The controller must never log the full secret.

## Dry-run first

Before wiring the real proxy container:

```env
MTPROTO_ENABLED=true
MTPROTO_DRY_RUN=true
MTPROTO_PUBLIC_HOST=proxy.example.com
MTPROTO_PUBLIC_PORT=443
MTPROTO_SECRET_KEY=<stable random value>
```

Restart the bot, open `/mtproto_admin`, enable MTProto on one paid tariff and test the user flow. Dry-run changes only the bot database; it does not touch the real MTProxy.

After the current MTProto container image/mounts/start command are known, implement or adapt the controller to that exact service and switch `MTPROTO_DRY_RUN=false`.

## Reconcile semantics

The reconcile loop runs every `MTPROTO_RECONCILE_INTERVAL_SECONDS` (minimum 30 seconds):

1. if `MTPROTO_AUTO_CREATE=true`, create rows for all currently eligible paid users;
2. eligible row + not active -> enable at controller;
3. no entitlement + active/error/provisioning row -> disable at controller;
4. controller failures are recorded in `mtproto_access.last_error` and retried on later runs.

The database row is retained when access expires. This lets a renewed customer reuse the same Telegram proxy link automatically.
