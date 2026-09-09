# Warp Private Network — Developer Context

## Текущий статус

- Приложение: `Warp Private Network`.
- Версия в `app/version.py`: `0.8.5`.
- Основная ветка: `main`.
- Python 3.11+.
- Telegram framework: aiogram 3.x.
- База: SQLite через `aiosqlite`.
- Запуск: polling или Docker Compose.
- VPN backend: Remnawave API с автоопределением 2.x/3.x.

## Назначение

Бот продаёт и продлевает VPN-доступ, выдаёт персональную ссылку Remnawave, показывает пользователю состояние аккаунта, обрабатывает платежи, ведёт HelpDesk через Telegram Topics и предоставляет административные инструменты.

## Пользовательские возможности

- покупка тарифа;
- тестовый доступ;
- экран `Мой VPN`;
- имя пользователя;
- текущий информационный баланс;
- дата окончания подписки;
- персональная ссылка `Ваш ключ 🔽 ✅`;
- открытие страницы подключения и Happ;
- просмотр последних платежей;
- продление;
- инструкции по устройствам;
- FAQ и диагностика;
- обращение к оператору.

Баланс пока только отображается. Автоматическое пополнение и списание не реализованы.

## Remnawave

`app/remnawave.py` автоматически определяет major API.

### 2.x

- пользователь обычно идентифицируется UUID;
- используется классическая pagination-схема;
- node setup может возвращать `pubKey`.

### 3.x

- пользователь имеет числовой `id`;
- список пользователей работает через `/api/users/stream`;
- локальные UUID старого формата могут relink-иться на новый ID;
- node setup использует `secretKey`.

Не пишите новую логику, предполагающую только один формат Remnawave ID.

## Основная структура

```text
app/
├── main.py                    entrypoint
├── boot.py                    startup/polling/background jobs
├── config.py                  Settings/.env
├── version.py                 app/schema version
├── runtime.py                 runtime dependencies + provisioning
├── db.py                      core SQLite data
├── user_account.py            account/balance/payment history
├── user_vpn_handlers.py       My VPN
├── external_payment_handlers.py
├── payments/                  external payment providers
├── remnawave.py               version-aware Remnawave client
├── admin_*                    Telegram admin sections
├── faq.py / faq_handlers.py   FAQ
├── mailing.py                 reminders
├── support/                   HelpDesk + Telegram Topics
└── products/                  optional product modules
```

## База данных

Основные таблицы:

| Таблица | Назначение |
|---|---|
| `users` | Telegram user и локальный баланс |
| `plans` | Тарифы |
| `subscriptions` | Локальные VPN-подписки |
| `payments` | Платежи и их статусы |
| `receipt_contacts` | Контакты для чеков |
| `faq_items` | FAQ |
| `support_tickets` | Тикеты HelpDesk |
| `support_messages` | История HelpDesk |
| `mailing_rules` | Напоминания |
| `app_settings` | Настройки приложения |
| `app_meta` | Метаданные и версия схемы |

`balance_rub` добавляется безопасно для существующих баз и по умолчанию равен `0`.

## Платежи

Поддерживаются:

- Telegram Stars;
- YooKassa;
- Cryptomus;
- Lava;
- Platega.

Список включённых провайдеров задаётся `PAYMENT_PROVIDERS`. Внешние платежи проверяются через API провайдера; успешная оплата приводит к выдаче или продлению VPN.

## HelpDesk

`app/support/` использует Telegram supergroup с Topics. Один тикет соответствует одной теме. Операторские команды:

```text
/take
/wait
/close
```

SQLite — источник истины, Telegram topic — интерфейс оператора.

## Конфигурация

Минимум:

```env
BOT_TOKEN=
ADMIN_IDS=
DB_PATH=data/vpn_bot.sqlite3
```

Remnawave:

```env
REMNAWAVE_BASE_URL=
REMNAWAVE_API_TOKEN=
REMNAWAVE_INTERNAL_SQUAD_UUID=
REMNAWAVE_EXTERNAL_SQUAD_UUID=
REMNAWAVE_SUBSCRIPTION_BASE_URL=
```

HelpDesk:

```env
SUPPORT_ENABLED=true
SUPPORT_GROUP_ID=0
```

Полный список — в `.env.example`.

## Команды разработки

```bash
python -m app.main
```

```bash
docker compose up -d --build
```

Production deploy после обновления `main`:

```bash
cd /opt/vpn_manager
git pull origin main
docker compose up -d --build --force-recreate bot
```

## Правила при изменениях

- не коммитьте секреты;
- обновляйте `docs/CHANGELOG.md`;
- сохраняйте совместимость существующей SQLite-базы;
- проверяйте обе major-ветки Remnawave API при изменении интеграции;
- не дублируйте SQL между handlers;
- новые пользовательские возможности документируйте в README и профильном файле `docs/`.
