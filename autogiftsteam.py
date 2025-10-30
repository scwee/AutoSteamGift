"""
═══════════════════════════════════════════════════════════════════════════
    AUTO STEAM GIFT SENDER v3.0.0 - Финальная версия
═══════════════════════════════════════════════════════════════════════════
Автоматическая отправка Steam гифтов через API ns.gifts
Команда: /gift_steam
"""

from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from cardinal import Cardinal

from FunPayAPI.updater.events import NewOrderEvent, NewMessageEvent
from FunPayAPI.types import Message
from telebot.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B
from telebot.types import CallbackQuery, Message as TGMessage
import logging
import requests
import json
import os
import re
from datetime import datetime

# ═══════════════════════════════════════════════════════════════════════════
# МЕТАДАННЫЕ
# ═══════════════════════════════════════════════════════════════════════════
NAME = "Auto Steam Gift Sender"
VERSION = "3.0.0"
DESCRIPTION = "Автоматическая отправка Steam гифтов через API ns.gifts"
CREDITS = "Based on auto_steam_points.py"
UUID = "a7f3c8e2-9d4b-4f1a-8e5c-2b9d7f6a3c1e"
SETTINGS_PAGE = False

# ═══════════════════════════════════════════════════════════════════════════
# API КЛИЕНТ
# ═══════════════════════════════════════════════════════════════════════════
API_BASE_URL = "https://api.ns.gifts/api/v1"

