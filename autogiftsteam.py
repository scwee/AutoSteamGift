
   """
═══════════════════════════════════════════════════════════════════════════
    AUTO STEAM GIFT SENDER v3.1.0 - ФИНАЛЬНАЯ ВЕРСИЯ
═══════════════════════════════════════════════════════════════════════════
Автоматическая отправка Steam гифтов через API ns.gifts
JWT авторизация: email + password → токен с автообновлением
Команда: /gift_steam
"""

from __future__ import annotations
from typing import TYPE_CHECKING
from FunPayAPI.updater.events import NewOrderEvent, NewMessageEvent
from FunPayAPI.types import Message
from telebot.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B
from telebot.types import CallbackQuery, Message as TGMessage
import logging
import requests
import json
import os
import re
import time
from datetime import datetime

if TYPE_CHECKING:
    from cardinal import Cardinal

# ═══════════════════════════════════════════════════════════════════════════
# МЕТАДАННЫЕ
# ═══════════════════════════════════════════════════════════════════════════
NAME = "Auto Steam Gift Sender"
VERSION = "3.1"
DESCRIPTION = "Автоматическая отправка Steam гифтов через API ns.gifts с JWT авторизацией"
CREDITS = "@Scwee_xz"
UUID = "a7f3c8e2-9d4b-4f1a-8e5c-2b9d7f6a3c1e"
SETTINGS_PAGE = False

# ═══════════════════════════════════════════════════════════════════════════
# API КЛИЕНТ С JWT АВТОРИЗАЦИЕЙ
# ═══════════════════════════════════════════════════════════════════════════
API_BASE_URL = "https://api.ns.gifts/api/v1"

TOKEN_DATA = {
    "token": None,
    "expiry": 0
}

def get_token(api_login: str, api_password: str) -> str:
    """
    Получить JWT токен через email/password
    Кеширует токен до истечения срока действия (valid_thru)
    Автоматически обновляет токен при истечении
    
    API: POST /api/v1/get_token
    Body: {"email": "...", "password": "..."}
    Response: {"token": "...", "valid_thru": timestamp}
    """
    global TOKEN_DATA
    
    # Если токен ещё действителен - возвращаем
    if TOKEN_DATA["token"] and time.time() < TOKEN_DATA["expiry"]:
        logger.debug(f"[SteamGifts] Используем кешированный токен")
        return TOKEN_DATA["token"]
    
    # Запрашиваем новый токен
    logger.info(f"[SteamGifts] Запрос нового токена для {api_login}")
    
    payload = {
        "email": api_login,
        "password": api_password
    }
    
    try:
        response = requests.post(
            f"{API_BASE_URL}/get_token",
            json=payload,
            timeout=10
        )
        response.raise_for_status()
        
        data = response.json()
        
        # Поддержка разных форматов ответа
        token = (
            data.get("token") or 
            data.get("access_token") or 
            (data.get("data", {}).get("token") if isinstance(data.get("data"), dict) else None)
        )
        
        if not token:
            raise Exception(f"Токен не найден в ответе API: {data}")
        
        # Сохраняем токен и время истечения
        TOKEN_DATA["token"] = token
        TOKEN_DATA["expiry"] = data.get("valid_thru", time.time() + 7200)  # 2 часа по умолчанию
        
        expiry_time = datetime.fromtimestamp(TOKEN_DATA["expiry"]).strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"[SteamGifts] ✅ Токен получен, действителен до {expiry_time}")
        
        return TOKEN_DATA["token"]
        
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            raise Exception("Неверный логин или пароль")
        elif e.response.status_code == 403:
            raise Exception("Доступ запрещён")
        else:
            raise Exception(f"HTTP {e.response.status_code}: {e.response.text}")
    except Exception as e:
        logger.error(f"[SteamGifts] Ошибка получения токена: {e}")
        raise Exception(f"Не удалось получить токен: {str(e)}")


