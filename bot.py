from __future__ import annotations

# ==========================================================
# 0) IMPORTS
# ==========================================================
import json
import logging
import os
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from db import init_db, create_booking


# ==========================================================
# 1) LOGGING
# ==========================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("spalnik_bot")


# ==========================================================
# 2) PATHS + ENV (config.env рядом с bot.py)
# ==========================================================
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / "config.env"

ASSETS_DIR = BASE_DIR / "assets"
LOGO_PATH = ASSETS_DIR / "logo.jpg"
MENU_FILE = ASSETS_DIR / "menu.pdf"
EVENTS_FILE = ASSETS_DIR / "events.pdf"  # может не быть


def load_env_file(path: Path) -> None:
    """Загрузка KEY=VALUE из config.env без сторонних библиотек."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


load_env_file(ENV_PATH)

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "❌ Не найден BOT_TOKEN.\n"
        f"Создай файл {ENV_PATH.name} рядом с bot.py и вставь:\n"
        "BOT_TOKEN=123456:ABCDEF...\n"
    )

# Ссылка на мини-апп (ОБЯЗАТЕЛЬНО HTTPS)
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()
if not WEBAPP_URL:
    logger.warning("⚠️ WEBAPP_URL пустой. Кнопка мини-аппа не появится.")


# ==========================================================
# 3) LINKS + NOTIFICATIONS
# ==========================================================
YANDEX_REVIEWS_URL = "https://yandex.ru/maps/org/spalnik/104151350821/reviews/?ll=37.715866%2C55.532722&z=16"
GIS2_REVIEWS_URL = "https://2gis.ru/moscow/firm/70000001053915498"
YANDEX_FOOD_URL = "https://eda.yandex.ru/r/spal_nik?placeSlug=spalnik"
TG_CHANNEL_URL = "https://t.me/SpalnikBar"

TIP_URL = "https://netmonet.co/qr/244255/tip?o=4"

# ВАЖНО: сюда chat_id группы заказов
def parse_chat_ids(raw: str) -> list[int]:
    ids: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            logger.warning("⚠️ Некорректный chat_id в NOTIFY_CHAT_IDS: %s", part)
    return ids


ENV_NOTIFY_CHAT_IDS = os.getenv("NOTIFY_CHAT_IDS", "").strip()
NOTIFY_CHAT_IDS: list[int] = parse_chat_ids(ENV_NOTIFY_CHAT_IDS) if ENV_NOTIFY_CHAT_IDS else [-5102802574]


# ==========================================================
# 4) BOOKING STATES
# ==========================================================
B_DATE, B_TIME, B_GUESTS, B_NAME, B_PHONE, B_COMMENT = range(6)


# ==========================================================
# 5) UI
# ==========================================================
HOME_TEXT = "🍻 *Спальник Бар*\n\nВыбирай действие 👇"


def main_keyboard() -> InlineKeyboardMarkup:
    tips_btn = InlineKeyboardButton("💜 Чаевые", url=TIP_URL)

    rows = []

    # ✅ ВАЖНО: мини-апп должен открываться как WebApp, иначе web_app_data не придёт
    if WEBAPP_URL:
        rows.append([InlineKeyboardButton("Меню/Забронировать стол", web_app=WebAppInfo(url=WEBAPP_URL))])

    rows += [
        [
            InlineKeyboardButton("⭐ (Яндекс)", url=YANDEX_REVIEWS_URL),
            InlineKeyboardButton("⭐ (2ГИС)", url=GIS2_REVIEWS_URL),
        ],
        [
            InlineKeyboardButton("📣 Наш канал", url=TG_CHANNEL_URL),
            InlineKeyboardButton("🛵 Яндекс Еда", url=YANDEX_FOOD_URL),
        ],
        [InlineKeyboardButton("🎉 События", callback_data="open_events")],
        [tips_btn],
    ]

    return InlineKeyboardMarkup(rows)


def back_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Главное меню", callback_data="go_home")]])


# ==========================================================
# 6) HELPERS
# ==========================================================
async def show_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id

    old_home = context.chat_data.get("home_message_id")
    if isinstance(old_home, int):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=old_home)
        except Exception:
            pass

    if LOGO_PATH.exists():
        with LOGO_PATH.open("rb") as f:
            msg = await context.bot.send_photo(
                chat_id=chat_id,
                photo=f,
                caption=HOME_TEXT,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_keyboard(),
            )
    else:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=HOME_TEXT,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_keyboard(),
        )

    context.chat_data["home_message_id"] = msg.message_id

    try:
        await context.bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id, disable_notification=True)
    except Exception:
        pass


async def notify_staff(
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    extra_chat_ids: list[int] | None = None,
) -> int:
    """Шлёт в группу(ы). Возвращает сколько чатов успешно отправлено."""
    ok = 0
    target_ids = set(NOTIFY_CHAT_IDS)
    if extra_chat_ids:
        target_ids.update(extra_chat_ids)
    for cid in target_ids:
        try:
            # ⚠️ без ParseMode, чтобы спецсимволы не ломали отправку
            await context.bot.send_message(chat_id=cid, text=text)
            ok += 1
        except Exception as e:
            logger.exception("❌ Не смог отправить в чат %s: %s", cid, e)
    return ok


# ==========================================================
# 7) COMMANDS
# ==========================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_home(update, context)


async def chatid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"chat_id этого чата: {update.effective_chat.id}")


async def testnotify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Проверка: может ли бот писать в группу заказов."""
    ok = await notify_staff(context, "✅ Тест: бот умеет отправлять сообщения в группу заказов.")
    await update.message.reply_text(f"Результат: отправлено в {ok} чат(ов) из {len(NOTIFY_CHAT_IDS)}.")