class NSGiftsAPIClient:
    """Клиент для работы с NS.Gifts API"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
    
    def get_balance(self) -> float:
        """Получить баланс (GET /api/v1/check_balance)"""
        try:
            url = f"{API_BASE_URL}/check_balance"
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data.get("success"):
                return float(data.get("balance", 0))
            else:
                raise Exception(f"API error: {data.get('error', 'Unknown')}")
        except Exception as e:
            logging.error(f"[SteamGifts] Balance check error: {e}")
            raise
    
    def send_gift(self, steam_link: str, game_name: str, region: str = "ru") -> dict:
        """Отправить Steam гифт"""
        try:
            url = f"{API_BASE_URL}/steam_gift/create_order"
            
            payload = {
                "friendLink": steam_link,
                "sub_id": 0,
                "region": region,
                "giftName": game_name,
                "giftDescription": "Спасибо за покупку!"
            }
            
            response = requests.post(url, json=payload, headers=self.headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if data.get("success"):
                return {"success": True, "data": data}
            else:
                error = data.get("error", "Unknown error")
                raise Exception(f"API error: {error}")
                
        except Exception as e:
            logging.error(f"[SteamGifts] Gift send error: {e}")
            return {"success": False, "error": str(e)}

# ═══════════════════════════════════════════════════════════════════════════
# КОНФИГУРАЦИЯ
# ═══════════════════════════════════════════════════════════════════════════
CONFIG_DIR = "storage/steam_gifts"
CONFIG_PATH = f"{CONFIG_DIR}/config.json"

DEFAULT_CONFIG = {
    "api_key": "",
    "auto_refunds": False,
    "lot_game_mapping": {},
    "templates": {
        "start_message": "Спасибо за оплату!\n\nОтправьте ссылку на ваш Steam профиль:\nhttps://steamcommunity.com/id/ВАШ_ID\nили\nhttps://steamcommunity.com/profiles/76561198XXXXXXXXX",
        "invalid_link": "❌ Неверная ссылка на Steam профиль.\n\nПравильный формат:\n• steamcommunity.com/id/ВАШ_ID\n• steamcommunity.com/profiles/76561198XXXXXXXXX",
        "link_confirmation": "Подтвердите ваш Steam профиль:\n{link}\n\nОтправьте + для подтверждения или - для отмены",
        "purchase_success": "✅ Гифт \"{game_name}\" успешно отправлен!\n\n🎮 Проверьте подарки в Steam\n\nОставьте отзыв 😊",
        "purchase_error": "❌ Ошибка отправки: {error}\n\nОбратитесь к продавцу",
        "insufficient_balance": "⚠️ Недостаточно средств на балансе!\n\nОбратитесь к продавцу"
    },
    "order_history": []
}

# ═══════════════════════════════════════════════════════════════════════════
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# ═══════════════════════════════════════════════════════════════════════════
logger = logging.getLogger("FPC.steamgifts")
bot = None
cardinal = None
api_client = None
config = {}
waiting_for_link = {}
order_history = []

# ═══════════════════════════════════════════════════════════════════════════
# CALLBACK ДАННЫЕ
# ═══════════════════════════════════════════════════════════════════════════
CB_API = "sg_api"
CB_STATS = "sg_stats"
CB_LOTS = "sg_lots"
CB_ADD_LOT = "sg_addlot"
CB_DEL_LOT = "sg_dellot_"
CB_BALANCE = "sg_balance"
CB_TOGGLE_REFUNDS = "sg_refunds"
CB_BACK = "sg_back"

# ═══════════════════════════════════════════════════════════════════════════
# РАБОТА С КОНФИГУРАЦИЕЙ
# ═══════════════════════════════════════════════════════════════════════════
def ensure_config():
    """Загрузить конфигурацию"""
    global config, order_history
    
    if not os.path.exists(CONFIG_DIR):
        os.makedirs(CONFIG_DIR)
    
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
    
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    for key in DEFAULT_CONFIG:
        if key not in config:
            config[key] = DEFAULT_CONFIG[key].copy() if isinstance(DEFAULT_CONFIG[key], dict) else DEFAULT_CONFIG[key]
    
    for key in DEFAULT_CONFIG['templates']:
        if key not in config['templates']:
            config['templates'][key] = DEFAULT_CONFIG['templates'][key]
    
    order_history = config.get('order_history', [])
    return config


def save_config():
    """Сохранить конфигурацию"""
    global config, order_history
    config['order_history'] = order_history
    
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

# ═══════════════════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ═══════════════════════════════════════════════════════════════════════════
def is_valid_link(link: str) -> tuple[bool, str]:
    """Проверка валидности Steam ссылки"""
    pattern = r"https?://steamcommunity\.com/(id|profiles)/[A-Za-z0-9_-]+"
    
    if re.match(pattern, link):
        return True, ""
    
    return False, config['templates']['invalid_link']


def format_template(template_name: str, **kwargs) -> str:
    """Форматировать шаблон сообщения"""
    template = config['templates'].get(template_name, "")
    
    if not template:
        return DEFAULT_CONFIG['templates'].get(template_name, "")
    
    try:
        return template.format(**kwargs)
    except KeyError:
        return template


def get_game_by_lot(lot_id: str) -> tuple[str | None, str | None]:
    """Получить игру и регион по ID лота"""
    lot_data = config.get("lot_game_mapping", {}).get(str(lot_id))
    
    if not lot_data:
        return None, None
    
    if isinstance(lot_data, str):
        return lot_data, "ru"
    
    return lot_data.get("name"), lot_data.get("region", "ru")

# ═══════════════════════════════════════════════════════════════════════════
# ОБРАБОТКА ЗАКАЗОВ FUNPAY
# ═══════════════════════════════════════════════════════════════════════════
def handle_new_order(c, event):
    """Обработка нового заказа"""
    global waiting_for_link
    
    order_id = event.order.id
    order = event.order
    
    logger.info(f"[SteamGifts] New order: {order_id}")
    
    game_name, region = get_game_by_lot(str(order.lot_id))
    
    if not game_name:
        logger.debug(f"[SteamGifts] Lot {order.lot_id} not configured")
        return
    
    try:
        full_order = c.account.get_order(order_id)
    except Exception as e:
        logger.error(f"[SteamGifts] Get order error: {e}")
        return
    
    if hasattr(full_order, 'chat_id'):
        chat_id = full_order.chat_id
    elif hasattr(full_order, 'chat') and hasattr(full_order.chat, 'id'):
        chat_id = full_order.chat.id
    else:
        logger.error(f"[SteamGifts] No chat_id for order {order_id}")
        return
    
    buyer_id = getattr(full_order, 'buyer_id', None)
    
    if buyer_id is None:
        logger.error(f"[SteamGifts] No buyer_id for order {order_id}")
        return
    
    try:
        revenue = full_order.sum
    except AttributeError:
        logger.error(f"[SteamGifts] No sum for order {order_id}")
        return
    
    waiting_for_link[order_id] = {
        "buyer_id": buyer_id,
        "step": "await_link",
        "chat_id": chat_id,
        "game_name": game_name,
        "region": region,
        "order_id": order_id,
        "revenue": revenue
    }
    
    message = format_template('start_message')
    c.account.send_message(chat_id, message)
    
    logger.info(f"[SteamGifts] Waiting for Steam link from buyer {buyer_id}")


def handle_new_message(c, event):
    """Обработка новых сообщений"""
    global waiting_for_link
    
    msg = event.message
    chat_id = getattr(msg, 'chat_id', None)
    text = (getattr(msg, 'content', None) or getattr(msg, 'text', None))
    author_id = getattr(msg, 'author_id', None)
    
    if text is None or chat_id is None or author_id is None:
        return
    
    text = text.replace('\u2061', '').strip()
    
    logger.debug(f"[SteamGifts] Message: {text[:50]}...")
    
    for order_id, data in list(waiting_for_link.items()):
        if data['buyer_id'] == author_id:
            
            if data['step'] == 'await_link':
                link_match = re.search(r'https?://[^\s]+', text)
                
                if not link_match:
                    c.account.send_message(chat_id, format_template('invalid_link'))
                    return
                
                link = link_match.group(0)
                
                ok, reason = is_valid_link(link)
                
                if not ok:
                    c.account.send_message(chat_id, reason)
                    return
                
                data['link'] = link
                data['step'] = 'await_confirm'
                
                c.account.send_message(
                    chat_id,
                    format_template('link_confirmation', link=link)
                )
                return
            
            elif data['step'] == 'await_confirm':
                if text.lower() in ['+', 'да', 'yes', 'confirm']:
                    process_purchase(c, data)
                    return
                
                elif text.lower() in ['-', 'нет', 'no', 'cancel']:
                    data['step'] = 'await_link'
                    c.account.send_message(
                        chat_id,
                        "Отправка отменена. Отправьте новую ссылку."
                    )
                    return
                
                else:
                    c.account.send_message(
                        chat_id,
                        "Отправьте + для подтверждения или - для отмены"
                    )
                    return


def process_purchase(c, data):
    """Обработка покупки гифта"""
    global api_client, order_history
    
    chat_id = data['chat_id']
    link = data['link']
    game_name = data['game_name']
    region = data['region']
    order_id = data['order_id']
    buyer_id = data['buyer_id']
    revenue = data['revenue']
    
    c.account.send_message(chat_id, f"⏳ Отправляем {game_name}...")
    
    try:
        result = api_client.send_gift(link, game_name, region)
        
        if result['success']:
            success_message = format_template(
                'purchase_success',
                game_name=game_name
            )
            c.account.send_message(chat_id, success_message)
            
            order_history.append({
                "order_id": order_id,
                "buyer_id": buyer_id,
                "game_name": game_name,
                "region": region,
                "link": link,
                "revenue": revenue,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
            save_config()
            
            logger.info(f"[SteamGifts] ✅ Gift sent: {game_name} to {link}")
            
        else:
            error_msg = result.get('error', 'Unknown error')
            
            if "Insufficient" in error_msg or "balance" in error_msg.lower():
                c.account.send_message(chat_id, format_template('insufficient_balance'))
                try_refund(c, order_id, "Insufficient balance")
            else:
                c.account.send_message(
                    chat_id,
                    format_template('purchase_error', error=error_msg)
                )
            
            logger.error(f"[SteamGifts] ❌ Gift error: {error_msg}")
    
    except Exception as e:
        error_msg = str(e)
        c.account.send_message(
            chat_id,
            format_template('purchase_error', error=error_msg)
        )
        logger.error(f"[SteamGifts] Exception: {error_msg}")
    
    finally:
        if order_id in waiting_for_link:
            del waiting_for_link[order_id]


def try_refund(c, order_id, reason):
    """Попытка возврата"""
    if not config.get('auto_refunds', False):
        return False
    
    try:
        c.account.refund(order_id)
        logger.info(f"[SteamGifts] Refunded order {order_id}: {reason}")
        return True
    except Exception as e:
        logger.error(f"[SteamGifts] Refund error for {order_id}: {e}")
        return False

# ═══════════════════════════════════════════════════════════════════════════
# TELEGRAM ПАНЕЛЬ
# ═══════════════════════════════════════════════════════════════════════════
def create_main_keyboard():
    """Главная клавиатура"""
    kb = K(row_width=2)
    
    api_status = "✅" if config.get('api_key') else "❌"
    lots_count = len(config.get('lot_game_mapping', {}))
    
    kb.row(
        B(f"🔑 API {api_status}", callback_data=CB_API),
        B("💰 Баланс", callback_data=CB_BALANCE)
    )
    kb.row(
        B(f"🎮 Лоты ({lots_count})", callback_data=CB_LOTS),
        B("📊 Статистика", callback_data=CB_STATS)
    )
    
    refunds = "✅" if config.get('auto_refunds') else "❌"
    kb.add(B(f"💸 Авторефунды {refunds}", callback_data=CB_TOGGLE_REFUNDS))
    
    return kb


def show_main_panel(message_or_call):
    """Показать главную панель"""
    api_key = config.get('api_key', '')
    api_display = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else ("Не указан" if not api_key else api_key)
    
    lots_count = len(config.get('lot_game_mapping', {}))
    orders_count = len(order_history)
    
    text = f"""<b>🎮 Steam Gifts - Панель управления</b>

