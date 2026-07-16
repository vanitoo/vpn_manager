# Remnawave VPN Bot

Telegram-бот для продажи VPN-доступа: тарифы, Telegram Stars, ЮKassa, SQLite-подписки и выдача ссылок Remnawave.

## Что уже реализовано

- каталог VPN-тарифов;
- покупка через Telegram Stars;
- ЮKassa без webhook: ссылка на оплату и кнопка проверки статуса;
- SQLite-база: пользователи, тарифы, платежи, подписки;
- продление доступа: новая покупка добавляет дни после текущего активного срока;
- раздел `Мой VPN` с персональной ссылкой подписки;
- Remnawave API 2.7.x: создание, продление и получение `subscriptionUrl`;
- админ-команда `/admin` со статистикой и статусами провайдеров;
- прокси для Telegram Bot API и логирование;
- безопасный stub-режим без Remnawave API.

## Запуск

```bash
cp .env.example .env
# заполнить BOT_TOKEN и ADMIN_IDS
python -m app.main
```

Docker:

```bash
docker compose up -d --build
```

## Команды

```text
/start    Главное меню
/plans    Тарифы VPN
/vpn      Мой VPN
/support  Поддержка
/id       Telegram ID
/admin    Админка, только ADMIN_IDS
```

## Тарифы

При пустой базе бот добавляет два стартовых тарифа:

- 30 дней, 199 ₽;
- 90 дней, 499 ₽.

Они определены в `DEFAULT_PLANS` файла `app/main.py`. Следующим этапом добавим полноценное управление тарифами из админки, без ручного редактирования Python, потому что это не 2009 год.

## Платежи

### Telegram Stars

```env
PAYMENT_PROVIDERS=stars
STARS_RUB_PER_STAR=1.70
```

Стоимость тарифа хранится в рублях. Stars считаются так:

```text
ceil(price_rub / STARS_RUB_PER_STAR)
```

### ЮKassa

```env
PAYMENT_PROVIDERS=stars,yookassa
YOOKASSA_ENABLED=true
YOOKASSA_SHOP_ID=123456
YOOKASSA_SECRET_KEY=live_xxxxxxxxx
RECEIPT_REQUIRE_CONTACT=false
RECEIPT_FALLBACK_EMAIL=orders@example.com
```

Пользователь открывает платёжную ссылку, возвращается в бот и нажимает `Я оплатил / проверить`. Бот проверяет статус через API ЮKassa и только затем выдаёт VPN-доступ.

## Remnawave

```env
REMNAWAVE_BASE_URL=https://rw.example.com
REMNAWAVE_API_TOKEN=replace_me
REMNAWAVE_INTERNAL_SQUAD_UUID=replace_me
REMNAWAVE_EXTERNAL_SQUAD_UUID=
REMNAWAVE_SUBSCRIPTION_BASE_URL=
REMNAWAVE_DEFAULT_TRAFFIC_GB=0
REMNAWAVE_HWID_DEVICE_LIMIT=0
```

Интеграция изолирована в `app/remnawave.py`.

Для Remnawave 2.7.x используется схема:

```text
GET   /api/users/by-email/{email}  найти пользователя
POST  /api/users                   создать пользователя
PATCH /api/users                   продлить / обновить пользователя
```

При создании отправляются поля:

```text
username
status=ACTIVE
expireAt
activeInternalSquads=[REMNAWAVE_INTERNAL_SQUAD_UUID]
email
telegramId
description
trafficLimitBytes, если тариф с лимитом
trafficLimitStrategy=NO_RESET
hwidDeviceLimit, если REMNAWAVE_HWID_DEVICE_LIMIT > 0
externalSquadUuid, если REMNAWAVE_EXTERNAL_SQUAD_UUID заполнен
```

Для продления бот ищет пользователя по email `tg<telegram_id>@bot.local`. Если пользователь уже активен, новые дни добавляются к текущему `expireAt`, а не к текущей дате. Если пользователя нет, создаётся новый.

Ссылка берётся из поля `subscriptionUrl`. Если панель его не вернула, бот пробует собрать ссылку из `REMNAWAVE_SUBSCRIPTION_BASE_URL` и `shortUuid`/`uuid`.

## SQLite и будущий переход на PostgreSQL

Текущие таблицы:

```text
plans
users
subscriptions
payments
receipt_contacts
app_settings
app_meta
```

SQLite подходит для MVP и одного процесса бота. Переход на PostgreSQL сделаем через слой `app/db.py`: бизнес-логика не должна зависеть от SQL-диалекта, иначе миграция снова станет религиозным обрядом.

## Важно

- Не хранить `.env`, токены, ключи ЮKassa и Remnawave API в Git.
- Если токен был отправлен в чат, перевыпусти его в Remnawave.
- Для реальной выдачи нужен `REMNAWAVE_INTERNAL_SQUAD_UUID`.
