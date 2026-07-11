from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'y', 'on'}


def _csv_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [x.strip().lower() for x in value.replace(';', ',').split(',') if x.strip()]


def _int_list(value: str | None) -> list[int]:
    if not value:
        return []
    return [int(x.strip()) for x in value.split(',') if x.strip()]


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_ids: list[int]
    db_path: str
    proxy_mode: str
    proxy: str | None
    proxy_healthcheck_url: str
    proxy_healthcheck_timeout: float
    proxy_healthcheck_interval: float
    delete_webhook_on_start: bool
    drop_pending_updates: bool
    seed_plans_on_start: bool
    stars_rub_per_star: float
    payment_providers: list[str]
    yookassa_enabled: bool
    yookassa_shop_id: str
    yookassa_secret_key: str
    yookassa_return_url: str
    yookassa_test_mode: bool
    cryptomus_enabled: bool
    cryptomus_merchant_id: str
    cryptomus_api_key: str
    cryptomus_currency: str
    cryptomus_return_url: str
    lava_enabled: bool
    lava_shop_id: str
    lava_api_key: str
    lava_secret_key: str
    platega_enabled: bool
    platega_merchant_id: str
    platega_api_key: str
    receipt_require_contact: bool
    receipt_fallback_email: str
    receipt_save_contact: bool
    remnawave_base_url: str
    remnawave_api_token: str
    remnawave_internal_squad_uuid: str
    remnawave_external_squad_uuid: str
    remnawave_subscription_base_url: str
    remnawave_default_traffic_gb: int
    remnawave_hwid_device_limit: int
    remnawave_nginx_auth_enabled: bool
    remnawave_nginx_cookie_name: str
    remnawave_nginx_cookie_value: str
    remnawave_nginx_basic_login: str
    remnawave_nginx_basic_password: str
    pending_payment_ttl_minutes: int
    admin_notify_purchases: bool
    startup_alerts_enabled: bool
    shutdown_alerts_enabled: bool
    backups_enabled: bool
    backup_dir: str
    log_level: str
    log_file: str
    log_max_bytes: int
    log_backup_count: int
    auto_setup_bot_menu: bool