<b>API ключ:</b> <code>{api_display}</code>
<b>Настроено лотов:</b> {lots_count}
<b>Всего заказов:</b> {orders_count}

<b>Авторефунды:</b> {'✅ Включены' if config.get('auto_refunds') else '❌ Выключены'}
"""
    
    kb = create_main_keyboard()
    
    if isinstance(message_or_call, TGMessage):
        bot.send_message(message_or_call.chat.id, text, parse_mode="HTML", reply_markup=kb)
    else:
        try:
            bot.edit_message_text(
                text,
                message_or_call.message.chat.id,
                message_or_call.message.id,
                parse_mode="HTML",
                reply_markup=kb
            )
        except:
            pass


def handle_api_callback(call: CallbackQuery):
    """Настройка API ключа"""
    msg = bot.send_message(
        call.message.chat.id,
        "🔑 Отправьте ваш API ключ от ns.gifts:"
    )
    bot.register_next_step_handler(msg, process_api_key, call.message.chat.id, call.message.id)


def process_api_key(message: TGMessage, chat_id: int, msg_id: int):
    """Обработка API ключа"""
    global api_client, config
    
    try:
        bot.delete_message(chat_id, message.id - 1)
        bot.delete_message(chat_id, message.id)
    except:
        pass
    
    api_key = message.text.strip()
    config['api_key'] = api_key
    save_config()
    
    try:
        api_client = NSGiftsAPIClient(api_key=api_key)
        balance = api_client.get_balance()
        bot.send_message(chat_id, f"✅ API ключ сохранён!\n\n💰 Баланс: {balance} руб.")
    except Exception as e:
        bot.send_message(chat_id, f"⚠️ API ключ сохранён, но проверка не удалась:\n{str(e)}")
    
    try:
        bot.delete_message(chat_id, msg_id)
    except:
        pass
    
    show_main_panel(message)


def handle_balance_callback(call: CallbackQuery):
    """Проверка баланса"""
    if not api_client:
        bot.answer_callback_query(call.id, "❌ API ключ не настроен!", show_alert=True)
        return
    
    try:
        balance = api_client.get_balance()
        bot.answer_callback_query(call.id, f"💰 Баланс: {balance} руб.")
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ Ошибка: {str(e)}", show_alert=True)


def handle_stats_callback(call: CallbackQuery):
    """Статистика"""
    total_orders = len(order_history)
    
    if total_orders == 0:
        text = "<b>📊 Статистика</b>\n\nНет заказов"
    else:
        total_revenue = sum(order.get('revenue', 0) for order in order_history)
        games = {}
        
        for order in order_history:
            game = order.get('game_name', 'Unknown')
            games[game] = games.get(game, 0) + 1
        
        top_games = sorted(games.items(), key=lambda x: x[1], reverse=True)[:5]
        
        text = f"""<b>📊 Статистика Steam Gifts</b>

