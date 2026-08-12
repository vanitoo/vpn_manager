#!/usr/bin/env python3
"""Create a Remnawave node through the API and write its docker-compose.yml."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import ssl
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = ROOT / ".env"
DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parent / "generated"


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


class RemnawaveApi:
    def __init__(self, base_url: str, token: str, *, insecure: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.ssl_context = ssl._create_unverified_context() if insecure else ssl.create_default_context()

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        cookie_name = os.getenv("REMNAWAVE_NGINX_COOKIE_NAME", "").strip()
        cookie_value = os.getenv("REMNAWAVE_NGINX_COOKIE_VALUE", "").strip()
        if cookie_name and cookie_value:
            headers["Cookie"] = f"{cookie_name}={cookie_value}"
        request = Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=30, context=self.ssl_context) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")[:1500]
            raise RuntimeError(f"Remnawave API {method} {path}: HTTP {exc.code}: {error_body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Cannot connect to Remnawave at {self.base_url}: {exc.reason}") from exc
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Remnawave returned non-JSON data for {method} {path}") from exc
        return decoded.get("response", decoded) if isinstance(decoded, dict) else decoded


def required_text(current: str | None, prompt: str, *, min_length: int = 1) -> str:
    value = (current or "").strip()
    while len(value) < min_length:
        value = input(prompt).strip()
        if len(value) < min_length:
            print(f"Enter at least {min_length} characters.")
    return value


def choose_profile(profiles: list[dict[str, Any]], requested_uuid: str | None) -> dict[str, Any]:
    if requested_uuid:
        for profile in profiles:
            if profile.get("uuid") == requested_uuid:
                return profile
        raise RuntimeError(f"Config profile not found: {requested_uuid}")
    if len(profiles) == 1:
        print(f"Config profile: {profiles[0].get('name', profiles[0].get('uuid'))}")
        return profiles[0]
    print("Available config profiles:")
    for index, profile in enumerate(profiles, start=1):
        print(f"  {index}. {profile.get('name', '-') } ({profile.get('uuid', '-')})")
    while True:
        selected = input(f"Select profile [1-{len(profiles)}]: ").strip()
        if selected.isdigit() and 1 <= int(selected) <= len(profiles):
            return profiles[int(selected) - 1]
        print("Invalid profile number.")


def compose_text(node_port: int, secret_key: str) -> str:
    secret_scalar = json.dumps(secret_key, ensure_ascii=True)
    return f"""services:
  remnanode:
    image: remnawave/node:latest
    container_name: remnanode
    hostname: remnanode
    network_mode: host
    restart: always
    cap_add:
      - NET_ADMIN
    ulimits:
      nofile:
        soft: 1048576
        hard: 1048576
    environment:
      - NODE_PORT={node_port}
      - SECRET_KEY={secret_scalar}
