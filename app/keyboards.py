from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu(*, support_url: str = '') -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text='🛡 Тарифы VPN', callback_data='plans')],
        [InlineKeyboardButton(text='🔑 Мой VPN', callback_data='my_vpn')],
    ]
    if support_url:
        rows.append([InlineKeyboardButton(text='🆘 Поддержка', url=support_url)])
    rows.append([InlineKeyboardButton(text='ℹ️ Помощь', callback_data='help')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plans_menu(plans: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for plan in plans:
        price = int(plan.get('price_rub') or 0)
        days = int(plan.get('duration_days') or 0)
        rows.append([
            InlineKeyboardButton(
                text=f"{plan['title']} · {days} дн. · {price} ₽",
                callback_data=f"plan:{plan['id']}",
            )
        ])
    if not rows:
        rows = [[InlineKeyboardButton(text='Тарифов пока нет', callback_data='noop')]]
    rows.append([InlineKeyboardButton(text='🏠 Главное меню', callback_data='home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plan_menu(plan_id: int, *, has_pending: bool = False) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text='🛒 Купить доступ', callback_data=f'buy:{plan_id}')]]
    if has_pending:
        rows.append([InlineKeyboardButton(text='✅ Проверить оплату', callback_data=f'pending:{plan_id}')])
    rows.append([InlineKeyboardButton(text='⬅️ К тарифам', callback_data='plans')])
    rows.append([InlineKeyboardButton(text='🏠 Главное меню', callback_data='home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_methods_menu(plan_id: int, providers: list[str]) -> InlineKeyboardMarkup:
    labels = {
        'stars': '⭐ Telegram Stars',
        'yookassa': '💳 ЮKassa: карта / СБП',
        'lava': '💳 Lava',
        'platega': '💳 Platega',
    }
    rows = [[InlineKeyboardButton(text=labels.get(p, p), callback_data=f'pay:{p}:{plan_id}')] for p in providers]
    rows.append([InlineKeyboardButton(text='⬅️ К тарифу', callback_data=f'plan:{plan_id}')])
    rows.append([InlineKeyboardButton(text='🏠 Главное меню', callback_data='home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def external_payment_menu(payment_id: int, payment_url: str, plan_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='💳 Перейти к оплате', url=payment_url)],
        [InlineKeyboardButton(text='✅ Я оплатил / проверить', callback_data=f'epay:check:{payment_id}')],
        [InlineKeyboardButton(text='⬅️ К тарифу', callback_data=f'plan:{plan_id}')],
        [InlineKeyboardButton(text='🏠 Главное меню', callback_data='home')],
    ])


def after_purchase_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔑 Открыть мой VPN', callback_data='my_vpn')],
        [InlineKeyboardButton(text='🛡 Продлить / купить ещё', callback_data='plans')],
        [InlineKeyboardButton(text='🏠 Главное меню', callback_data='home')],
    ])


def my_vpn_menu(*, subscription_url: str = '') -> InlineKeyboardMarkup:
    rows = []
    if subscription_url:
        rows.append([InlineKeyboardButton(text='🔗 Открыть подписку', url=subscription_url)])
    rows.append([InlineKeyboardButton(text='🔁 Продлить доступ', callback_data='plans')])
    rows.append([InlineKeyboardButton(text='🏠 Главное меню', callback_data='home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🛡 Тарифы', callback_data='admin:plans')],
        [InlineKeyboardButton(text='📊 Статистика', callback_data='admin:stats')],
        [InlineKeyboardButton(text='💳 Платежные системы', callback_data='admin:payments')],
        [InlineKeyboardButton(text='ℹ️ О системе', callback_data='admin:system')],
        [InlineKeyboardButton(text='🏠 В главное меню бота', callback_data='home')],
    ])
