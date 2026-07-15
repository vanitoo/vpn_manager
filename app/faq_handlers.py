from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app import runtime
from app.faq import add_faq, delete_faq, get_faq, list_faq, toggle_faq, update_faq
from app.keyboards import admin_faq_item_menu, admin_faq_menu, faq_item_menu, faq_list_menu

router = Router()


class FAQForm(StatesGroup):
    add_question = State()
    add_answer = State()
    edit_value = State()


def esc(value: str) -> str:
    return value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


@router.callback_query(F.data == 'faq')
async def user_faq(callback: CallbackQuery) -> None:
    await callback.answer()
    items = await list_faq(runtime.settings.db_path, active_only=True)
    await callback.message.answer('❓ <b>Частые вопросы</b>\n\nВыберите вопрос:', reply_markup=faq_list_menu(items))


@router.callback_query(F.data.startswith('faq:item:'))
async def user_faq_item(callback: CallbackQuery) -> None:
    await callback.answer()
    item = await get_faq(runtime.settings.db_path, int(callback.data.rsplit(':', 1)[1]))
    if not item or not int(item['is_active']):
        await callback.message.answer('Вопрос не найден.', reply_markup=faq_item_menu())
        return
    await callback.message.answer(
        f"❓ <b>{esc(item['question'])}</b>\n\n{esc(item['answer'])}",
        reply_markup=faq_item_menu(),
    )


@router.callback_query(F.data == 'admin:faq')
async def admin_faq(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await callback.answer()
    items = await list_faq(runtime.settings.db_path, active_only=False)
    await callback.message.answer('❓ <b>Управление FAQ</b>', reply_markup=admin_faq_menu(items))


@router.callback_query(F.data.startswith('admin:faq:item:'))
async def admin_faq_item(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    item_id = int(callback.data.rsplit(':', 1)[1])
    item = await get_faq(runtime.settings.db_path, item_id)
    if not item:
        await callback.answer('Не найдено', show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        f"❓ <b>{esc(item['question'])}</b>\n\n{esc(item['answer'])}\n\n"
        f"Статус: <b>{'показывается' if item['is_active'] else 'скрыт'}</b>",
        reply_markup=admin_faq_item_menu(item_id, bool(item['is_active'])),
    )


@router.callback_query(F.data == 'admin:faq:add')
async def admin_faq_add(callback: CallbackQuery, state: FSMContext) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await state.set_state(FAQForm.add_question)
    await callback.answer()
    await callback.message.answer('Пришлите новый вопрос. Для отмены: /cancel')


@router.message(FAQForm.add_question)
async def admin_faq_add_question(message: Message, state: FSMContext) -> None:
    if not runtime.admin(message):
        return
    await state.update_data(question=message.text or '')
    await state.set_state(FAQForm.add_answer)
    await message.answer('Теперь пришлите ответ.')


@router.message(FAQForm.add_answer)
async def admin_faq_add_answer(message: Message, state: FSMContext) -> None:
    if not runtime.admin(message):
        return
    data = await state.get_data()
    item_id = await add_faq(runtime.settings.db_path, data.get('question') or 'Вопрос', message.text or '')
    await state.clear()
    item = await get_faq(runtime.settings.db_path, item_id)
    await message.answer('✅ Вопрос добавлен.', reply_markup=admin_faq_item_menu(item_id, bool(item['is_active'])))


@router.callback_query(F.data.startswith('admin:faq:editq:') | F.data.startswith('admin:faq:edita:'))
async def admin_faq_edit(callback: CallbackQuery, state: FSMContext) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    parts = callback.data.split(':')
    field = 'question' if parts[2] == 'editq' else 'answer'
    item_id = int(parts[3])
    await state.update_data(item_id=item_id, field=field)
    await state.set_state(FAQForm.edit_value)
    await callback.answer()
    await callback.message.answer('Пришлите новое значение. Для отмены: /cancel')


@router.message(FAQForm.edit_value)
async def admin_faq_edit_value(message: Message, state: FSMContext) -> None:
    if not runtime.admin(message):
        return
    data = await state.get_data()
    await update_faq(runtime.settings.db_path, int(data['item_id']), str(data['field']), message.text or '')
    item = await get_faq(runtime.settings.db_path, int(data['item_id']))
    await state.clear()
    await message.answer('✅ FAQ обновлён.', reply_markup=admin_faq_item_menu(int(item['id']), bool(item['is_active'])))


@router.callback_query(F.data.startswith('admin:faq:toggle:'))
async def admin_faq_toggle(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    item_id = int(callback.data.rsplit(':', 1)[1])
    await toggle_faq(runtime.settings.db_path, item_id)
    item = await get_faq(runtime.settings.db_path, item_id)
    await callback.answer('Готово')
    await callback.message.answer('Статус FAQ изменён.', reply_markup=admin_faq_item_menu(item_id, bool(item['is_active'])))


@router.callback_query(F.data.startswith('admin:faq:delete:'))
async def admin_faq_delete(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    item_id = int(callback.data.rsplit(':', 1)[1])
    await delete_faq(runtime.settings.db_path, item_id)
    items = await list_faq(runtime.settings.db_path, active_only=False)
    await callback.answer('Удалено')
    await callback.message.answer('🗑 Вопрос удалён.', reply_markup=admin_faq_menu(items))
