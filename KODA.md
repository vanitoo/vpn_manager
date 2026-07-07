# Remnawave VPN Bot — KODA Context

## Обзор проекта

**Remnawave VPN Bot v3.3.1** — Telegram-бот для продажи VPN-подписок с автоматической выдачей доступа через панель Remnawave.
Бот управляет каталогом тарифов, обрабатывает платежи (Telegram Stars, ЮKassa, Lava, Platega) и взаимодействует с API Remnawave для создания пользователей и генерации ссылок на подключение.

## Основные технологии

| Слой | Технология |
|------|-----------|
| Язык | Python 3.11+ (async) |
| Фреймворк бота | aiogram 3.x |
| База данных | SQLite (aiosqlite) |
| Конфигурация | `.env` через python-dotenv |
| Интеграция VPN | Remnawave API 2.7.x |
| Прокси | aiohttp-socks (failover-режим) |
| Сборка/контейнер | Docker, docker-compose |

## Структура проекта

```
.
├── app/
│   ├── __init__.py          # Инициализация (runtime, DEFAULT_PLANS)
│   ├── boot.py              # Точка входа: инициализация бота, логирование, запуск
│   ├── config.py            # Настройки из .env (Settings dataclass)
│   ├── db.py                # SQLite: схемы, миграции, CRUD (тарифы, подписки, платежи)
│   ├── runtime.py           # Глобальное состояние инициализации
│   ├── proxy_manager.py     # Управление прокси-сессиями
│   ├── remnawave.py         # Клиент для API Remnawave
│   ├── user_vpn_handlers.py # Обработчики для пользователей (тарифы, vpn, поддержка)
│   └── admin_remna_handlers.py # Обработчики админки
├── data/
│   └── vpn_bot.sqlite3      # База данных
├── logs/
│   └── bot.log              # Лог с ротацией
├── docker-compose.yml       # Docker-сборка
├── Dockerfile               # Образ контейнера
├── requirements.txt         # Зависимости Python
├── run.cmd                  # Запуск Windows (UTF-8 fix included)
├── run.sh                   # Запуск Linux
└── .env.example             # Шаблон переменных окружения
```

## База данных

**Schema version:** хранится в `app_meta.db_schema_version`.

Три таблицы:

| Таблица | Назначение |
|---------|-----------|
| `plans` | Тарифы VPN (slug, title, duration_days, traffic_gb, price_rub, is_active, sort_order) |
| `users` | Пользователи Telegram (telegram_id, username, full_name) |
| `subscriptions` | Активные подписки (user_id, plan_id, status, expires_at, remnawave_user_id, subscription_url) |
| `payments` | История платежей (provider, amount_rub, status, provider_payment_id, subscription_id) |
| `receipt_contacts` | Контакты для чеков ЮKassa (email, phone) |
| `app_settings` | Глобальные настройки бота |
| `app_meta` | Метаданные (версия схемы) |

**Особенности:**
- Подписки продлеваются: новые дни добавляются к текущему сроку действия.
- Автоматическая выдача доступа через Remnawave при успешной оплате.

## Сборка и запуск

### Локальный запуск (Windows)

```bat
run.cmd
```

### Локальный запуск (Linux/Mac)

```bash
python -m app.main
# или
./run.sh
```

### Docker

```bash
docker compose up -d --build
```

### Переменные окружения (.env)

**Основные:**
| Переменная | Описание |
|-----------|----------|
| `BOT_TOKEN` | Токен бота от @BotFather |
| `ADMIN_IDS` | ID администраторов (через запятую) |
| `DB_PATH` | Путь к БД (`data/vpn_bot.sqlite3`) |
| `PAYMENT_PROVIDERS` | Список провайдеров (`stars`, `yookassa`, `lava`, `platega`) |

**Remnawave:**
| Переменная | Описание |
|-----------|----------|
| `REMNAWAVE_BASE_URL` | URL панели (например, `https://pp.example.com`) |
| `REMNAWAVE_API_TOKEN` | Токен API |
| `REMNAWAVE_INTERNAL_SQUAD_UUID` | UUID внутренней группы (для доступа) |

**Платежи:**
| Переменная | Описание |
|-----------|----------|
| `STARS_RUB_PER_STAR` | Курс конвертации (по умолч. 1.70) |
| `YOOKASSA_ENABLED` | Включить ЮKassa |
| `YOOKASSA_SHOP_ID` | ID магазина |
| `YOOKASSA_SECRET_KEY` | Секретный ключ |

## Формула цены

Для Telegram Stars:
```text
stars = ceil(price_rub / STARS_RUB_PER_STAR)
```

## Команды бота

| Команда | Описание | Доступ |
|---------|----------|-------|
| `/start` | Главное меню | Все |
| `/plans` | Каталог тарифов | Все |
| `/vpn` | Моя подписка (ссылка) | Все |
| `/support` | Поддержка | Все |
| `/id` | Показать ID | Все |
| `/admin` | Админ-панель | Админы |

## Админка

- **Статистика**: количество пользователей, активных подписок, выручка.
- **Тарифы**: управление списками (вкл/выкл, редактирование).
- **Remnawave**: диагностика подключения, статус провайдеров.
- **Платежи**: история транзакций.

## Архитектура

```
boot.py (asyncio.run)
  ├── config.py → Settings
  ├── db.py → init_db() + CRUD
  ├── remnawave.py → RemnawaveClient (API calls)
  ├── user_vpn_handlers.py → Рутер пользователя
  └── admin_remna_handlers.py → Рутер админа
```

**Ключевые паттерны:**
- Глобальный объект `settings` (dataclass).
- Асинхронная работа с БД через `aiosqlite`.
- FSM-состояния в памяти (`MemoryStorage`).
- Изоляция логики Remnawave в отдельном модуле с fallback-режимом.

## Стиль кодирования

- Python 3.11+ с `from __future__ import annotations`
- Строгая типизация (type hints)
- Комментарии на русском языке
- Функциональный стиль для CRUD
- Использование dataclass для конфигурации
