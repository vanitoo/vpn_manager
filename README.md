# Warp Private Network

Self-hosted Telegram-платформа для продажи и управления VPN-доступом через Remnawave.

Проект объединяет пользовательский Telegram-бот, управление подписками, оплату, автоматическую диагностику, FAQ, поддержку через Telegram Topics и административные инструменты. Отдельная веб-панель для пользователей не требуется.

> Техническое имя репозитория: `vpn_manager`.

## Возможности

### Для пользователя

- покупка VPN-доступа;
- тестовый доступ;
- просмотр активной подписки и срока действия;
- получение персональной ссылки Remnawave;
- продление подписки;
- оплата через Telegram Stars и внешних провайдеров;
- раздел `❓ Помощь`;
- управляемый FAQ;
- автоматическая диагностика подписки;
- обращение к специалисту с текстом, фото, видео, файлами и голосовыми сообщениями.

### Для администратора

- Telegram-админка;
- управление пользователями и тарифами;
- просмотр состояния Remnawave, nodes и squads;
- синхронизация пользователей;
- рассылки и правила напоминаний;
- резервное копирование SQLite;
- просмотр логов и системной информации;
- управление FAQ;
- статистика обращений;
- ответы пользователям через отдельные темы Telegram-группы.

## Архитектура

```text
Пользователь Telegram
        │
        ▼
     aiogram
        │
 ┌──────┼───────────────┐
 │      │               │
 ▼      ▼               ▼
VPN   Payments        Support
 │      │               │
 └──────┼───────┬───────┘
        ▼       ▼
      SQLite  Remnawave API
                │
                ▼
           VPN nodes/squads
```

Основные компоненты:

```text
app/
├── main.py                 запуск приложения
├── config.py               общая конфигурация
├── db.py                   SQLite и бизнес-данные
├── remnawave.py            клиент Remnawave API
├── user_vpn_handlers.py    пользовательские VPN-сценарии
├── external_payment_handlers.py
├── admin_*                 административные разделы
├── faq.py                  хранение FAQ
├── faq_handlers.py         пользовательский и административный FAQ
└── support/                диагностика, тикеты и Telegram Topics
```

Подробности: [ARCHITECTURE.md](ARCHITECTURE.md).

## Требования

- Python 3.11 или новее;
- Telegram Bot Token;
- SQLite;
- Remnawave 2.7.x или совместимый API;
- Docker и Docker Compose, если используется контейнерный запуск.

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

Локальный запуск:

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

## Основная конфигурация

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

Все доступные параметры перечислены в `.env.example`.

## Remnawave

```env
REMNAWAVE_BASE_URL=https://panel.example.com
REMNAWAVE_API_TOKEN=replace_me
REMNAWAVE_INTERNAL_SQUAD_UUID=
REMNAWAVE_EXTERNAL_SQUAD_UUID=
REMNAWAVE_SUBSCRIPTION_BASE_URL=https://sub.example.com
REMNAWAVE_DEFAULT_TRAFFIC_GB=0
REMNAWAVE_HWID_DEVICE_LIMIT=0
```

Бот умеет:

- находить пользователя по служебному email;
- создавать пользователя;
- продлевать существующую подписку;
- назначать internal squad;
- применять лимит трафика и устройств;
- получать или собирать ссылку подписки;
- выполнять диагностические запросы к Remnawave API.

При продлении дни добавляются к текущей активной дате окончания. Если срок уже истёк, новый период считается от текущего момента.

## Платежи

Поддерживаемые провайдеры задаются через:

```env
PAYMENT_PROVIDERS=stars,yookassa,cryptomus,lava,platega
```

### Telegram Stars

```env
ENABLE_STARS=true
STARS_RUB_PER_STAR=1.70
```

Стоимость тарифа хранится в рублях, количество Stars рассчитывается автоматически.

### ЮKassa

```env
YOOKASSA_ENABLED=true
YOOKASSA_SHOP_ID=
YOOKASSA_SECRET_KEY=
YOOKASSA_RETURN_URL=https://t.me
YOOKASSA_TEST_MODE=true
```

Внешняя оплата работает без обязательного webhook: пользователь открывает ссылку, возвращается в бот и запускает проверку платежа.

### Cryptomus, Lava и Platega

Провайдеры включаются отдельными переменными из `.env.example`. Пустая или отключённая конфигурация не должна ломать запуск бота.

## Помощь и поддержка

Пользовательский сценарий:

```text
FAQ
 ↓
Автоматическая диагностика
 ↓
Выбор категории проблемы
 ↓
Создание тикета
 ↓
Отдельная тема Telegram-группы
 ↓
Ответ специалиста
```

Для поддержки используется Telegram-группа с включёнными темами. Один тикет соответствует одной теме. SQLite остаётся источником данных, Telegram используется как интерфейс оператора.

Настройка:

```env
SUPPORT_ENABLED=true
SUPPORT_GROUP_ID=-1001234567890
```

После добавления бота в группу отправьте `/chatid`, чтобы получить её числовой ID.

Команды оператора внутри темы:

```text
/take   взять обращение в работу
/wait   ожидать ответа пользователя
/close  закрыть обращение
```

Подробности: [SUPPORT.md](SUPPORT.md).

## База данных

Основные таблицы:

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

SQLite рассчитана на один экземпляр приложения и текущий объём MVP. Код доступа к данным изолируется от обработчиков, чтобы последующий переход на PostgreSQL не потребовал переписывать весь проект, как это обычно случается после фразы «пока хватит SQLite».

## Безопасность

- не добавляйте `.env` в Git;
- не публикуйте Telegram, Remnawave и платёжные токены;
- используйте отдельные тестовые и рабочие ключи;
- ограничивайте список `ADMIN_IDS`;
- регулярно создавайте резервные копии базы;
- при утечке токена немедленно перевыпускайте его.

Политика сообщения об уязвимостях: [SECURITY.md](SECURITY.md).

## Документация

- [CHANGELOG.md](CHANGELOG.md) — история изменений;
- [ROADMAP.md](ROADMAP.md) — план развития;
- [ARCHITECTURE.md](ARCHITECTURE.md) — архитектура;
- [SUPPORT.md](SUPPORT.md) — система поддержки;
- [CONTRIBUTING.md](CONTRIBUTING.md) — правила участия;
- [SECURITY.md](SECURITY.md) — безопасность.

## Статус проекта

Проект находится в активной разработке. До стабильного релиза `1.0` интерфейсы, структура базы и конфигурация могут изменяться.

## Лицензия

Условия использования определяются файлом `LICENSE`, если он присутствует в репозитории.
