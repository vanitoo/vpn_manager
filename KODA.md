# KamenevaBook Bot — KODA Context

## Обзор проекта

**KamenevaBook Bot v2.1** — Telegram-бот для продажи электронных книг через Telegram Stars.
Бот позволяет пользователям просматривать каталог книг, покупать их через встроенную оплату Telegram Stars и мгновенно получать файлы книг. Администраторы управляют книгами, статистикой и пользователями прямо через Telegram.

## Основные технологии

| Слой | Технология |
|------|-----------|
| Язык | Python 3.11+ (async) |
| Фреймворк бота | aiogram 3.x |
| База данных | SQLite (aiosqlite) |
| Конфигурация | `.env` через python-dotenv |
| Прокси | aiohttp-socks (failover-режим) |
| Сборка/контейнер | Docker, docker-compose |

## Структура проекта

```
.
├── app/
│   ├── main.py              # Основной файл: бот, рутеры, FSM-сценарии админки
│   ├── config.py            # Настройки из .env (Settings dataclass)
│   ├── db.py                # SQLite: схемы, миграции, CRUD книг/покупок
│   ├── keyboards.py         # Inline-клавиатуры (меню бота и админки)
│   └── proxy_manager.py     # Управление прокси-сессиями с healthcheck
├── books/                   # Файлы книг (папки по slug)
│   └── <slug>/
│       ├── cover.jpg        # Обложка
│       ├── description.md   # Описание книги
│       ├── meta.json        # Метаданные
│       └── <slug>.<ext>     # Файлы книги (PDF, EPUB, FB2, DOCX...)
├── content/start/           # Стартовый текст и картинка бота
│   ├── start.md
│   └── start.jpg
├── data/
│   └── bot.sqlite3          # База данных
├── backups/                 # JSON-бэкапы пользователей и покупок
├── logs/
│   ├── bot.log              # Основной лог с ротацией
│   └── purchases.log        # Лог покупок
├── docker-compose.yml       # Docker-сборка
├── Dockerfile               # Образ контейнера
├── requirements.txt         # Зависимости Python
├── run.bat                  # Запуск Windows
├── run.sh                   # Запуск Linux
└── .env.example             # Шаблон переменных окружения
```

## База данных

**Schema version:** 5

Три таблицы:

| Таблица | Назначение |
|---------|-----------|
| `books` | Каталог книг (slug, title, description, price_rub, price_stars, cover_path, file_paths, is_active, sort_order, created_at, updated_at) |
| `purchases` | Покупки (user_id, username, full_name, book_id, payload, currency, total_amount, telegram_payment_charge_id, provider_payment_charge_id, created_at) |
| `app_settings` | Настройки (key, value) — используется для welcome_text и welcome_image_path |

**Миграции:** автоматические — при старте `init_db()` добавляет отсутствующие колонки через `ALTER TABLE ADD COLUMN`.

## Сборка и запуск

### Локальный запуск

```bat
run.bat
```

или

```bash
python -m app.main
```

### Docker

```bash
docker compose up -d --build
```

### Переменные окружения (.env)

| Переменная | Описание | Значение по умолчанию |
|-----------|----------|----------------------|
| `BOT_TOKEN` | Token бота от @BotFather | (обязательно) |
| `ADMIN_IDS` | IDs администраторов через запятую | `319415227` |
| `DATABASE_PATH` | Путь к SQLite-базе | `data/bot.sqlite3` |
| `DELETE_WEBHOOK_ON_START` | Удалять вебхук при старте | `true` |
| `DROP_PENDING_UPDATES` | Отбрасывать накопившиеся обновления | `false` |
| `SEED_BOOKS_ON_START` | Инициализировать демо-книги при старте | `true` |
| `STARS_RUB_PER_STAR` | Курс рублей к Stars | `1.70` |
| `PROXY_MODE` | Режим прокси: `off`, `failover` | `failover` |
| `PROXY` | URL прокси (socks5, http и т.д.) | — |
| `PROXY_HEALTHCHECK_URL` | URL для проверки прокси | `https://api.telegram.org` |
| `PROXY_HEALTHCHECK_TIMEOUT` | Таймаут healthcheck (сек) | `8` |
| `PROXY_HEALTHCHECK_INTERVAL` | Интервал healthcheck (сек) | `60` |
| `LOG_LEVEL` | Уровень логирования | `INFO` |
| `LOG_FILE` | Путь к лог-файлу | `logs/bot.log` |