<b>Всего заказов:</b> {total_orders}
<b>Общая выручка:</b> {total_revenue:.2f} руб.

<b>🏆 Топ-5 игр:</b>
"""
        for i, (game, count) in enumerate(top_games, 1):
            text += f"{i}. {game} — {count} шт.\n"
    
    kb = K()
    kb.add(B("🔙 Назад", callback_data=CB_BACK))
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.id,
        parse_mode="HTML",
        reply_markup=kb
    )


def handle_lots_callback(call: CallbackQuery):
    """Управление лотами"""
    lot_game_mapping = config.get("lot_game_mapping", {})
    
    if not lot_game_mapping:
        text = "<b>🎮 Управление лотами</b>\n\n📭 Лоты не настроены"
    else:
        text = f"<b>🎮 Управление лотами ({len(lot_game_mapping)})</b>\n\nНажмите для удаления:"
    
    kb = K(row_width=1)
    
    region_emoji = {"ru": "🇷🇺", "ua": "🇺🇦", "kz": "🇰🇿"}
    
    for lot_id, lot_data in lot_game_mapping.items():
        if isinstance(lot_data, str):
            game_name = lot_data
            region = "ru"
        else:
            game_name = lot_data.get("name", "Unknown")
            region = lot_data.get("region", "ru")
        
        flag = region_emoji.get(region, "🌍")
        kb.add(B(f"{flag} {game_name} (ID: {lot_id})", callback_data=f"{CB_DEL_LOT}{lot_id}"))
    
    kb.add(B("➕ Добавить лот", callback_data=CB_ADD_LOT))
    kb.add(B("🔙 Назад", callback_data=CB_BACK))
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.id,
        parse_mode="HTML",
        reply_markup=kb
    )


def handle_add_lot_callback(call: CallbackQuery):
    """Добавление лота - шаг 1"""
    msg = bot.send_message(
        call.message.chat.id,
        "➕ <b>Добавление лота</b>\n\n<b>Шаг 1/3:</b> Введите ID лота",
        parse_mode="HTML"
    )
    bot.register_next_step_handler(msg, process_lot_id, call.message.chat.id, call.message.id)


def process_lot_id(message: TGMessage, chat_id: int, msg_id: int):
    """Шаг 2: Название игры"""
    try:
        bot.delete_message(chat_id, message.id - 1)
    except:
        pass
    
    lot_id = message.text.strip()
    
    if not lot_id.isdigit():
        bot.delete_message(chat_id, message.id)
        bot.send_message(chat_id, "❌ ID лота должен быть числом!")
        return
    
    if lot_id in config.get("lot_game_mapping", {}):
        bot.delete_message(chat_id, message.id)
        bot.send_message(chat_id, f"❌ Лот {lot_id} уже существует!")
        return
    
    bot.delete_message(chat_id, message.id)
    
    msg = bot.send_message(
        chat_id,
        f"➕ <b>Добавление лота</b>\n\n<b>Шаг 2/3:</b> Введите название игры\n\n<b>ID:</b> <code>{lot_id}</code>",
        parse_mode="HTML"
    )
    bot.register_next_step_handler(msg, process_game_name, chat_id, msg_id, lot_id)


def process_game_name(message: TGMessage, chat_id: int, msg_id: int, lot_id: str):
    """Шаг 3: Регион"""
    try:
        bot.delete_message(chat_id, message.id - 1)
    except:
        pass
    
    game_name = message.text.strip()
    
    if not game_name:
        bot.delete_message(chat_id, message.id)
        bot.send_message(chat_id, "❌ Название не может быть пустым!")
        return
    
    bot.delete_message(chat_id, message.id)
    
    if not hasattr(cardinal, '_temp_lot_data'):
        cardinal._temp_lot_data = {}
    cardinal._temp_lot_data[lot_id] = game_name
    
    kb = K(row_width=3)
    kb.row(
        B("🇷🇺 RU", callback_data=f"region_ru_{lot_id}"),
        B("🇺🇦 UA", callback_data=f"region_ua_{lot_id}"),
        B("🇰🇿 KZ", callback_data=f"region_kz_{lot_id}")
    )
    kb.add(B("🔙 Отмена", callback_data=CB_BACK))
    
    bot.send_message(
        chat_id,
        f"➕ <b>Добавление лота</b>\n\n<b>Шаг 3/3:</b> Выберите регион\n\n<b>ID:</b> <code>{lot_id}</code>\n<b>Игра:</b> {game_name}",
        parse_mode="HTML",
        reply_markup=kb
    )


def handle_region_selection(call: CallbackQuery):
    """Финальный шаг - сохранение"""
    parts = call.data.split("_")
    if len(parts) < 3:
        return
    
    region = parts[1]
    lot_id = parts[2]
    
    if not hasattr(cardinal, '_temp_lot_data') or lot_id not in cardinal._temp_lot_data:
        bot.answer_callback_query(call.id, "❌ Ошибка: данные потеряны", show_alert=True)
        return
    
    game_name = cardinal._temp_lot_data.pop(lot_id)
    
    config.setdefault("lot_game_mapping", {})
    config["lot_game_mapping"][lot_id] = {
        "name": game_name,
        "region": region
    }
    save_config()
    
    region_names = {"ru": "🇷🇺 Россия", "ua": "🇺🇦 Украина", "kz": "🇰🇿 Казахстан"}
    
    bot.edit_message_text(
        f"✅ <b>Лот добавлен!</b>\n\n<b>ID:</b> <code>{lot_id}</code>\n<b>Игра:</b> {game_name}\n<b>Регион:</b> {region_names.get(region, region)}",
        call.message.chat.id,
        call.message.id,
        parse_mode="HTML"
    )
    
    bot.answer_callback_query(call.id, "Лот добавлен!")
    
    import time
    import threading
    def show_delayed():
        time.sleep(2)
        show_main_panel(call)
    threading.Thread(target=show_delayed, daemon=True).start()


def handle_delete_lot(call: CallbackQuery):
    """Удаление лота"""
    lot_id = call.data.replace(CB_DEL_LOT, "")
    
    lot_game_mapping = config.get("lot_game_mapping", {})
    
    if lot_id in lot_game_mapping:
        lot_data = lot_game_mapping[lot_id]
        game_name = lot_data.get("name") if isinstance(lot_data, dict) else lot_data
        
        del config["lot_game_mapping"][lot_id]
        save_config()
        
        bot.answer_callback_query(call.id, f"Лот '{game_name}' удалён!")
    
    handle_lots_callback(call)


def handle_toggle_refunds(call: CallbackQuery):
    """Переключение авторефундов"""
    config['auto_refunds'] = not config.get('auto_refunds', False)
    save_config()
    
    status = "включены ✅" if config['auto_refunds'] else "выключены ❌"
    bot.answer_callback_query(call.id, f"Авторефунды {status}")
    
    show_main_panel(call)


def handle_back(call: CallbackQuery):
    """Возврат на главную"""
    show_main_panel(call)


def handle_callback(call: CallbackQuery):
    """Роутер callback'ов"""
    data = call.data
    
    if data == CB_API:
        handle_api_callback(call)
    elif data == CB_BALANCE:
        handle_balance_callback(call)
    elif data == CB_STATS:
        handle_stats_callback(call)
    elif data == CB_LOTS:
        handle_lots_callback(call)
    elif data == CB_ADD_LOT:
        handle_add_lot_callback(call)
    elif data.startswith(CB_DEL_LOT):
        handle_delete_lot(call)
    elif data.startswith("region_"):
        handle_region_selection(call)
    elif data == CB_TOGGLE_REFUNDS:
        handle_toggle_refunds(call)
    elif data == CB_BACK:
        handle_back(call)
    else:
        bot.answer_callback_query(call.id, "Неизвестная команда")


