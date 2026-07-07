# Remnawave VPN Bot

Telegram-бот для продажи VPN-доступа: тарифы, Telegram Stars, ЮKassa, SQLite-подписки и выдача ссылок Remnawave.

## Что уже реализовано

- каталог VPN-тарифов;
- покупка через Telegram Stars;
- ЮKassa без webhook: ссылка на оплату и кнопка проверки статуса;
- SQLite-база: пользователи, тарифы, платежи, подписки;
- продление доступа: новая покупка добавляет дни после текущего активного срока;
- раздел `Мой VPN` с персональной ссылкой подписки;
- админ-команда `/admin` со статистикой и статусами провайдеров;
- прокси для Telegram Bot API и логирование;
- безопасный режим без Remnawave API: бот запускается и выдаёт тестовую ссылку, чтобы интерфейс и платежи можно было проверить до интеграции.

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
REMNAWAVE_BASE_URL=https://panel.example.com
REMNAWAVE_API_TOKEN=replace_me
REMNAWAVE_SUBSCRIPTION_BASE_URL=https://sub.example.com
```

Интеграция изолирована в `app/remnawave.py`.

Сейчас в нём есть один центральный метод `create_or_extend_user()`. До подключения точной схемы API твоей конкретной версии Remnawave он работает в stub-режиме: создаёт тестовый ID и URL, не пытаясь изобрести несуществующий endpoint.

После получения документации/ответа от панели надо уточнить:

1. endpoint создания или обновления пользователя;
2. формат даты истечения доступа;
3. поле лимита трафика;
4. где API возвращает subscription URL;
5. как корректно продлевать уже существующего пользователя, а не создавать дубль.

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
- До подключения Remnawave реальная VPN-конфигурация не создаётся.
- Проверь, какой именно API опубликован в твоей панели Remnawave, прежде чем включать `REMNAWAVE_API_TOKEN` в проде.
