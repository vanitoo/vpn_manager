# mtproto-control

Small internal control plane for per-user MTProxy secrets.

It is intentionally separate from the Telegram bot. The bot never needs Docker socket access.

## What it does

- `PUT /v1/users/<telegram_id>` stores/replaces one base MTProxy secret per Telegram ID;
- `enabled=false` removes that secret from the active manifest;
- writes `/data/active_secrets.txt` atomically, one 32-hex secret per line;
- can either execute a static apply command or wait for an external MTProxy supervisor acknowledgement;
- rolls controller state and the manifest back when apply fails;
- `/health` reports controller configuration, apply state and active credential count;
- diagnostic API never returns secrets.

## Recommended mode with vanitoo/telegram-mtproxy

Use watch mode:

```env
MTPROTO_CONTROL_DRY_RUN=false
MTPROTO_APPLY_MODE=watch
MTPROTO_APPLY_ACK_FILE=/data/active_secrets.applied.sha256
MTPROTO_APPLY_TIMEOUT_SECONDS=15
```

The controller writes:

```text
/data/active_secrets.txt
```

The MTProxy supervisor reads that file from the shared Docker volume, applies one
`-S <secret>` argument per active secret, and then writes:

```text
/data/active_secrets.applied.sha256
```

The acknowledgement must contain the SHA-256 of the exact manifest bytes. The HTTP
mutation returns success only after the acknowledgement matches.

The Compose integration in this repository and `vanitoo/telegram-mtproxy` uses the
same named volume:

```text
warp-mtproto-control-data
```

## Local dry run

```bash
docker build -t warp-mtproto-control ./mtproto_control

docker run --rm -p 127.0.0.1:18080:8080 \
  -e MTPROTO_CONTROL_TOKEN=test-token \
  -e MTPROTO_CONTROL_DRY_RUN=true \
  -v mtproto-control-data:/data \
  warp-mtproto-control
```

Health:

```bash
curl -sS \
  -H 'Authorization: Bearer test-token' \
  http://127.0.0.1:18080/health
```

Add a credential:

```bash
curl -sS -X PUT \
  -H 'Authorization: Bearer test-token' \
  -H 'Content-Type: application/json' \
  http://127.0.0.1:18080/v1/users/123456789 \
  -d '{"telegram_id":123456789,"secret":"0123456789abcdef0123456789abcdef","enabled":true}'
```

## Command mode for other deployments

For an MTProxy deployment that has its own safe reload hook, use:

```env
MTPROTO_APPLY_MODE=command
MTPROTO_APPLY_COMMAND=/opt/hooks/apply-mtproxy-secrets.sh
```

The command receives:

```text
MTPROTO_SECRETS_FILE=/data/active_secrets.txt
```

It must return exit code `0` only after the target proxy is serving the supplied
manifest.

Do not build a shell command from request data. `MTPROTO_APPLY_COMMAND` is static
configuration.

## Security

Keep the controller only on the private Compose network. Always configure
`MTPROTO_CONTROL_TOKEN` outside local dry-run development. Do not publish port
8080 to the Internet.