async def webappurl_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Диагностика: показать текущий WEBAPP_URL и кнопку WebApp."""
    if not WEBAPP_URL:
        await update.message.reply_text("WEBAPP_URL не задан.")
        return
    await update.message.reply_text(f"WEBAPP_URL: {WEBAPP_URL}")
    await update.message.reply_text(
        "Открыть Mini App:",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🛒 Mini App", web_app=WebAppInfo(url=WEBAPP_URL))]]
        ),
    )


# ==========================================================
# 8) CALLBACKS
# ==========================================================
async def go_home_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    for k in ["b_date", "b_time", "b_guests", "b_name", "b_phone"]:
        context.user_data.pop(k, None)
    await show_home(update, context)


async def open_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    return


async def open_events_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()

    if not EVENTS_FILE.exists():
        await q.message.reply_text("🎉 Пока пусто.", reply_markup=back_home_kb())
        return

    with EVENTS_FILE.open("rb") as f:
        await q.message.reply_document(document=f, filename=EVENTS_FILE.name, reply_markup=back_home_kb())


# ==========================================================
# 9) BOOKING FLOW
# ==========================================================
async def booking_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("📅 Напиши дату (например: 26.01 или 26 января):", reply_markup=back_home_kb())
    return B_DATE


async def b_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["b_date"] = update.message.text.strip()
    await update.message.reply_text("⏰ Время (например: 19:30):", reply_markup=back_home_kb())
    return B_TIME


async def b_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["b_time"] = update.message.text.strip()
    await update.message.reply_text("👥 Количество гостей числом (1–50):", reply_markup=back_home_kb())
    return B_GUESTS


async def b_guests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    try:
        guests = int(raw)
        if not (1 <= guests <= 50):
            raise ValueError
    except ValueError:
        await update.message.reply_text("Напиши число от 1 до 50.", reply_markup=back_home_kb())
        return B_GUESTS

    context.user_data["b_guests"] = guests
    await update.message.reply_text("👤 На какое имя бронируем?", reply_markup=back_home_kb())
    return B_NAME


async def b_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["b_name"] = update.message.text.strip()
    await update.message.reply_text("📞 Телефон для связи:", reply_markup=back_home_kb())
    return B_PHONE


async def b_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["b_phone"] = update.message.text.strip()
    return await finalize_booking(update, context)


async def b_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return ConversationHandler.END


async def finalize_booking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user

    booking_id = create_booking(
        tg_user_id=user.id if user else None,
        tg_username=user.username if user else None,
        date=str(context.user_data.get("b_date", "")),
        time=str(context.user_data.get("b_time", "")),
        guests=int(context.user_data.get("b_guests", 1)),
        name=str(context.user_data.get("b_name", "")),
        phone=str(context.user_data.get("b_phone", "")),
        comment="",
    )

    await update.message.reply_text(
        f"✅ Бронь принята! Номер #{booking_id}",
        reply_markup=back_home_kb(),
    )

    ok = await notify_staff(
        context,
        f"📌 Новая бронь #{booking_id}\n"
        f"Дата: {context.user_data.get('b_date')}\n"
        f"Время: {context.user_data.get('b_time')}\n"
        f"Гостей: {context.user_data.get('b_guests')}\n"
        f"Имя: {context.user_data.get('b_name')}\n"
        f"Телефон: {context.user_data.get('b_phone')}\n"
        f"Комментарий: -",
    )
    logger.info("Booking notify sent to %s chats", ok)

    for k in ["b_date", "b_time", "b_guests", "b_name", "b_phone"]:
        context.user_data.pop(k, None)

    return ConversationHandler.END


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("Ок, отменил.", reply_markup=back_home_kb())
    return ConversationHandler.END


# ==========================================================
# 10) MINI APP → WEB_APP_DATA (ПРЕДЗАКАЗ)
# ==========================================================
async def webapp_order_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.web_app_data:
        return

    raw = update.message.web_app_data.data
    logger.info("📦 WEB_APP_DATA RAW: %s", raw)
    logger.info("📦 WEB_APP_DATA LEN: %s", len(raw))

    try:
        data = json.loads(raw)
    except Exception as e:
        logger.exception("❌ JSON parse error: %s", e)
        await update.message.reply_text("❌ Ошибка чтения заказа (JSON).")
        return

    if data.get("type") != "preorder":
        logger.info("⚠️ not preorder type: %s", data.get("type"))
        return

    user = update.effective_user
    who = f"@{user.username}" if user and user.username else (user.full_name if user else "Неизвестно")

    phone = str(data.get("phone", "-"))
    desired_time = str(data.get("desired_time", "-"))
    comment = str(data.get("comment", "") or "")
    total = data.get("total", 0)
    items = data.get("items", []) or []
    tg = data.get("tg") or {}
    tg_line = ""
    if isinstance(tg, dict) and tg:
        tg_user = tg.get("username") or ""
        if tg_user:
            tg_line = f"Telegram: @{tg_user}\n"

    lines = []
    for it in items:
        try:
            name = it.get("name") or it.get("id") or "item"
            qty = it.get("qty")
            summ = it.get("sum")
            if summ is not None:
                lines.append(f"- {name} × {qty} = {summ} ₽")
            else:
                lines.append(f"- {name} × {qty}")
        except Exception:
            pass

    text = (
        "🛒 НОВЫЙ ПРЕДЗАКАЗ (Mini App)\n\n"
        f"От: {who}\n"
        f"{tg_line}"
        f"Телефон: {phone}\n"
        f"Время: {desired_time}\n\n"
        + "\n".join(lines) +
        f"\n\nИтого: {total} ₽"
    )
    if comment:
        text += f"\nКомментарий: {comment}"

    source_chat_id = None
    if update.effective_chat and update.effective_chat.type in ("group", "supergroup"):
        source_chat_id = update.effective_chat.id

    ok = await notify_staff(context, text, extra_chat_ids=[source_chat_id] if source_chat_id else None)
    logger.info("Preorder notify sent to %s chats", ok)

    if ok > 0:
        # Ответ в тот чат, где был открыт мини‑апп
        try:
            await update.message.reply_text("✅ Предзаказ принят! Мы скоро свяжемся.")
        except Exception:
            pass
        # И отдельное подтверждение пользователю в личку (если доступно)
        if user:
            try:
                await context.bot.send_message(
                    chat_id=user.id,
                    text="✅ Предзаказ принят! Мы скоро свяжемся.",
                )
            except Exception:
                pass
    else:
        await update.message.reply_text(
            "❌ Заказ дошёл до бота, но НЕ отправился в группу.\n"
            "Проверь: бот добавлен в группу, chat_id верный, нет ограничений на отправку."
        )


async def debug_all_updates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Диагностика: лог всех входящих апдейтов."""
    try:
        if update.message and update.message.web_app_data:
            logger.info("✅ DEBUG: web_app_data received")
        elif update.message:
            logger.info("ℹ️ DEBUG: message chat=%s type=%s text=%s",
                        update.effective_chat.id if update.effective_chat else None,
                        update.effective_chat.type if update.effective_chat else None,
                        update.message.text)
        elif update.callback_query:
            logger.info("ℹ️ DEBUG: callback %s", update.callback_query.data)
        else:
            logger.info("ℹ️ DEBUG: update %s", update)
    except Exception as e:
        logger.exception("❌ DEBUG handler error: %s", e)


# ==========================================================
# 11) GLOBAL ERROR HANDLER
# ==========================================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error: %s", context.error)


# ==========================================================
# 12) MAIN
# ==========================================================
def main() -> None:
    init_db(str(BASE_DIR / "schema.sql"))

    app = ApplicationBuilder().token(TOKEN).build()

    # commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("chatid", chatid_cmd))
    app.add_handler(CommandHandler("testnotify", testnotify_cmd))
    app.add_handler(CommandHandler("webappurl", webappurl_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))

    # callbacks
    app.add_handler(CallbackQueryHandler(go_home_cb, pattern="^go_home$"))
    app.add_handler(CallbackQueryHandler(open_events_cb, pattern="^open_events$"))

    # booking conversation
    # booking conversation removed (бронь в мини-аппе)

    # ✅ web app data handler (шире, чтобы не потерять апдейты)
    app.add_handler(MessageHandler(filters.ALL, webapp_order_handler))
    # debug handler
    app.add_handler(MessageHandler(filters.ALL, debug_all_updates))

    # error handler
    app.add_error_handler(error_handler)

    logger.info("🤖 Бот запущен (POLLING)")
    app.run_polling()


if __name__ == "__main__":
    main()
