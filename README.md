# Warp Private Network

Self-hosted Telegram-платформа для продажи и управления VPN-доступом через Remnawave.

Проект объединяет Telegram-бот, подписки, платежи, Remnawave, пользовательский HelpDesk через Telegram Topics, рассылки и административные инструменты. Отдельный веб-кабинет для пользователя не обязателен.

> Техническое имя репозитория: `vpn_manager`.

## Возможности

### Пользователь

- покупка и продление VPN-доступа;
- тестовый доступ;
- экран `🔑 Мой VPN`;
- имя пользователя и текущий информационный баланс;
- срок действия подписки;
- персональная ссылка `Ваш ключ 🔽 ✅`;
- быстрое подключение через страницу подписки и Happ;
- история последних платежей;
- инструкции для iPhone/iPad, Android, Windows, macOS, Linux, Android TV и Apple TV;
- FAQ и автоматическая диагностика;
- обращение к специалисту через Telegram.

### Администратор

- Telegram-админка;
- управление пользователями и тарифами;
- работа с Remnawave users, nodes и squads;
- синхронизация Remnawave → локальная база;
- служебные тарифы и тестовый доступ;
- рассылки и правила напоминаний;
- резервные копии SQLite;
- системная информация и логи;
- управление FAQ;
- Telegram Topics HelpDesk.

## Технологии

- Python 3.11+;
- aiogram 3.x;
- SQLite + aiosqlite;
- aiohttp;
- Docker / Docker Compose;
- Remnawave API 2.x и 3.x с автоопределением версии.

## Архитектура

```text
Telegram user/admin
        │
        ▼
     aiogram
        │
 ┌──────┼───────────────┬──────────────┐
 │      │               │              │
 ▼      ▼               ▼              ▼
VPN   Payments        Support        Admin
 │      │               │              │
 └──────┴───────┬───────┴──────────────┘
                ▼
              SQLite
                │
                ▼
        Remnawave API 2/3
```

Подробности: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Быстрый запуск

```bash
git clone https://github.com/vanitoo/vpn_manager.git
cd vpn_manager
cp .env.example .env
```

Заполните минимум:

```env
BOT_TOKEN=123456:replace_me
ADMIN_IDS=123456789
```

Локально:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.main
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.main
```

Docker:

```bash
docker compose up -d --build
```

Production update:

```bash
cd /opt/vpn_manager
git pull origin main
docker compose up -d --build --force-recreate bot
```

## Конфигурация

Основные параметры:

```env
BOT_TOKEN=
ADMIN_IDS=
DB_PATH=data/vpn_bot.sqlite3

DELETE_WEBHOOK_ON_START=true
DROP_PENDING_UPDATES=false
AUTO_SETUP_BOT_MENU=true

LOG_LEVEL=INFO
LOG_FILE=logs/bot.log
BACKUPS_ENABLED=true
BACKUP_DIR=backups
```

Полный список находится в `.env.example`.

## Remnawave

```env
REMNAWAVE_BASE_URL=https://panel.example.com
REMNAWAVE_API_TOKEN=replace_me
REMNAWAVE_INTERNAL_SQUAD_UUID=
REMNAWAVE_EXTERNAL_SQUAD_UUID=
REMNAWAVE_SUBSCRIPTION_BASE_URL=https://sub.example.com
REMNAWAVE_DEFAULT_TRAFFIC_GB=0
REMNAWAVE_HWID_DEVICE_LIMIT=0
REMNAWAVE_TRAFFIC_LIMIT_STRATEGY=MONTH
```

Клиент автоматически определяет major-версию API и поддерживает Remnawave 2.x и 3.x. В 2.x пользователь обычно имеет UUID, а в 3.x — числовой `id`; код не должен полагаться на один формат идентификатора.

Бот умеет создавать и находить пользователя, продлевать доступ, блокировать/активировать, работать с squads, получать subscription URL и синхронизировать локальную запись с Remnawave.

## Платежи

Доступные провайдеры:

```env
PAYMENT_PROVIDERS=stars,yookassa,cryptomus,lava,platega
```

Telegram Stars:

```env
ENABLE_STARS=true
STARS_RUB_PER_STAR=1.70
```

YooKassa и Cryptomus работают через API-проверку статуса платежа. Lava и Platega подключены как отдельные provider-модули. Отключённая или неполная конфигурация провайдера не должна ломать запуск приложения.

Доступ выдаётся только после подтверждения успешной оплаты.

## Аккаунт и баланс

Экран `Мой VPN` показывает:

```text
Имя
Баланс
Срок действия
Ваш ключ 🔽 ✅
История платежей
```

Поле `balance_rub` уже хранится в пользовательском аккаунте и по умолчанию равно `0 ₽`. Сейчас баланс информационный: пополнение, списание и отдельный журнал операций ещё не реализованы.

## Помощь и поддержка

Для операторов используется Telegram supergroup с включёнными Topics.

```env
SUPPORT_ENABLED=true
SUPPORT_GROUP_ID=-1001234567890
```

Пользователь проходит инструкцию по подключению, FAQ и диагностику, а при необходимости создаёт тикет. Один тикет соответствует одной Telegram topic.

Команды оператора:

```text
/take
/wait
/close
```

Подробнее: [docs/SUPPORT.md](docs/SUPPORT.md).

## База данных

Основные сущности:

```text
users
plans
subscriptions
payments
receipt_contacts
mailing_rules
faq_items
support_tickets
support_messages
app_settings
app_meta
```

SQLite рассчитана на текущий single-instance режим. Полноценная система последовательных миграций запланирована до стабильного 1.0.

## Безопасность

- не коммитьте `.env`;
- не публикуйте Telegram/Remnawave/payment secrets;
- относитесь к subscription URL как к чувствительному bearer-secret;
- ограничивайте `ADMIN_IDS` и доступ к support-группе;
- регулярно создавайте резервные копии;
- при утечке секрета обязательно ротируйте его.

Политика: [docs/SECURITY.md](docs/SECURITY.md).

## Документация

- [Архитектура](docs/ARCHITECTURE.md)
- [Changelog](docs/CHANGELOG.md)
- [Roadmap](docs/ROADMAP.md)
- [HelpDesk / Support](docs/SUPPORT.md)
- [Contributing](docs/CONTRIBUTING.md)
- [Security](docs/SECURITY.md)
- [Developer Context](docs/KODA.md)

GitHub распознаёт community-файлы `CONTRIBUTING.md`, `SECURITY.md` и `SUPPORT.md` также из каталога `docs/`, поэтому корень репозитория остаётся чистым.

## Статус

Проект находится в активной разработке до релиза `1.0`. Интерфейсы, схема БД и конфигурация ещё могут меняться.
