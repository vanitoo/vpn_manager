from __future__ import annotations

import asyncio
import math
import re
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from app.admin_db import add_plan, find_user, has_used_trial, init_admin_tables, mark_trial_used, set_subscription_status, update_plan
from app.config import get_settings
from app.db import add_payment, add_subscription, get_active_subscription, get_plan_by_id, get_stats, init_db, list_plans, seed_plans, upsert_user
from app.keyboards import admin_menu, admin_plan_menu, admin_plans_menu, admin_user_menu, admin_users_menu, after_purchase_menu, main_menu, my_vpn_menu, payment_methods_menu, plan_menu, plans_menu
from app.proxy_manager import ProxyManager
from app.remnawave import RemnawaveClient

router = Router()
settings = None
proxy_manager: ProxyManager | None = None
TRIAL_DAYS = 1
DEFAULT_PLANS = [
 {'slug':'month','title':'1 месяц','description':'Доступ VPN на 30 дней.','duration_days':30,'traffic_gb':0,'price_rub':199,'sort_order':10},
 {'slug':'quarter','title':'3 месяца','description':'Доступ VPN на 90 дней.','duration_days':90,'traffic_gb':0,'price_rub':499,'sort_order':20},
]
class Form(StatesGroup):
 search=State(); plan_field=State(); new_title=State(); new_price=State(); new_days=State()
def stars(v:int)->int:return max(1,math.ceil(v/max(settings.stars_rub_per_star,0.01)))
def admin(x):return bool(x.from_user and x.from_user.id in settings.admin_ids)
async def user(x):return await upsert_user(settings.db_path,telegram_id=x.from_user.id,username=x.from_user.username,full_name=x.from_user.full_name)
async def make_bot()->Bot:
 global proxy_manager
 proxy_manager=ProxyManager.from_env_string(settings.proxy,mode=settings.proxy_mode,healthcheck_url=settings.proxy_healthcheck_url,healthcheck_timeout=settings.proxy_healthcheck_timeout,healthcheck_interval=settings.proxy_healthcheck_interval)
 if proxy_manager.has_proxies:
  await proxy_manager.check_all()
 session=proxy_manager.get_session() or proxy_manager.get_session_sync()
 if session:
  await proxy_manager.start_healthcheck_loop()
  return Bot(settings.bot_token,session=session,default=DefaultBotProperties(parse_mode=ParseMode.HTML))
 return Bot(settings.bot_token,default=DefaultBotProperties(parse_mode=ParseMode.HTML))
async def provision(u:dict,tid:int,p:dict,trial=False):
 a=await RemnawaveClient(settings).create_or_extend_user(telegram_id=tid,username=u.get('username'),duration_days=int(p['duration_days']),traffic_gb=int(p.get('traffic_gb') or 0))
 sid=await add_subscription(settings.db_path,user_id=int(u['id']),telegram_id=tid,plan_id=int(p['id']),duration_days=int(p['duration_days']),traffic_limit_gb=int(p.get('traffic_gb') or 0),remnawave_user_id=a.remnawave_user_id,subscription_url=a.subscription_url)
 if trial:await mark_trial_used(settings.db_path,tid,sid)
 return await get_active_subscription(settings.db_path,telegram_id=tid)
async def home(m:Message):
 await user(m); s=await get_active_subscription(settings.db_path,telegram_id=m.from_user.id); t=not await has_used_trial(settings.db_path,m.from_user.id)
 txt='🛡 <b>VPN</b>\n\nПодключайтесь за минуту.' if not s else f"🛡 <b>VPN</b>\n\n🟢 Доступ активен до <b>{s['expires_at'][:10]}</b>"
 await m.answer(txt,reply_markup=main_menu(active=bool(s),trial_available=t))
@router.message(CommandStart())
async def cstart(m:Message):await home(m)
@router.message(Command('plans'))
async def cplans(m:Message):await m.answer('🛡 <b>Тарифы</b>',reply_markup=plans_menu(await list_plans(settings.db_path,active_only=True)))
@router.message(Command('vpn'))
async def cvpn(m:Message):await showvpn(m)
@router.message(Command('admin'))
async def cadm(m:Message):
 if admin(m):await m.answer('⚙️ <b>Админка</b>',reply_markup=admin_menu())