def get_settings() -> Settings:
    token = os.getenv('BOT_TOKEN', '').strip()
    if not token or token == '123456:CHANGE_ME':
        raise RuntimeError('Set BOT_TOKEN in .env or environment variables')
    db_path = os.getenv('DB_PATH', 'data/vpn_bot.sqlite3').strip()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    explicit = _csv_list(os.getenv('PAYMENT_PROVIDERS'))
    flags: list[str] = []
    if _bool(os.getenv('ENABLE_STARS'), True): flags.append('stars')
    if _bool(os.getenv('YOOKASSA_ENABLED'), False): flags.append('yookassa')
    if _bool(os.getenv('CRYPTOMUS_ENABLED'), False): flags.append('cryptomus')
    if _bool(os.getenv('LAVA_ENABLED'), False): flags.append('lava')
    if _bool(os.getenv('PLATEGA_ENABLED'), False): flags.append('platega')
    providers = explicit or flags or ['stars']
    providers = [p for p in providers if p in {'stars','yookassa','cryptomus','lava','platega'}] or ['stars']
    proxy = os.getenv('PROXY', '').strip() or None
    proxy_mode = os.getenv('PROXY_MODE', '').strip().lower() or ('failover' if proxy else 'off')
    backup_dir = os.getenv('BACKUP_DIR', 'backups').strip()
    Path(backup_dir).mkdir(parents=True, exist_ok=True)
    return Settings(
        bot_token=token, admin_ids=_int_list(os.getenv('ADMIN_IDS')), db_path=db_path,
        proxy_mode=proxy_mode, proxy=proxy,
        proxy_healthcheck_url=os.getenv('PROXY_HEALTHCHECK_URL','https://api.telegram.org').strip(),
        proxy_healthcheck_timeout=float(os.getenv('PROXY_HEALTHCHECK_TIMEOUT','8')),
        proxy_healthcheck_interval=float(os.getenv('PROXY_HEALTHCHECK_INTERVAL','60')),
        delete_webhook_on_start=_bool(os.getenv('DELETE_WEBHOOK_ON_START'),True),
        drop_pending_updates=_bool(os.getenv('DROP_PENDING_UPDATES'),False),
        seed_plans_on_start=_bool(os.getenv('SEED_PLANS_ON_START'),True),
        stars_rub_per_star=float(os.getenv('STARS_RUB_PER_STAR','1.70')),
        payment_providers=providers,
        yookassa_enabled='yookassa' in providers,
        yookassa_shop_id=os.getenv('YOOKASSA_SHOP_ID','').strip(),
        yookassa_secret_key=os.getenv('YOOKASSA_SECRET_KEY','').strip(),
        yookassa_return_url=os.getenv('YOOKASSA_RETURN_URL','https://t.me').strip(),
        yookassa_test_mode=_bool(os.getenv('YOOKASSA_TEST_MODE'),True),
        cryptomus_enabled='cryptomus' in providers,
        cryptomus_merchant_id=os.getenv('CRYPTOMUS_MERCHANT_ID','').strip(),
        cryptomus_api_key=os.getenv('CRYPTOMUS_API_KEY','').strip(),
        cryptomus_currency=os.getenv('CRYPTOMUS_CURRENCY','RUB').strip().upper(),
        cryptomus_return_url=os.getenv('CRYPTOMUS_RETURN_URL','https://t.me').strip(),
        lava_enabled='lava' in providers, lava_shop_id=os.getenv('LAVA_SHOP_ID','').strip(),
        lava_api_key=os.getenv('LAVA_API_KEY','').strip(), lava_secret_key=os.getenv('LAVA_SECRET_KEY','').strip(),
        platega_enabled='platega' in providers, platega_merchant_id=os.getenv('PLATEGA_MERCHANT_ID','').strip(),
        platega_api_key=os.getenv('PLATEGA_API_KEY','').strip(),
        receipt_require_contact=_bool(os.getenv('RECEIPT_REQUIRE_CONTACT'),False),
        receipt_fallback_email=os.getenv('RECEIPT_FALLBACK_EMAIL','orders@example.com').strip(),
        receipt_save_contact=_bool(os.getenv('RECEIPT_SAVE_CONTACT'),True),
        remnawave_base_url=os.getenv('REMNAWAVE_BASE_URL','').strip().rstrip('/'),
        remnawave_api_token=os.getenv('REMNAWAVE_API_TOKEN','').strip(),
        remnawave_internal_squad_uuid=os.getenv('REMNAWAVE_INTERNAL_SQUAD_UUID','').strip(),
        remnawave_external_squad_uuid=os.getenv('REMNAWAVE_EXTERNAL_SQUAD_UUID','').strip(),
        remnawave_subscription_base_url=os.getenv('REMNAWAVE_SUBSCRIPTION_BASE_URL','').strip().rstrip('/'),
        remnawave_default_traffic_gb=int(os.getenv('REMNAWAVE_DEFAULT_TRAFFIC_GB','0')),
        remnawave_hwid_device_limit=int(os.getenv('REMNAWAVE_HWID_DEVICE_LIMIT','0')),
        remnawave_nginx_auth_enabled=_bool(os.getenv('REMNAWAVE_NGINX_AUTH_ENABLED'),False),
        remnawave_nginx_cookie_name=os.getenv('REMNAWAVE_NGINX_COOKIE_NAME','').strip(),
        remnawave_nginx_cookie_value=os.getenv('REMNAWAVE_NGINX_COOKIE_VALUE','').strip(),
        remnawave_nginx_basic_login=os.getenv('REMNAWAVE_NGINX_BASIC_LOGIN','').strip(),
        remnawave_nginx_basic_password=os.getenv('REMNAWAVE_NGINX_BASIC_PASSWORD','').strip(),
        pending_payment_ttl_minutes=int(os.getenv('PENDING_PAYMENT_TTL_MINUTES','60')),
        admin_notify_purchases=_bool(os.getenv('ADMIN_NOTIFY_PURCHASES'),True),
        startup_alerts_enabled=_bool(os.getenv('STARTUP_ALERTS_ENABLED'),True),
        shutdown_alerts_enabled=_bool(os.getenv('SHUTDOWN_ALERTS_ENABLED'),True),
        backups_enabled=_bool(os.getenv('BACKUPS_ENABLED'),True), backup_dir=backup_dir,
        log_level=os.getenv('LOG_LEVEL','INFO').strip().upper(), log_file=os.getenv('LOG_FILE','logs/bot.log').strip(),
        log_max_bytes=int(os.getenv('LOG_MAX_BYTES',str(10*1024*1024))),
        log_backup_count=int(os.getenv('LOG_BACKUP_COUNT','5')),
        auto_setup_bot_menu=_bool(os.getenv('AUTO_SETUP_BOT_MENU'),True),
    )
