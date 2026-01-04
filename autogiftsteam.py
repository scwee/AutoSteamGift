from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import logging
import os
import re
import time
from typing import TYPE_CHECKING

import requests

from FunPayAPI.updater.events import NewOrderEvent, NewMessageEvent
from telebot.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B
from telebot.types import CallbackQuery, Message as TGMessage

if TYPE_CHECKING:
    from cardinal import Cardinal

NAME = "Auto Steam Gift Sender"
VERSION = "3.2"
DESCRIPTION = "Автоматическая отправка Steam гифтов через API ns.gifts с JWT авторизацией"
CREDITS = "@Scwee_xz"
UUID = "a7f3c8e2-9d4b-4f1a-8e5c-2b9d7f6a3c1e"
SETTINGS_PAGE = False

API_BASE_URL = "https://api.ns.gifts/api/v1"

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
        "insufficient_balance": "❌ Недостаточно средств на балансе.\n\nОбратитесь к продавцу",
    },
    "order_history": [],
}

logger = logging.getLogger("FPC.steamgifts")


@dataclass
class TokenCache:
    token: str | None = None
    expiry: float = 0.0


class TokenManager:
    def __init__(self, api_login: str, api_password: str):
        self.api_login = api_login
        self.api_password = api_password
        self.cache = TokenCache()

    def get_token(self) -> str:
        if self.cache.token and time.time() < self.cache.expiry:
            logger.debug("[SteamGifts] Используем кешированный токен")
            return self.cache.token

        logger.info("[SteamGifts] Запрос нового токена для %s", self.api_login)
        payload = {
            "email": self.api_login,
            "password": self.api_password,
        }

        try:
            response = requests.post(
                f"{API_BASE_URL}/get_token",
                json=payload,
                timeout=10,
            )
            response.raise_for_status()

            data = response.json()
            token = (
                data.get("token")
                or data.get("access_token")
                or (data.get("data", {}).get("token") if isinstance(data.get("data"), dict) else None)
            )

            if not token:
                raise Exception(f"Токен не найден в ответе API: {data}")

            self.cache.token = token
            self.cache.expiry = data.get("valid_thru", time.time() + 7200)

            expiry_time = datetime.fromtimestamp(self.cache.expiry).strftime("%Y-%m-%d %H:%M:%S")
            logger.info("[SteamGifts] ✅ Токен получен, действителен до %s", expiry_time)

            return token

        except requests.exceptions.HTTPError as exc:
            if exc.response.status_code == 401:
                raise Exception("Неверный логин или пароль")
            if exc.response.status_code == 403:
                raise Exception("Доступ запрещён")
            raise Exception(f"HTTP {exc.response.status_code}: {exc.response.text}")
        except Exception as exc:
            logger.error("[SteamGifts] Ошибка получения токена: %s", exc)
            raise Exception(f"Не удалось получить токен: {str(exc)}")


class NSGiftsAPIClient:
    """Клиент для работы с NS.Gifts API через JWT авторизацию"""

    def __init__(self, token_manager: TokenManager):
        self.token_manager = token_manager

    def _get_headers(self) -> dict:
        token = self.token_manager.get_token()
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

    def get_balance(self) -> float:
        try:
            url = f"{API_BASE_URL}/check_balance"
            response = requests.get(url, headers=self._get_headers(), timeout=10)
            response.raise_for_status()
            data = response.json()

            if data.get("success"):
                return float(data.get("balance", 0))
            raise Exception(f"API error: {data.get('error', 'Unknown')}")
        except Exception as exc:
            logger.error("[SteamGifts] Balance check error: %s", exc)
            raise

    def send_gift(self, steam_link: str, game_name: str, region: str = "ru") -> dict:
        try:
            url = f"{API_BASE_URL}/steam_gift/create_order"

            payload = {
                "friendLink": steam_link,
                "sub_id": 0,
                "region": region,
                "giftName": game_name,
                "giftDescription": "Спасибо за покупку!",
            }

            response = requests.post(url, json=payload, headers=self._get_headers(), timeout=30)
            response.raise_for_status()
            data = response.json()

            if data.get("success"):
                return {"success": True, "data": data}

            error = data.get("error", "Unknown error")
            raise Exception(f"API error: {error}")

        except Exception as exc:
            logger.error("[SteamGifts] Gift send error: %s", exc)
            return {"success": False, "error": str(exc)}