async def showvpn(m:Message):
 s=await get_active_subscription(settings.db_path,telegram_id=m.from_user.id)
 if not s:return await m.answer('🔑 Нет активного доступа.',reply_markup=main_menu(active=False,trial_available=not await has_used_trial(settings.db_path,m.from_user.id)))
 url=s.get('subscription_url') or ''
 await m.answer(f"🔑 <b>Ваш VPN</b>\n\nДо: <b>{s['expires_at'][:16]}</b>\n\n<code>{url}</code>",reply_markup=my_vpn_menu(subscription_url=url))
@router.callback_query(F.data=='home')
async def bh(c:CallbackQuery):await c.answer();await home(c.message)
@router.callback_query(F.data=='plans')
async def bp(c:CallbackQuery):await c.answer();await c.message.answer('🛡 <b>Тарифы</b>',reply_markup=plans_menu(await list_plans(settings.db_path,active_only=True)))
@router.callback_query(F.data=='my_vpn')
async def bv(c:CallbackQuery):await c.answer();await showvpn(c.message)
@router.callback_query(F.data=='trial')
async def bt(c:CallbackQuery):
 await c.answer();u=await user(c)
 if await has_used_trial(settings.db_path,c.from_user.id):return await c.message.answer('Тестовый доступ уже использован.')
 p=(await list_plans(settings.db_path,active_only=True))[0];p={**p,'duration_days':TRIAL_DAYS};s=await provision(u,c.from_user.id,p,True)
 await c.message.answer('🎁 <b>Тест на 24 часа активирован.</b>\n\n'+f"<code>{s.get('subscription_url') or ''}</code>",reply_markup=after_purchase_menu())
@router.callback_query(F.data=='servers')
async def bs(c:CallbackQuery):
 await c.answer();ok=RemnawaveClient(settings).is_configured
 await c.message.answer('🌍 <b>Серверы</b>\n\n'+('🟢 Панель подключена.' if ok else '🟡 Панель пока не настроена.'))
@router.callback_query(F.data.startswith('plan:'))
async def bplan(c:CallbackQuery):
 await c.answer();p=await get_plan_by_id(settings.db_path,int(c.data.split(':')[1]));await c.message.answer(f"<b>{p['title']}</b>\n\n{p['description']}\n\n📅 {p['duration_days']} дней\n💳 <b>{p['price_rub']} ₽</b> · {stars(int(p['price_rub']))} ⭐",reply_markup=plan_menu(p['id']))
@router.callback_query(F.data.startswith('buy:'))
async def bbuy(c:CallbackQuery):await c.answer();await c.message.answer('Выберите оплату:',reply_markup=payment_methods_menu(int(c.data.split(':')[1]),['stars']))
@router.callback_query(F.data.startswith('pay:stars:'))
async def bstars(c:CallbackQuery,bot:Bot):
 await c.answer();pid=int(c.data.split(':')[2]);p=await get_plan_by_id(settings.db_path,pid)
 await bot.send_invoice(chat_id=c.from_user.id,title=p['title'],description=f"VPN на {p['duration_days']} дней",payload=f'vpn:{pid}',provider_token='',currency='XTR',prices=[LabeledPrice(label=p['title'],amount=stars(int(p['price_rub'])))])
@router.pre_checkout_query()
async def pre(q:PreCheckoutQuery):await q.answer(ok=q.invoice_payload.startswith('vpn:'))
@router.message(F.successful_payment)
async def paid(m:Message):
 pid=int(m.successful_payment.invoice_payload.split(':')[1]);p=await get_plan_by_id(settings.db_path,pid);u=await user(m);s=await provision(u,m.from_user.id,p)
 await add_payment(settings.db_path,provider='stars',provider_payment_id=m.successful_payment.telegram_payment_charge_id,user_id=int(u['id']),telegram_id=m.from_user.id,plan_id=pid,amount_rub=int(p['price_rub']),currency='XTR',status='paid',payload='')
 await m.answer('✅ <b>VPN активирован.</b>\n\n'+f"<code>{s.get('subscription_url') or ''}</code>",reply_markup=after_purchase_menu())
