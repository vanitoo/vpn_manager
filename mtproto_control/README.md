# mtproto-control

Small internal control plane for per-user MTProxy secrets.

It is intentionally separate from the Telegram bot. The bot never needs Docker socket access.

## What it does

- `PUT /v1/users/<telegram_id>` stores/replaces one base MTProxy secret per Telegram ID;
- `enabled=false` removes that secret from the active manifest;
- writes `/data/active_secrets.txt` atomically, one 32-hex secret per line;
- runs one static `MTPROTO_APPLY_COMMAND` after every change;
- rolls the manifest/state back if the apply command fails;
- `/health` reports controller configuration and active credential count;
- diagnostic API never returns secrets.

The apply command is deployment-specific because different MTProxy Docker images manage multi-secret configuration differently. After inspecting the existing MTProto container, set the command to the exact safe reload/recreate hook for that service.

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

The controller file will contain:

```text
/data/active_secrets.txt
```

with one base secret per active user.

## Real apply hook

Set:

```env
MTPROTO_CONTROL_DRY_RUN=false
MTPROTO_APPLY_COMMAND=/opt/hooks/apply-mtproxy-secrets.sh
```

The command receives this environment variable:

```text
MTPROTO_SECRETS_FILE=/data/active_secrets.txt
```

It must return exit code `0` only after the MTProxy process is actually serving exactly the secrets from that manifest.

Do not build a shell command from request data. `MTPROTO_APPLY_COMMAND` is static configuration.

## Security

Bind the controller only to localhost or a private Docker network. Always configure `MTPROTO_CONTROL_TOKEN` outside dry local development. Do not publish port 8080 publicly.