class NSGiftsAPIClient:
    """Клиент для работы с NS.Gifts API через JWT авторизацию"""
    
    def __init__(self, api_login: str, api_password: str):
        self.api_login = api_login
        self.api_password = api_password
    
    def _get_headers(self) -> dict:
        """Получить заголовки с актуальным JWT токеном (автообновление)"""
        token = get_token(self.api_login, self.api_password)
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
    
    def get_balance(self) -> float:
        """Получить баланс (GET /api/v1/check_balance)"""
        try:
            url = f"{API_BASE_URL}/check_balance"
            response = requests.get(url, headers=self._get_headers(), timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data.get("success"):
                return float(data.get("balance", 0))
            else:
                raise Exception(f"API error: {data.get('error', 'Unknown')}")
        except Exception as e:
            logger.error(f"[SteamGifts] Balance check error: {e}")
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
            
            response = requests.post(url, json=payload, headers=self._get_headers(), timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if data.get("success"):
                return {"success": True, "data": data}
            else:
                error = data.get("error", "Unknown error")
                raise Exception(f"API error: {error}")
                
        except Exception as e:
            logger.error(f"[SteamGifts] Gift send error: {e}")
            return {"success": False, "error": str(e)}

# ═══════════════════════════════════════════════════════════════════════════
# КОНФИГУРАЦИЯ
# ═══════════════════════════════════════════════════════════════════════════
CONFIG_DIR = "storage/steam_gifts"
CONFIG_PATH = f"{CONFIG_DIR}/config.json"

DEFAULT_CONFIG = {
    "api_login": "",
    "api_password": "",
    "auto_refunds": False,
    "lot_game_mapping": {},
    "templates": {
        "start_message": "Спасибо за оплату!\n\nОтправьте ссылку на ваш Steam профиль:\nhttps://steamcommunity.com/id/ВАШ_ID\nили\nhttps://steamcommunity.com/profiles/76561198XXXXXXXXX",
        "invalid_link": "❌ Неверная ссылка на Steam профиль.\n\nПравильный формат:\n• steamcommunity.com/id/ВАШ_ID\n• steamcommunity.com/profiles/76561198XXXXXXXXX",
        "link_confirmation": "Подтвердите ваш Steam профиль:\n{link}\n\nОтправьте + для подтверждения или - для отмены",
        "purchase_success": "✅ Гифт \"{game_name}\" успешно отправлен!\n\n🎮 Проверьте подарки в Steam\n\nОставьте отзыв 😊",
        "purchase_error": "❌ Ошибка отправки: {error}\n\nОбратитесь к продавцу",
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

CB_AUTH = "sg_auth"
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
    global config, order_history
    config['order_history'] = order_history
    
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

# ═══════════════════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ═══════════════════════════════════════════════════════════════════════════
def is_valid_link(link: str) -> tuple[bool, str]:
    pattern = r"https?://steamcommunity\.com/(id|profiles)/[A-Za-z0-9_-]+"
    if re.match(pattern, link):
        return True, ""
    return False, config['templates']['invalid_link']


def format_template(template_name: str, **kwargs) -> str:
    template = config['templates'].get(template_name, "")
    if not template:
        return DEFAULT_CONFIG['templates'].get(template_name, "")
    try:
        return template.format(**kwargs)
    except KeyError:
        return template


def get_game_by_lot(lot_id: str) -> tuple[str | None, str | None]:
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
    global waiting_for_link
    
    msg = event.message
    chat_id = getattr(msg, 'chat_id', None)
    text = (getattr(msg, 'content', None) or getattr(msg, 'text', None))
    author_id = getattr(msg, 'author_id', None)
    
    if text is None or chat_id is None or author_id is None:
        return
    
    text = text.replace('\u2061', '').strip()
    
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
    kb = K(row_width=2)
    
    auth_status = "✅" if config.get('api_login') and config.get('api_password') else "❌"
    lots_count = len(config.get('lot_game_mapping', {}))
    
    kb.row(
        B(f"🔐 Авторизация {auth_status}", callback_data=CB_AUTH),
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
    api_login = config.get('api_login', '')
    login_display = f"{api_login[:4]}...{api_login[-4:]}" if len(api_login) > 8 else ("Не указан" if not api_login else api_login)
    
    lots_count = len(config.get('lot_game_mapping', {}))
    orders_count = len(order_history)
    
    text = f"""<b>🎮 Steam Gifts - Панель управления</b>

<b>Логин:</b> <code>{login_display}</code>
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


def handle_auth_callback(call: CallbackQuery):
    msg = bot.send_message(
        call.message.chat.id,
        "🔐 <b>Авторизация ns.gifts</b>\n\n<b>Шаг 1/2:</b> Введите ваш email:",
        parse_mode="HTML"
    )
    bot.register_next_step_handler(msg, process_login, call.message.chat.id, call.message.id)


def process_login(message: TGMessage, chat_id: int, msg_id: int):
    try:
        bot.delete_message(chat_id, message.id - 1)
        bot.delete_message(chat_id, message.id)
    except:
        pass
    
    login = message.text.strip()
    
    if not login or '@' not in login:
        bot.send_message(chat_id, "❌ Неверный формат email!")
        show_main_panel(message)
        return
    
    if not hasattr(cardinal, '_temp_auth_data'):
        cardinal._temp_auth_data = {}
    cardinal._temp_auth_data[chat_id] = {"login": login}
    
    msg = bot.send_message(
        chat_id,
        f"🔐 <b>Авторизация ns.gifts</b>\n\n<b>Шаг 2/2:</b> Введите ваш пароль:\n\n<b>Email:</b> <code>{login}</code>",
        parse_mode="HTML"
    )
    bot.register_next_step_handler(msg, process_password, chat_id, msg_id)


def process_password(message: TGMessage, chat_id: int, msg_id: int):
    global api_client, config
    
    try:
        bot.delete_message(chat_id, message.id - 1)
        bot.delete_message(chat_id, message.id)
    except:
        pass
    
    password = message.text.strip()
    
    if not password:
        bot.send_message(chat_id, "❌ Пароль не может быть пустым!")
        show_main_panel(message)
        return
    
    if not hasattr(cardinal, '_temp_auth_data') or chat_id not in cardinal._temp_auth_data:
        bot.send_message(chat_id, "❌ Ошибка: данные авторизации потеряны")
        show_main_panel(message)
        return
    
    login = cardinal._temp_auth_data[chat_id]["login"]
    del cardinal._temp_auth_data[chat_id]
    
    config['api_login'] = login
    config['api_password'] = password
    save_config()
    
    try:
        api_client = NSGiftsAPIClient(api_login=login, api_password=password)
        balance = api_client.get_balance()
        
        bot.send_message(
            chat_id,
            f"✅ <b>Авторизация успешна!</b>\n\n💰 Баланс: {balance} руб.",
            parse_mode="HTML"
        )
        logger.info(f"[SteamGifts] Авторизация успешна для {login}")
        
    except Exception as e:
        bot.send_message(
            chat_id,
            f"⚠️ <b>Авторизация не удалась</b>\n\n{str(e)}\n\nДанные сохранены, попробуйте позже.",
            parse_mode="HTML"
        )
        logger.error(f"[SteamGifts] Ошибка авторизации: {e}")
    
    try:
        bot.delete_message(chat_id, msg_id)
    except:
        pass
    
    show_main_panel(message)


def handle_balance_callback(call: CallbackQuery):
    if not api_client:
        bot.answer_callback_query(call.id, "❌ Сначала авторизуйтесь!", show_alert=True)
        return
    
    try:
        balance = api_client.get_balance()
        bot.answer_callback_query(call.id, f"💰 Баланс: {balance} руб.")
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ Ошибка: {str(e)}", show_alert=True)


def handle_stats_callback(call: CallbackQuery):
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
    msg = bot.send_message(
        call.message.chat.id,
        "➕ <b>Добавление лота</b>\n\n<b>Шаг 1/3:</b> Введите ID лота",
        parse_mode="HTML"
    )
    bot.register_next_step_handler(msg, process_lot_id, call.message.chat.id, call.message.id)


def process_lot_id(message: TGMessage, chat_id: int, msg_id: int):
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
    
    import threading
    def show_delayed():
        time.sleep(2)
        show_main_panel(call)
    threading.Thread(target=show_delayed, daemon=True).start()


def handle_delete_lot(call: CallbackQuery):
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
    config['auto_refunds'] = not config.get('auto_refunds', False)
    save_config()
    
    status = "включены ✅" if config['auto_refunds'] else "выключены ❌"
    bot.answer_callback_query(call.id, f"Авторефунды {status}")
    
    show_main_panel(call)


def handle_back(call: CallbackQuery):
    show_main_panel(call)


def handle_callback(call: CallbackQuery):
    data = call.data
    
    if data == CB_AUTH:
        handle_auth_callback(call)
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
    if message.text == "/gift_steam":
        show_main_panel(message)

# ═══════════════════════════════════════════════════════════════════════════
# ИНИЦИАЛИЗАЦИЯ
# ═══════════════════════════════════════════════════════════════════════════
def init_commands(c):
    global bot, cardinal, config, api_client
    
    cardinal = c
    bot = c.telegram.bot
    config = ensure_config()
    
    c.add_telegram_commands(
        UUID,
        [("gift_steam", "Steam Gifts панель", True)]
    )
    
    if config.get('api_login') and config.get('api_password'):
        api_client = NSGiftsAPIClient(
            api_login=config['api_login'],
            api_password=config['api_password']
        )
        logger.info("[SteamGifts] API client initialized")
    else:
        logger.warning("[SteamGifts] Авторизация не настроена")
    
    bot.register_message_handler(handle_command, commands=['gift_steam'])
    bot.register_callback_query_handler(
        handle_callback,
        func=lambda call: call.data.startswith(('sg_', 'region_'))
    )
    
    logger.info(f"[SteamGifts] Plugin v{VERSION} initialized!")


def cleanup(c):
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


BIND_TO_PRE_INIT = [init_commands]
BIND_TO_NEW_ORDER = [handle_new_order]
BIND_TO_NEW_MESSAGE = [handle_new_message]
BIND_TO_DELETE = cleanup