@router.callback_query(F.data.startswith('admin:'))
async def ba(c:CallbackQuery,state:FSMContext):
 if not admin(c):return await c.answer('Нет доступа',show_alert=True)
 await c.answer();x=c.data.split(':');a=x[1]
 if a=='home':return await c.message.answer('⚙️ <b>Админка</b>',reply_markup=admin_menu())
 if a=='stats':
  s=await get_stats(settings.db_path);return await c.message.answer(f"📊 Пользователей: {s['users']}\nАктивных: {s['active_subscriptions']}\nОплат: {s['paid_payments']}",reply_markup=admin_menu())
 if a=='users':return await c.message.answer('👥 <b>Пользователи</b>',reply_markup=admin_users_menu())
 if a=='usersearch':await state.set_state(Form.search);return await c.message.answer('Пришлите Telegram ID или @username.')
 if a=='plans':return await c.message.answer('💰 <b>Тарифы</b>',reply_markup=admin_plans_menu(await list_plans(settings.db_path,active_only=False)))
 if a=='plan' and len(x)==3:
  p=await get_plan_by_id(settings.db_path,int(x[2]));return await c.message.answer(f"<b>{p['title']}</b>\nЦена: {p['price_rub']} ₽\nСрок: {p['duration_days']} дн.",reply_markup=admin_plan_menu(p['id'],bool(p['is_active'])))
 if a=='plantoggle':
  p=await get_plan_by_id(settings.db_path,int(x[2]));await update_plan(settings.db_path,p['id'],'is_active',0 if p['is_active'] else 1);return await c.message.answer('Статус изменён.')
 if a=='planedit':await state.update_data(field=x[2],pid=int(x[3]));await state.set_state(Form.plan_field);return await c.message.answer('Новое значение?')
 if a=='planadd':await state.set_state(Form.new_title);return await c.message.answer('Название тарифа?')
 if a=='servers':return await c.message.answer('🌍 <b>Серверы</b>\n\nСейчас проверяется доступность API Remnawave. Полная нагрузка нод добавляется после подтверждения endpoint панели.')
 if a in {'block','activate'} and len(x)==3:await set_subscription_status(settings.db_path,int(x[2]),'blocked' if a=='block' else 'active');return await c.message.answer('Статус обновлён.')
@router.message(Form.search)
async def search(m:Message,state:FSMContext):
 u=await find_user(settings.db_path,m.text or '');await state.clear()
 if not u:return await m.answer('Не найден.')
 await m.answer(f"👤 <b>{u.get('full_name') or 'Пользователь'}</b>\nID: <code>{u['telegram_id']}</code>\nДо: {u.get('expires_at') or 'нет'}\nОплат: {u.get('paid_count',0)}",reply_markup=admin_user_menu(u['telegram_id'],bool(u.get('expires_at'))))
@router.message(Form.plan_field)
async def pedit(m:Message,state:FSMContext):
 d=await state.get_data();v=m.text or '';f=d['field'];await update_plan(settings.db_path,d['pid'],f,int(v) if f in {'price_rub','duration_days','traffic_gb'} else v);await state.clear();await m.answer('Тариф обновлён.')
@router.message(Form.new_title)
async def nt(m:Message,state:FSMContext):await state.update_data(title=m.text or 'Тариф');await state.set_state(Form.new_price);await m.answer('Цена?')
@router.message(Form.new_price)
async def np(m:Message,state:FSMContext):await state.update_data(price=int(m.text or '0'));await state.set_state(Form.new_days);await m.answer('Дней?')
@router.message(Form.new_days)
async def nd(m:Message,state:FSMContext):
 d=await state.get_data();days=int(m.text or '0');slug=re.sub('[^a-z0-9]+','-',f"plan-{days}-{d['price']}").strip('-');await add_plan(settings.db_path,slug=slug,title=d['title'],description='',duration_days=days,price_rub=d['price']);await state.clear();await m.answer('✅ Тариф создан.')
async def main():
 global settings
 settings=get_settings();Path(settings.log_file).parent.mkdir(parents=True,exist_ok=True);await init_db(settings.db_path);await init_admin_tables(settings.db_path);await seed_plans(settings.db_path,DEFAULT_PLANS)
 bot=await make_bot();dp=Dispatcher(storage=MemoryStorage());dp.include_router(router)
 try:
  if settings.delete_webhook_on_start:await bot.delete_webhook(drop_pending_updates=settings.drop_pending_updates)
  await dp.start_polling(bot)
 finally:
  if proxy_manager:await proxy_manager.close()
  await bot.session.close()
if __name__=='__main__':asyncio.run(main())