"""


def safe_directory_name(name: str, node_uuid: str) -> str:
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", name).strip("-.") or "node"
    return f"{safe_name[:40]}-{node_uuid[:8]}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a Remnawave node and generate docker-compose.yml")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--base-url", help="Defaults to REMNAWAVE_BASE_URL")
    parser.add_argument("--name", help="Node name")
    parser.add_argument("--address", help="Public node IP address or hostname")
    parser.add_argument("--node-port", type=int, default=2222, help="Remnawave Node API port (default: 2222)")
    parser.add_argument("--country-code", default="XX", help="Two-letter country code (default: XX)")
    parser.add_argument("--profile-uuid", help="Use this config profile without prompting")
    parser.add_argument("--inbound-uuid", action="append", default=[], help="Inbound UUID; repeat as needed")
    parser.add_argument("--output-dir", type=Path, help="Exact directory for generated files")
    parser.add_argument("--yes", action="store_true", help="Do not ask for final confirmation")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification (testing only)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env_file(args.env_file)

    base_url = (args.base_url or os.getenv("REMNAWAVE_BASE_URL", "")).strip()
    token = os.getenv("REMNAWAVE_API_TOKEN", "").strip()
    if not base_url:
        base_url = required_text(None, "Remnawave URL (https://panel.example.com): ", min_length=4)
    if not token:
        token = getpass.getpass("Remnawave API token: ").strip()
    if not token:
        raise RuntimeError("Remnawave API token is required.")
    if args.node_port < 1 or args.node_port > 65535:
        raise RuntimeError("--node-port must be between 1 and 65535.")
    country_code = args.country_code.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", country_code):
        raise RuntimeError("--country-code must contain exactly two letters.")

    name = required_text(args.name, "Node name: ", min_length=3)
    address = required_text(args.address, "Node public IP or hostname: ", min_length=2)
    api = RemnawaveApi(base_url, token, insecure=args.insecure)

    profiles_payload = api.request("GET", "/api/config-profiles")
    profiles = profiles_payload.get("configProfiles", []) if isinstance(profiles_payload, dict) else []
    if not profiles:
        raise RuntimeError("No config profiles found in Remnawave. Create one before adding a node.")
    profile = choose_profile(profiles, args.profile_uuid)
    available_inbounds = profile.get("inbounds") or []
    if args.inbound_uuid:
        available_by_uuid = {item.get("uuid"): item for item in available_inbounds}
        missing = [value for value in args.inbound_uuid if value not in available_by_uuid]
        if missing:
            raise RuntimeError(f"Inbound UUID does not belong to the selected profile: {', '.join(missing)}")
        inbound_uuids = args.inbound_uuid
    else:
        inbound_uuids = [str(item["uuid"]) for item in available_inbounds if item.get("uuid")]
    if not inbound_uuids:
        raise RuntimeError("The selected config profile has no inbounds.")

    print("\nNode to create:")
    print(f"  Name:       {name}")
    print(f"  Address:    {address}")
    print(f"  Node port:  {args.node_port}")
    print(f"  Country:    {country_code}")
    print(f"  Profile:    {profile.get('name', profile.get('uuid'))}")
    print(f"  Inbounds:   {len(inbound_uuids)}")
    if not args.yes and input("Create this node? [y/N]: ").strip().lower() not in {"y", "yes"}:
        print("Cancelled.")
        return 1

    payload = {
        "name": name,
        "address": address,
        "port": args.node_port,
        "countryCode": country_code,
        "consumptionMultiplier": 1,
        "isTrafficTrackingActive": False,
        "configProfile": {
            "activeConfigProfileUuid": profile["uuid"],
            "activeInbounds": inbound_uuids,
        },
    }
    node = api.request("POST", "/api/nodes", payload)
    if not isinstance(node, dict) or not node.get("uuid"):
        raise RuntimeError(f"Node was created but API returned an unexpected response: {node!r}")

    key_payload = api.request("GET", "/api/keygen")
    secret_key = key_payload.get("pubKey", "") if isinstance(key_payload, dict) else ""
    if not secret_key:
        raise RuntimeError(
            f"Node {node['uuid']} was created, but /api/keygen returned no pubKey. "
            "Delete the incomplete node manually if necessary."
        )

    output_dir = args.output_dir or (DEFAULT_OUTPUT_ROOT / safe_directory_name(name, str(node["uuid"])))
    output_dir.mkdir(parents=True, exist_ok=False)
    compose_path = output_dir / "docker-compose.yml"
    metadata_path = output_dir / "node.json"
    compose_path.write_text(compose_text(args.node_port, secret_key), encoding="utf-8", newline="\n")
    metadata_path.write_text(json.dumps(node, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    try:
        os.chmod(compose_path, 0o600)
        os.chmod(metadata_path, 0o600)
    except OSError:
        pass

    print("\nNode created successfully.")
    print(f"  UUID:    {node['uuid']}")
    print(f"  Compose: {compose_path.resolve()}")
    print(f"  Metadata:{metadata_path.resolve()}")
    print("\nUse deploy-node.ps1 with the generated docker-compose.yml to install it.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)