@dataclass
class ConfigStore:
    config_path: str
    config_dir: str
    defaults: dict
    config: dict = field(default_factory=dict)

    def load(self) -> dict:
        if not os.path.exists(self.config_dir):
            os.makedirs(self.config_dir)

        if not os.path.exists(self.config_path):
            with open(self.config_path, "w", encoding="utf-8") as file:
                json.dump(self.defaults, file, indent=4, ensure_ascii=False)

        with open(self.config_path, "r", encoding="utf-8") as file:
            self.config = json.load(file)

        self._merge_defaults()
        return self.config

    def _merge_defaults(self) -> None:
        for key, value in self.defaults.items():
            if key not in self.config:
                self.config[key] = json.loads(json.dumps(value))
        for key, value in self.defaults.get("templates", {}).items():
            if key not in self.config.setdefault("templates", {}):
                self.config["templates"][key] = value

    def save(self) -> None:
        with open(self.config_path, "w", encoding="utf-8") as file:
            json.dump(self.config, file, indent=4, ensure_ascii=False)

    def format_template(self, template_name: str, **kwargs) -> str:
        template = self.config.get("templates", {}).get(template_name, "")
        if not template:
            template = self.defaults.get("templates", {}).get(template_name, "")
        try:
            return template.format(**kwargs)
        except KeyError:
            return template


