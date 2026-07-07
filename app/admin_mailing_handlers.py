from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup

from app import runtime
from app.keyboards import admin_mailing_menu, admin_menu, reminder_rule_menu
from app.mailing import get_reminder_rule, init_mailing_tables, list_reminder_rules, reminder_stats, toggle_reminder_rule, update_reminder_message

router = Router()


class MailingForm(StatesGroup):
    edit_message = State()


def esc(value: str) -> str:
    return value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def rules_menu(rules: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for rule in rules:
        status = '🟢' if rule['is_enabled'] else '⚪'
        rows.append([InlineKeyboardButton(text=f"{status} {rule['title']}", callback_data=f"admin:mailing:rule:{rule['code']}")])
    rows.append([InlineKeyboardButton(text='← Рассылки', callback_data='admin:mailing')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == 'admin:mailing')
async def mailing_home(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await callback.answer()
    await init_mailing_tables(runtime.settings.db_path)
    stats = await reminder_stats(runtime.settings.db_path)
    text = (
        '📣 <b>Рассылки и напоминания</b>\n\n'
        f"Правил: <b>{stats['rules']}</b>\n"
        f"Включено: <b>{stats['enabled']}</b>\n"
        f"Событий в журнале: <b>{stats['events']}</b>\n"
        f"Отправлено: <b>{stats['sent']}</b>\n"
        f"Ошибок: <b>{stats['errors']}</b>\n\n"
        'Пока это настройки и dry-run. Автоотправку подключим отдельным безопасным циклом, чтобы бот случайно не стал спам-машиной с энтузиазмом офисного принтера.'
    )
    await callback.message.answer(text, reply_markup=admin_mailing_menu())


@router.callback_query(F.data == 'admin:mailing:rules')
async def mailing_rules(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await callback.answer('Правила')
    rules = await list_reminder_rules(runtime.settings.db_path)
    await callback.message.answer('⚙️ <b>Правила напоминаний</b>\n\nВыберите правило:', reply_markup=rules_menu(rules))


@router.callback_query(F.data.startswith('admin:mailing:rule:'))
async def mailing_rule_card(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    code = callback.data.split(':', 3)[3]
    rule = await get_reminder_rule(runtime.settings.db_path, code)
    if not rule:
        await callback.answer('Не найдено', show_alert=True)
        return
    await callback.answer()
    when = f"{rule['offset_hours']} ч от даты окончания"
    status = 'включено' if rule['is_enabled'] else 'выключено'
    text = (
        f"📣 <b>{esc(rule['title'])}</b>\n\n"
        f"Код: <code>{esc(rule['code'])}</code>\n"
        f"Смещение: <b>{esc(when)}</b>\n"
        f"Статус: <b>{status}</b>\n\n"
        f"Текст:\n<code>{esc(rule['message'])}</code>"
    )
    await callback.message.answer(text, reply_markup=reminder_rule_menu(code, bool(rule['is_enabled'])))


@router.callback_query(F.data.startswith('admin:mailing:toggle:'))
async def mailing_toggle(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    code = callback.data.split(':', 3)[3]
    await toggle_reminder_rule(runtime.settings.db_path, code)
    await callback.answer('Готово')
    rule = await get_reminder_rule(runtime.settings.db_path, code)
    await callback.message.answer(f"Правило <b>{esc(rule['title'])}</b>: {'включено' if rule['is_enabled'] else 'выключено'}", reply_markup=reminder_rule_menu(code, bool(rule['is_enabled'])))


@router.callback_query(F.data.startswith('admin:mailing:edit:'))
async def mailing_edit(callback: CallbackQuery, state: FSMContext) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    code = callback.data.split(':', 3)[3]
    await state.update_data(rule_code=code)
    await state.set_state(MailingForm.edit_message)
    await callback.answer()
    await callback.message.answer('Пришлите новый текст сообщения.\n\nМожно использовать обычный текст без HTML. Кнопку продления добавим отдельным действием позже.')


@router.message(MailingForm.edit_message)
async def mailing_edit_message(message: Message, state: FSMContext) -> None:
    if not runtime.admin(message):
        return
    data = await state.get_data()
    code = data.get('rule_code')
    await update_reminder_message(runtime.settings.db_path, code, message.text or '')
    await state.clear()
    rule = await get_reminder_rule(runtime.settings.db_path, code)
    await message.answer(f"✅ Текст правила <b>{esc(rule['title'])}</b> обновлён.", reply_markup=reminder_rule_menu(code, bool(rule['is_enabled'])))


@router.callback_query(F.data == 'admin:mailing:stats')
async def mailing_stats(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await callback.answer('Статистика')
    stats = await reminder_stats(runtime.settings.db_path)
    text = '📊 <b>Статистика рассылок</b>\n\n' + '\n'.join(f'{k}: <b>{v}</b>' for k, v in stats.items())
    await callback.message.answer(text, reply_markup=admin_mailing_menu())


@router.callback_query(F.data == 'admin:mailing:dryrun')
async def mailing_dryrun(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await callback.answer('Dry-run')
    rules = [r for r in await list_reminder_rules(runtime.settings.db_path) if r['is_enabled']]
    now = datetime.now(timezone.utc)
    async with aiosqlite.connect(runtime.settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT telegram_id, expires_at FROM subscriptions WHERE status='active' ORDER BY expires_at LIMIT 200") as cur:
            subs = [dict(r) for r in await cur.fetchall()]
    hits = []
    for sub in subs:
        try:
            exp = datetime.fromisoformat(str(sub['expires_at']).replace('Z', '+00:00'))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        for rule in rules:
            target = exp.timestamp() + int(rule['offset_hours']) * 3600
            delta_hours = (target - now.timestamp()) / 3600
            if -24 <= delta_hours <= 24:
                hits.append(f"{rule['title']}: {sub['telegram_id']} · expire {str(sub['expires_at'])[:16]}")
    text = '🧪 <b>Dry-run ближайших уведомлений</b>\n\n' + ('\n'.join(hits[:40]) if hits else 'На ближайшие сутки кандидатов нет.')
    await callback.message.answer(text[:3900], reply_markup=admin_mailing_menu())
