from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu(*, active: bool = False, trial_available: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if active:
        rows.append([InlineKeyboardButton(text='🔑 Подключить VPN', callback_data='my_vpn')])
        rows.append([InlineKeyboardButton(text='💳 Продлить доступ', callback_data='plans')])
    else:
        rows.append([InlineKeyboardButton(text='🛡 Купить VPN', callback_data='plans')])
    if trial_available:
        rows.append([InlineKeyboardButton(text='🎁 Тестовый доступ', callback_data='trial')])
    rows.append([InlineKeyboardButton(text='🆘 Поддержка', callback_data='help')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plans_menu(plans: list[dict]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"{p['title']} · {p['price_rub']} ₽", callback_data=f"plan:{p['id']}")] for p in plans]
    rows.append([InlineKeyboardButton(text='⌂ Главное', callback_data='home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plan_menu(plan_id: int, *, has_pending: bool = False) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text='Купить', callback_data=f'buy:{plan_id}')]]
    if has_pending:
        rows.append([InlineKeyboardButton(text='Проверить оплату', callback_data=f'pending:{plan_id}')])
    rows.append([InlineKeyboardButton(text='← Тарифы', callback_data='plans')])
    rows.append([InlineKeyboardButton(text='⌂ Главное', callback_data='home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_methods_menu(plan_id: int, providers: list[str]) -> InlineKeyboardMarkup:
    labels = {'stars': '⭐ Telegram Stars', 'yookassa': '💳 Карта / СБП'}
    rows = [[InlineKeyboardButton(text=labels.get(p, p), callback_data=f'pay:{p}:{plan_id}')] for p in providers]
    rows.append([InlineKeyboardButton(text='← К тарифу', callback_data=f'plan:{plan_id}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def external_payment_menu(payment_id: int, payment_url: str, plan_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Оплатить', url=payment_url)],
        [InlineKeyboardButton(text='Я оплатил', callback_data=f'epay:check:{payment_id}')],
        [InlineKeyboardButton(text='← К тарифу', callback_data=f'plan:{plan_id}')],
    ])


def after_purchase_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔑 Подключить VPN', callback_data='my_vpn')],
        [InlineKeyboardButton(text='⌂ Главное', callback_data='home')],
    ])


def my_vpn_menu(*, subscription_url: str = '', happ_url: str = '') -> InlineKeyboardMarkup:
    rows = []
    if subscription_url:
        rows.append([InlineKeyboardButton(text='🌐 Открыть страницу подписки', url=subscription_url)])
    rows.append([InlineKeyboardButton(text='Продлить', callback_data='plans')])
    rows.append([InlineKeyboardButton(text='⌂ Главное', callback_data='home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='👥 Пользователи', callback_data='admin:users'), InlineKeyboardButton(text='💰 Тарифы', callback_data='admin:plans')],
        [InlineKeyboardButton(text='🌍 Remnawave', callback_data='admin:remna'), InlineKeyboardButton(text='ℹ️ Система', callback_data='admin:system')],
        [InlineKeyboardButton(text='📊 Статистика', callback_data='admin:stats')],
    ])


def admin_remna_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🖥 Серверы / Nodes', callback_data='admin:remna:nodes')],
        [InlineKeyboardButton(text='🧩 Squads', callback_data='admin:remna:squads')],
        [InlineKeyboardButton(text='👥 Пользователи Remna', callback_data='admin:remna:users')],
        [InlineKeyboardButton(text='🔄 Синхронизация Remna → Bot', callback_data='admin:remna:sync')],
        [InlineKeyboardButton(text='← Админка', callback_data='admin:home')],
    ])


def admin_users_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔎 Поиск', callback_data='admin:usersearch')],
        [InlineKeyboardButton(text='🟢 Активные', callback_data='admin:users:active'), InlineKeyboardButton(text='🔴 Просроченные', callback_data='admin:users:expired')],
        [InlineKeyboardButton(text='🕘 Последние', callback_data='admin:users:recent')],
        [InlineKeyboardButton(text='← Админка', callback_data='admin:home')],
    ])


def admin_user_menu(telegram_id: int, is_active: bool) -> InlineKeyboardMarkup:
    action = '🚫 Заблокировать' if is_active else '✅ Активировать'
    key = 'block' if is_active else 'activate'
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🎁 Выдать тест', callback_data=f'admin:trial:{telegram_id}')],
        [InlineKeyboardButton(text=action, callback_data=f'admin:{key}:{telegram_id}')],
        [InlineKeyboardButton(text='🔄 Синхронизировать', callback_data=f'admin:sync:{telegram_id}')],
        [InlineKeyboardButton(text='← Пользователи', callback_data='admin:users')],
    ])


def admin_plans_menu(plans: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for p in plans:
        public = int(p.get('is_public', 1))
        badge = '🛒' if public else '🔒'
        rows.append([InlineKeyboardButton(text=f"{badge} {'🟢' if p['is_active'] else '⚪'} {p['title']} · {p['price_rub']} ₽", callback_data=f"admin:plan:{p['id']}")])
    rows.append([InlineKeyboardButton(text='➕ Новый публичный', callback_data='admin:planadd')])
    rows.append([InlineKeyboardButton(text='🔒 Новый служебный', callback_data='admin:planadd_service')])
    rows.append([InlineKeyboardButton(text='← Админка', callback_data='admin:home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_plan_menu(plan_id: int, is_active: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✏️ Название', callback_data=f'admin:planedit:title:{plan_id}'), InlineKeyboardButton(text='💰 Цена', callback_data=f'admin:planedit:price_rub:{plan_id}')],
        [InlineKeyboardButton(text='📅 Дни', callback_data=f'admin:planedit:duration_days:{plan_id}'), InlineKeyboardButton(text='📶 Трафик', callback_data=f'admin:planedit:traffic_gb:{plan_id}')],
        [InlineKeyboardButton(text='📝 Описание', callback_data=f'admin:planedit:description:{plan_id}')],
        [InlineKeyboardButton(text='⛔ Выключить' if is_active else '✅ Включить', callback_data=f'admin:plantoggle:{plan_id}')],
        [InlineKeyboardButton(text='← Тарифы', callback_data='admin:plans')],
    ])