class SteamGiftPlugin:
    def __init__(self) -> None:
        self.bot = None
        self.cardinal: Cardinal | None = None
        self.config_store = ConfigStore(CONFIG_PATH, CONFIG_DIR, DEFAULT_CONFIG)
        self.api_client: NSGiftsAPIClient | None = None
        self.waiting_for_link: dict[int, dict] = {}
        self.order_history: list[dict] = []
        self._temp_auth_data: dict[int, dict] = {}
        self._temp_lot_data: dict[str, str] = {}

        self.cb_auth = "sg_auth"
        self.cb_stats = "sg_stats"
        self.cb_lots = "sg_lots"
        self.cb_add_lot = "sg_addlot"
        self.cb_del_lot = "sg_dellot_"
        self.cb_balance = "sg_balance"
        self.cb_toggle_refunds = "sg_refunds"
        self.cb_back = "sg_back"

    @property
    def config(self) -> dict:
        return self.config_store.config

    def init(self, c: Cardinal) -> None:
        self.cardinal = c
        self.bot = c.telegram.bot
        self.config_store.load()
        self.order_history = self.config.get("order_history", [])

        c.add_telegram_commands(
            UUID,
            [("gift_steam", "Steam Gifts панель", True)],
        )

        api_login = self.config.get("api_login")
        api_password = self.config.get("api_password")
        if api_login and api_password:
            self.api_client = NSGiftsAPIClient(TokenManager(api_login, api_password))
            logger.info("[SteamGifts] API client initialized")
        else:
            logger.warning("[SteamGifts] Авторизация не настроена")

        self.bot.register_message_handler(self.handle_command, commands=["gift_steam"])
        self.bot.register_callback_query_handler(
            self.handle_callback,
            func=lambda call: call.data.startswith(("sg_", "region_")),
        )

        logger.info("[SteamGifts] Plugin v%s initialized!", VERSION)

    def shutdown(self) -> None:
        try:
            self.config["order_history"] = self.order_history
            self.config_store.save()
            logger.info("[SteamGifts] Config saved. Orders: %s", len(self.order_history))
        except Exception as exc:
            logger.error("[SteamGifts] Save error: %s", exc)

        if self.waiting_for_link:
            logger.warning("[SteamGifts] %s orders still waiting", len(self.waiting_for_link))
            self.waiting_for_link.clear()

        logger.info("[SteamGifts] Plugin v%s stopped", VERSION)

    def is_valid_link(self, link: str) -> tuple[bool, str]:
        pattern = r"https?://steamcommunity\.com/(id|profiles)/[A-Za-z0-9_-]+"
        if re.match(pattern, link):
            return True, ""
        return False, self.config_store.format_template("invalid_link")

    def get_game_by_lot(self, lot_id: str) -> tuple[str | None, str | None]:
        lot_data = self.config.get("lot_game_mapping", {}).get(str(lot_id))
        if not lot_data:
            return None, None
        if isinstance(lot_data, str):
            return lot_data, "ru"
        return lot_data.get("name"), lot_data.get("region", "ru")

    def handle_new_order(self, c: Cardinal, event: NewOrderEvent) -> None:
        order_id = event.order.id
        order = event.order

        logger.info("[SteamGifts] New order: %s", order_id)

        game_name, region = self.get_game_by_lot(str(order.lot_id))

        if not game_name:
            logger.debug("[SteamGifts] Lot %s not configured", order.lot_id)
            return

        try:
            full_order = c.account.get_order(order_id)
        except Exception as exc:
            logger.error("[SteamGifts] Get order error: %s", exc)
            return

        if hasattr(full_order, "chat_id"):
            chat_id = full_order.chat_id
        elif hasattr(full_order, "chat") and hasattr(full_order.chat, "id"):
            chat_id = full_order.chat.id
        else:
            logger.error("[SteamGifts] No chat_id for order %s", order_id)
            return

        buyer_id = getattr(full_order, "buyer_id", None)

        if buyer_id is None:
            logger.error("[SteamGifts] No buyer_id for order %s", order_id)
            return

        try:
            revenue = full_order.sum
        except AttributeError:
            logger.error("[SteamGifts] No sum for order %s", order_id)
            return

        self.waiting_for_link[order_id] = {
            "buyer_id": buyer_id,
            "step": "await_link",
            "chat_id": chat_id,
            "game_name": game_name,
            "region": region,
            "order_id": order_id,
            "revenue": revenue,
        }

        message = self.config_store.format_template("start_message")
        c.account.send_message(chat_id, message)

        logger.info("[SteamGifts] Waiting for Steam link from buyer %s", buyer_id)

    def handle_new_message(self, c: Cardinal, event: NewMessageEvent) -> None:
        msg = event.message
        chat_id = getattr(msg, "chat_id", None)
        text = getattr(msg, "content", None) or getattr(msg, "text", None)
        author_id = getattr(msg, "author_id", None)

        if text is None or chat_id is None or author_id is None:
            return

        text = text.replace("\u2061", "").strip()

        for order_id, data in list(self.waiting_for_link.items()):
            if data["buyer_id"] != author_id:
                continue

            if data["step"] == "await_link":
                link_match = re.search(r"https?://[^\s]+", text)

                if not link_match:
                    c.account.send_message(chat_id, self.config_store.format_template("invalid_link"))
                    return

                link = link_match.group(0)
                ok, reason = self.is_valid_link(link)

                if not ok:
                    c.account.send_message(chat_id, reason)
                    return

                data["link"] = link
                data["step"] = "await_confirm"

                c.account.send_message(
                    chat_id,
                    self.config_store.format_template("link_confirmation", link=link),
                )
                return

            if data["step"] == "await_confirm":
                if text.lower() in ["+", "да", "yes", "confirm"]:
                    self.process_purchase(c, data)
                    return

                if text.lower() in ["-", "нет", "no", "cancel"]:
                    data["step"] = "await_link"
                    c.account.send_message(chat_id, "Отправка отменена. Отправьте новую ссылку.")
                    return

                c.account.send_message(chat_id, "Отправьте + для подтверждения или - для отмены")
                return

    def process_purchase(self, c: Cardinal, data: dict) -> None:
        if not self.api_client:
            c.account.send_message(
                data["chat_id"],
                self.config_store.format_template("purchase_error", error="API клиент не настроен"),
            )
            return

        chat_id = data["chat_id"]
        link = data["link"]
        game_name = data["game_name"]
        region = data["region"]
        order_id = data["order_id"]
        buyer_id = data["buyer_id"]
        revenue = data["revenue"]

        c.account.send_message(chat_id, f"⏳ Отправляем {game_name}...")

        try:
            result = self.api_client.send_gift(link, game_name, region)

            if result["success"]:
                success_message = self.config_store.format_template(
                    "purchase_success",
                    game_name=game_name,
                )
                c.account.send_message(chat_id, success_message)

                self.order_history.append(
                    {
                        "order_id": order_id,
                        "buyer_id": buyer_id,
                        "game_name": game_name,
                        "region": region,
                        "link": link,
                        "revenue": revenue,
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
                self.config["order_history"] = self.order_history
                self.config_store.save()

                logger.info("[SteamGifts] ✅ Gift sent: %s to %s", game_name, link)

            else:
                error_msg = result.get("error", "Unknown error")

                if "Insufficient" in error_msg or "balance" in error_msg.lower():
                    c.account.send_message(chat_id, self.config_store.format_template("insufficient_balance"))
                    self.try_refund(c, order_id, "Insufficient balance")
                else:
                    c.account.send_message(
                        chat_id,
                        self.config_store.format_template("purchase_error", error=error_msg),
                    )

                logger.error("[SteamGifts] ❌ Gift error: %s", error_msg)

        except Exception as exc:
            error_msg = str(exc)
            c.account.send_message(
                chat_id,
                self.config_store.format_template("purchase_error", error=error_msg),
            )
            logger.error("[SteamGifts] Exception: %s", error_msg)

        finally:
            self.waiting_for_link.pop(order_id, None)

    def try_refund(self, c: Cardinal, order_id: int, reason: str) -> bool:
        if not self.config.get("auto_refunds", False):
            return False

        try:
            c.account.refund(order_id)
            logger.info("[SteamGifts] Refunded order %s: %s", order_id, reason)
            return True
        except Exception as exc:
            logger.error("[SteamGifts] Refund error for %s: %s", order_id, exc)
            return False

    def create_main_keyboard(self) -> K:
        kb = K(row_width=2)

        auth_status = "✅" if self.config.get("api_login") and self.config.get("api_password") else "❌"
        lots_count = len(self.config.get("lot_game_mapping", {}))

        kb.row(
            B(f"🔐 Авторизация {auth_status}", callback_data=self.cb_auth),
            B("💰 Баланс", callback_data=self.cb_balance),
        )
        kb.row(
            B(f"🎮 Лоты ({lots_count})", callback_data=self.cb_lots),
            B("📊 Статистика", callback_data=self.cb_stats),
        )

        refunds = "✅" if self.config.get("auto_refunds") else "❌"
        kb.add(B(f"💸 Авторефунды {refunds}", callback_data=self.cb_toggle_refunds))

        return kb

    def show_main_panel(self, message_or_call: TGMessage | CallbackQuery) -> None:
        api_login = self.config.get("api_login", "")
        login_display = (
            f"{api_login[:4]}...{api_login[-4:]}" if len(api_login) > 8 else ("Не указан" if not api_login else api_login)
        )

        lots_count = len(self.config.get("lot_game_mapping", {}))
        orders_count = len(self.order_history)

        text = f"""<b>🎮 Steam Gifts - Панель управления</b>

<b>Логин:</b> <code>{login_display}</code>
<b>Настроено лотов:</b> {lots_count}
<b>Всего заказов:</b> {orders_count}

<b>Авторефунды:</b> {'✅ Включены' if self.config.get('auto_refunds') else '❌ Выключеы'}
"""

        kb = self.create_main_keyboard()

        if isinstance(message_or_call, TGMessage):
            self.bot.send_message(message_or_call.chat.id, text, parse_mode="HTML", reply_markup=kb)
            return

        try:
            self.bot.edit_message_text(
                text,
                message_or_call.message.chat.id,
                message_or_call.message.id,
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception:
            pass

    def handle_auth_callback(self, call: CallbackQuery) -> None:
        msg = self.bot.send_message(
            call.message.chat.id,
            "🔐 <b>Авторизация ns.gifts</b>\n\n<b>Шаг 1/2:</b> Введите ваш email:",
            parse_mode="HTML",
        )
        self.bot.register_next_step_handler(msg, self.process_login, call.message.chat.id, call.message.id)

    def process_login(self, message: TGMessage, chat_id: int, msg_id: int) -> None:
        try:
            self.bot.delete_message(chat_id, message.id - 1)
            self.bot.delete_message(chat_id, message.id)
        except Exception:
            pass

        login = message.text.strip()

        if not login or "@" not in login:
            self.bot.send_message(chat_id, "❌ Неверный формат email!")
            self.show_main_panel(message)
            return

        self._temp_auth_data[chat_id] = {"login": login}

        msg = self.bot.send_message(
            chat_id,
            (
                "🔐 <b>Авторизация ns.gifts</b>\n\n"
                "<b>Шаг 2/2:</b> Введите ваш пароль:\n\n"
                f"<b>Email:</b> <code>{login}</code>"
            ),
            parse_mode="HTML",
        )
        self.bot.register_next_step_handler(msg, self.process_password, chat_id, msg_id)

    def process_password(self, message: TGMessage, chat_id: int, msg_id: int) -> None:
        try:
            self.bot.delete_message(chat_id, message.id - 1)
            self.bot.delete_message(chat_id, message.id)
        except Exception:
            pass

        password = message.text.strip()

        if not password:
            self.bot.send_message(chat_id, "❌ Пароль не может быть пустым!")
            self.show_main_panel(message)
            return

        if chat_id not in self._temp_auth_data:
            self.bot.send_message(chat_id, "❌ Ошибка: данные авторизации потеряны")
            self.show_main_panel(message)
            return

        login = self._temp_auth_data.pop(chat_id)["login"]

        self.config["api_login"] = login
        self.config["api_password"] = password
        self.config_store.save()

        try:
            self.api_client = NSGiftsAPIClient(TokenManager(login, password))
            balance = self.api_client.get_balance()

            self.bot.send_message(
                chat_id,
                f"✅ <b>Авторизация успешна!</b>\n\n💰 Баланс: {balance} руб.",
                parse_mode="HTML",
            )
            logger.info("[SteamGifts] Авторизация успешна для %s", login)

        except Exception as exc:
            self.bot.send_message(
                chat_id,
                (
                    "⚠️ <b>Авторизация не удалась</b>\n\n"
                    f"{str(exc)}\n\nДанные сохранены, попробуйте позже."
                ),
                parse_mode="HTML",
            )
            logger.error("[SteamGifts] Ошибка авторизации: %s", exc)

        try:
            self.bot.delete_message(chat_id, msg_id)
        except Exception:
            pass

        self.show_main_panel(message)

    def handle_balance_callback(self, call: CallbackQuery) -> None:
        if not self.api_client:
            self.bot.answer_callback_query(call.id, "❌ Сначала авторизуйтесь!", show_alert=True)
            return

        try:
            balance = self.api_client.get_balance()
            self.bot.answer_callback_query(call.id, f"💰 Баланс: {balance} руб.")
        except Exception as exc:
            self.bot.answer_callback_query(call.id, f"❌ Ошибка: {str(exc)}", show_alert=True)

    def handle_stats_callback(self, call: CallbackQuery) -> None:
        total_orders = len(self.order_history)

        if total_orders == 0:
            text = "<b>📊 Статистика</b>\n\nНет заказов"
        else:
            total_revenue = sum(order.get("revenue", 0) for order in self.order_history)
            games: dict[str, int] = {}

            for order in self.order_history:
                game = order.get("game_name", "Unknown")
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
        kb.add(B("🔙 Назад", callback_data=self.cb_back))

        self.bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.id,
            parse_mode="HTML",
            reply_markup=kb,
        )

    def handle_lots_callback(self, call: CallbackQuery) -> None:
        lot_game_mapping = self.config.get("lot_game_mapping", {})

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
            kb.add(B(f"{flag} {game_name} (ID: {lot_id})", callback_data=f"{self.cb_del_lot}{lot_id}"))

        kb.add(B("➕ Добавить лот", callback_data=self.cb_add_lot))
        kb.add(B("🔙 Назад", callback_data=self.cb_back))

        self.bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.id,
            parse_mode="HTML",
            reply_markup=kb,
        )

    def handle_add_lot_callback(self, call: CallbackQuery) -> None:
        msg = self.bot.send_message(
            call.message.chat.id,
            "➕ <b>Добавление лота</b>\n\n<b>Шаг 1/3:</b> Введите ID лота",
            parse_mode="HTML",
        )
        self.bot.register_next_step_handler(msg, self.process_lot_id, call.message.chat.id, call.message.id)

    def process_lot_id(self, message: TGMessage, chat_id: int, msg_id: int) -> None:
        try:
            self.bot.delete_message(chat_id, message.id - 1)
        except Exception:
            pass

        lot_id = message.text.strip()

        if not lot_id.isdigit():
            self.bot.delete_message(chat_id, message.id)
            self.bot.send_message(chat_id, "❌ ID лота должен быть числом!")
            return

        if lot_id in self.config.get("lot_game_mapping", {}):
            self.bot.delete_message(chat_id, message.id)
            self.bot.send_message(chat_id, f"❌ Лот {lot_id} уже существует!")
            return

        self.bot.delete_message(chat_id, message.id)

        msg = self.bot.send_message(
            chat_id,
            (
                "➕ <b>Добавление лота</b>\n\n"
                "<b>Шаг 2/3:</b> Введите название игры\n\n"
                f"<b>ID:</b> <code>{lot_id}</code>"
            ),
            parse_mode="HTML",
        )
        self.bot.register_next_step_handler(msg, self.process_game_name, chat_id, msg_id, lot_id)

    def process_game_name(self, message: TGMessage, chat_id: int, msg_id: int, lot_id: str) -> None:
        try:
            self.bot.delete_message(chat_id, message.id - 1)
        except Exception:
            pass

        game_name = message.text.strip()

        if not game_name:
            self.bot.delete_message(chat_id, message.id)
            self.bot.send_message(chat_id, "❌ Название не может быть пустым!")
            return

        self.bot.delete_message(chat_id, message.id)

        self._temp_lot_data[lot_id] = game_name

        kb = K(row_width=3)
        kb.row(
            B("🇷🇺 RU", callback_data=f"region_ru_{lot_id}"),
            B("🇺🇦 UA", callback_data=f"region_ua_{lot_id}"),
            B("🇰🇿 KZ", callback_data=f"region_kz_{lot_id}"),
        )
        kb.add(B("🔙 Отмена", callback_data=self.cb_back))

        self.bot.send_message(
            chat_id,
            (
                "➕ <b>Добавление лота</b>\n\n"
                "<b>Шаг 3/3:</b> Выберите регион\n\n"
                f"<b>ID:</b> <code>{lot_id}</code>\n"
                f"<b>Игра:</b> {game_name}"
            ),
            parse_mode="HTML",
            reply_markup=kb,
        )

    def handle_region_selection(self, call: CallbackQuery) -> None:
        parts = call.data.split("_")
        if len(parts) < 3:
            return

        region = parts[1]
        lot_id = parts[2]

        if lot_id not in self._temp_lot_data:
            self.bot.answer_callback_query(call.id, "❌ Ошибка: данные потеряны", show_alert=True)
            return

        game_name = self._temp_lot_data.pop(lot_id)

        self.config.setdefault("lot_game_mapping", {})
        self.config["lot_game_mapping"][lot_id] = {
            "name": game_name,
            "region": region,
        }
        self.config_store.save()

        region_names = {"ru": "🇷🇺 Россия", "ua": "🇺🇦 Украина", "kz": "🇰🇿 Казахстан"}

        self.bot.edit_message_text(
            (
                "✅ <b>Лот добавлен!</b>\n\n"
                f"<b>ID:</b> <code>{lot_id}</code>\n"
                f"<b>Игра:</b> {game_name}\n"
                f"<b>Регион:</b> {region_names.get(region, region)}"
            ),
            call.message.chat.id,
            call.message.id,
            parse_mode="HTML",
        )

        self.bot.answer_callback_query(call.id, "Лот добавлен!")

        import threading

        def show_delayed() -> None:
            time.sleep(2)
            self.show_main_panel(call)

        threading.Thread(target=show_delayed, daemon=True).start()

    def handle_delete_lot(self, call: CallbackQuery) -> None:
        lot_id = call.data.replace(self.cb_del_lot, "")

        lot_game_mapping = self.config.get("lot_game_mapping", {})

        if lot_id in lot_game_mapping:
            lot_data = lot_game_mapping[lot_id]
            game_name = lot_data.get("name") if isinstance(lot_data, dict) else lot_data

            del self.config["lot_game_mapping"][lot_id]
            self.config_store.save()

            self.bot.answer_callback_query(call.id, f"Лот '{game_name}' удалён!")

        self.handle_lots_callback(call)

    def handle_toggle_refunds(self, call: CallbackQuery) -> None:
        self.config["auto_refunds"] = not self.config.get("auto_refunds", False)
        self.config_store.save()

        status = "включены ✅" if self.config["auto_refunds"] else "выключены ❌"
        self.bot.answer_callback_query(call.id, f"Авторефунды {status}")

        self.show_main_panel(call)

    def handle_back(self, call: CallbackQuery) -> None:
        self.show_main_panel(call)

    def handle_callback(self, call: CallbackQuery) -> None:
        data = call.data

        if data == self.cb_auth:
            self.handle_auth_callback(call)
        elif data == self.cb_balance:
            self.handle_balance_callback(call)
        elif data == self.cb_stats:
            self.handle_stats_callback(call)
        elif data == self.cb_lots:
            self.handle_lots_callback(call)
        elif data == self.cb_add_lot:
            self.handle_add_lot_callback(call)
        elif data.startswith(self.cb_del_lot):
            self.handle_delete_lot(call)
        elif data.startswith("region_"):
            self.handle_region_selection(call)
        elif data == self.cb_toggle_refunds:
            self.handle_toggle_refunds(call)
        elif data == self.cb_back:
            self.handle_back(call)
        else:
            self.bot.answer_callback_query(call.id, "Неизвестная команда")

    def handle_command(self, message: TGMessage) -> None:
        if message.text == "/gift_steam":
            self.show_main_panel(message)


plugin = SteamGiftPlugin()


# ═══════════════════════════════════════════════════════════════════════════
# ИНИЦИАЛИЗАЦИЯ
# ═══════════════════════════════════════════════════════════════════════════

def init_commands(c: Cardinal) -> None:
    plugin.init(c)


def handle_new_order(c: Cardinal, event: NewOrderEvent) -> None:
    plugin.handle_new_order(c, event)


def handle_new_message(c: Cardinal, event: NewMessageEvent) -> None:
    plugin.handle_new_message(c, event)


def cleanup(c: Cardinal) -> None:
    plugin.shutdown()


BIND_TO_PRE_INIT = [init_commands]
BIND_TO_NEW_ORDER = [handle_new_order]
BIND_TO_NEW_MESSAGE = [handle_new_message]
BIND_TO_DELETE = cleanup