def handle_command(message: TGMessage):
    """Обработка команды /gift_steam"""
    if message.text == "/gift_steam":
        show_main_panel(message)

# ═══════════════════════════════════════════════════════════════════════════
# ИНИЦИАЛИЗАЦИЯ И ЗАВЕРШЕНИЕ
# ═══════════════════════════════════════════════════════════════════════════
def init_commands(c):
    """Инициализация плагина"""
    global bot, cardinal, config, api_client
    
    cardinal = c
    bot = c.telegram.bot
    config = ensure_config()
    
    c.add_telegram_commands(
        UUID,
        [("gift_steam", "Steam Gifts панель", True)]
    )
    
    if config.get('api_key'):
        api_client = NSGiftsAPIClient(api_key=config['api_key'])
        logger.info("[SteamGifts] API client initialized")
    else:
        logger.warning("[SteamGifts] API key not configured")
    
    bot.register_message_handler(handle_command, commands=['gift_steam'])
    bot.register_callback_query_handler(
        handle_callback,
        func=lambda call: call.data.startswith(('sg_', 'region_'))
    )
    
    logger.info(f"[SteamGifts] Plugin v{VERSION} initialized!")


def cleanup(c):
    """Очистка при удалении"""
    global config, order_history, waiting_for_link
    
    try:
        config['order_history'] = order_history
        save_config()
        logger.info(f"[SteamGifts] Config saved. Orders: {len(order_history)}")
    except Exception as e:
        logger.error(f"[SteamGifts] Save error: {e}")
    
    if waiting_for_link:
        logger.warning(f"[SteamGifts] {len(waiting_for_link)} orders still waiting")
        waiting_for_link.clear()
    
    logger.info(f"[SteamGifts] Plugin v{VERSION} stopped")

# ═══════════════════════════════════════════════════════════════════════════
# ПРИВЯЗКА К СОБЫТИЯМ
# ═══════════════════════════════════════════════════════════════════════════
BIND_TO_PRE_INIT = [init_commands]
BIND_TO_NEW_ORDER = [handle_new_order]
BIND_TO_NEW_MESSAGE = [handle_new_message]
BIND_TO_DELETE = cleanup