## Формула цены

Цена указывается в рублях (`price_rub`), бот автоматически пересчитывает в Telegram Stars:

```
stars = ceil(price_rub / STARS_RUB_PER_STAR)
```

Пример: при `STARS_RUB_PER_STAR=1.70` книга за 299 ₽ = 176 ⭐.

## Команды бота

| Команда | Описание | Доступ |
|---------|----------|-------|
| `/start` | Стартовое меню | Все |
| `/books` | Открыть книжную полку | Все |
| `/get` | Показать мои покупки | Все |
| `/id` | Показать свой ID | Все |
| `/admin` | Открыть админку | Только администраторы |

## Админка

Разделы админки:

- **Книги** — список всех книг, переключение вкл/выкл
- **Добавить** — FSM-сценарий добавления книги (название → slug → описание → цена → файлы → обложка)
- **Статистика** — общее количество книг, покупок, покупателей, выручка
- **Продажи** — последние 30 продаж
- **Покупатели** — список покупателей с суммами
- **Продажи по книгам** — рейтинг книг по количеству продаж
- **Бэкап / восстановление** — экспорт/импорт пользователей и покупок в JSON
- **Старт бота** — настройка стартового текста и картинки

### Включение/выключение книги

- `is_active = 1` — книга видна в каталоге, доступна для покупки
- `is_active = 0` — книга скрыта из каталога, купить нельзя, но остаётся в базе и админ видит

### Порядок сортировки

Поле `sort_order` — чем меньше число, тем выше книга в каталоге:

| sort_order | Позиция |
|-----------|--------|
| 10 | Первая |
| 20 | Вторая |
| 30 | Третья |

### Deep-link для книги

```
https://t.me/<bot_username>?start=book_<slug>
```

Переход по ссылке сразу открывает карточку конкретной книги.

## Бэкап

- Файлы: `backups/backup_YYYY-MM-DD_HH-MM-SS.json`
- Содержит: пользователей, покупки, привязку к книгам (по `book_slug` и `book_id`)
- Файлы книг в бэкап не входят — они лежат отдельно в `books/`

## Логирование

- **Основной лог:** `logs/bot.log` (RotatingFileHandler, 10 МБ, 5 файлов)
- **Лог покупок:** `logs/purchases.log` (отдельный логгер `purchases`)
- При старте выводится сводка: версия, схема БД, прокси, количество книг/пользователей/покупок

## FSM-сценарии

### Добавление книги (`AdminAddBook`)

1. `title` — название
2. `slug` — slug (автоматически из названия, можно изменить)
3. `description` — описание
4. `price` — цена в рублях
5. `files` — файлы книги (отправка документами или список путей)
6. `cover` — обложка (фото или путь)

### Редактирование книги (`AdminEditBook`)

Редактирует одно поле: `title`, `slug`, `description`, `price_rub`, `sort_order`, `cover_path`, `file_paths`.

### Настройка старта (`AdminStartSettings`)

1. `text` — текст стартового сообщения
2. `image` — картинка стартового сообщения

### Восстановление бэкапа (`AdminBackupRestore`)

1. `file` — JSON-файл бэкапа

## Архитектура

```
main.py (asyncio.run)
  ├── config.py → Settings
  ├── db.py → init_db() + CRUD
  ├── proxy_manager.py → ProxyManager (aiohttp сессии)
  ├── keyboards.py → InlineKeyboardMarkup
  └── router (aiogram Router)
       ├── Command-хендлеры (/start, /admin, /books, /get, /id)
       ├── CallbackQuery-хендлеры (кнопки)
       ├── PreCheckoutQuery (валидация инвойса)
       ├── successful_payment (запись покупки + выдача файлов)
       └── FSM-хендлеры (админка)
```

**Ключевые паттерны:**

- Глобальная переменная `settings: Settings` — конфигурация
- Глобальная переменная `proxy_manager: ProxyManager` — управление прокси
- Все асинхронные функции с `await`
- FSM-состояния в памяти (`MemoryStorage`)
- Книги при старте синхронизируются в файловую систему (`sync_book_folder`)

## Стиль кодирования

- Python 3.11+ с `from __future__ import annotations`
- Строгая типизация через type hints
- Dataclass для настроек (`Settings`)
- Функциональный стиль для CRUD (async-функции в `db.py`)
- AIogram Router для обработки команд и колбэков
- Комментарии на русском языке
