from __future__ import annotations

# ==========================================================
# 0) IMPORTS
# ==========================================================
import json
import logging
import os
import html
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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


# ==========================================================
# 3) LINKS + NOTIFICATIONS
# ==========================================================
YANDEX_REVIEWS_URL = "https://yandex.ru/maps/org/spalnik/104151350821/reviews/?ll=37.715866%2C55.532722&z=16"
GIS2_REVIEWS_URL = "https://2gis.ru/moscow/firm/70000001053915498"
YANDEX_FOOD_URL = "https://eda.yandex.ru/r/spal_nik?placeSlug=spalnik"
TG_CHANNEL_URL = "https://t.me/SpalnikBar"

TIP_URL = ""  # если пусто — будет “скоро здесь можно будет оставить чаевые”

# !!! ВАЖНО !!!
# Сюда должен быть добавлен chat_id ГРУППЫ заказов.
# Обычно он выглядит так: -1002345678901
NOTIFY_CHAT_IDS: list[int] = [
    -5102802574,  # <-- замени на chat_id группы заказов (лучше -100...)
]


# ==========================================================
# 4) BOOKING STATES
# ==========================================================
B_DATE, B_TIME, B_GUESTS, B_NAME, B_PHONE, B_COMMENT = range(6)


# ==========================================================
# 5) UI
# ==========================================================
HOME_TEXT = "🍻 <b>Спальник Бар</b>\n\nВыбирай действие 👇"


def main_keyboard() -> InlineKeyboardMarkup:
    tips_btn = (
        InlineKeyboardButton("💜 Чаевые", url=TIP_URL)
        if TIP_URL
        else InlineKeyboardButton("💜 Чаевые", callback_data="tips")
    )
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📋 Меню (PDF)", callback_data="open_menu"),
                InlineKeyboardButton("🎉 События", callback_data="open_events"),
            ],
            [
                InlineKeyboardButton("⭐ (Яндекс)", url=YANDEX_REVIEWS_URL),
                InlineKeyboardButton("⭐ (2ГИС)", url=GIS2_REVIEWS_URL),
            ],
            [
                InlineKeyboardButton("📣 Наш канал", url=TG_CHANNEL_URL),
                InlineKeyboardButton("🛵 Яндекс Еда", url=YANDEX_FOOD_URL),
            ],
            [
                InlineKeyboardButton("📅 Бронь столов", callback_data="book_start"),
                tips_btn,
            ],
        ]
    )


def back_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Главное меню", callback_data="go_home")]])


# ==========================================================
# 6) HELPERS: pinned home + cleanup previous home message
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
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard(),
            )
    else:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=HOME_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
        )

    context.chat_data["home_message_id"] = msg.message_id

    # закрепление (если бот админ)
    try:
        await context.bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id, disable_notification=True)
    except Exception:
        pass


async def notify_staff(context: ContextTypes.DEFAULT_TYPE, text_html: str) -> tuple[int, list[str]]:
    """Шлём в группы. Возвращаем (сколько отправили, список ошибок)."""
    sent = 0
    errors: list[str] = []
    for cid in NOTIFY_CHAT_IDS:
        try:
            await context.bot.send_message(
                chat_id=cid,
                text=text_html,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            sent += 1
        except Exception as e:
            err = f"chat_id={cid}: {type(e).__name__}: {e}"
            errors.append(err)
            logger.exception("❌ Не отправилось в чат %s", cid)
    return sent, errors


# ==========================================================
# 7) COMMANDS
# ==========================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_home(update, context)


async def chatid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(f"chat_id этого чата: {update.effective_chat.id}")


# ==========================================================
# 8) CALLBACKS
# ==========================================================
async def go_home_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    for k in ["b_date", "b_time", "b_guests", "b_name", "b_phone"]:
        context.user_data.pop(k, None)
    await show_home(update, context)


async def tips_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "💜 Скоро здесь можно будет оставить чаевые.",
        reply_markup=back_home_kb(),
    )


async def open_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()

    if not MENU_FILE.exists():
        await q.message.reply_text("Файл меню не найден 🙁 Проверь `assets/menu.pdf`.", reply_markup=back_home_kb())
        return

    with MENU_FILE.open("rb") as f:
        await q.message.reply_document(document=f, filename=MENU_FILE.name, reply_markup=back_home_kb())


async def open_events_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()

    if not EVENTS_FILE.exists():
        await q.message.reply_text("🎉 Файла событий пока нет. Добавишь позже.", reply_markup=back_home_kb())
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
    await update.message.reply_text("💬 Комментарий (необязательно). Если нет — напиши: -", reply_markup=back_home_kb())
    return B_COMMENT


async def b_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    comment = update.message.text.strip()
    if comment == "-":
        comment = ""

    user = update.effective_user

    booking_id = create_booking(
        tg_user_id=user.id if user else None,
        tg_username=user.username if user else None,
        date=str(context.user_data.get("b_date", "")),
        time=str(context.user_data.get("b_time", "")),
        guests=int(context.user_data.get("b_guests", 1)),
        name=str(context.user_data.get("b_name", "")),
        phone=str(context.user_data.get("b_phone", "")),
        comment=comment,
    )

    msg_user = (
        f"✅ <b>Бронь принята!</b>\n\n"
        f"Номер: <b>#{booking_id}</b>\n"
        f"Дата: <b>{html.escape(str(context.user_data.get('b_date')))}</b>\n"
        f"Время: <b>{html.escape(str(context.user_data.get('b_time')))}</b>\n"
        f"Гостей: <b>{html.escape(str(context.user_data.get('b_guests')))}</b>\n"
        f"Имя: <b>{html.escape(str(context.user_data.get('b_name')))}</b>\n"
        f"Телефон: <b>{html.escape(str(context.user_data.get('b_phone')))}</b>\n"
    )
    if comment:
        msg_user += f"Комментарий: <i>{html.escape(comment)}</i>\n"

    await update.message.reply_text(msg_user, parse_mode=ParseMode.HTML, reply_markup=back_home_kb())

    # уведомление персоналу
    who = ""
    if user and user.username:
        who = f"@{user.username}"
    elif user:
        who = user.full_name
    else:
        who = "Неизвестно"

    msg_staff = (
        f"📌 <b>Новая бронь</b>\n\n"
        f"Заявка: <b>#{booking_id}</b>\n"
        f"Дата: <b>{html.escape(str(context.user_data.get('b_date')))}</b>\n"
        f"Время: <b>{html.escape(str(context.user_data.get('b_time')))}</b>\n"
        f"Гостей: <b>{html.escape(str(context.user_data.get('b_guests')))}</b>\n"
        f"Имя: <b>{html.escape(str(context.user_data.get('b_name')))}</b>\n"
        f"Телефон: <b>{html.escape(str(context.user_data.get('b_phone')))}</b>\n"
        f"От: <b>{html.escape(who)}</b>"
    )
    if comment:
        msg_staff += f"\nКомментарий: <i>{html.escape(comment)}</i>"

    await notify_staff(context, msg_staff)

    for k in ["b_date", "b_time", "b_guests", "b_name", "b_phone"]:
        context.user_data.pop(k, None)

    return ConversationHandler.END


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("Ок, отменил.", reply_markup=back_home_kb())
    return ConversationHandler.END


# ==========================================================
# 10) MINI APP → ПРИЁМ ПРЕДЗАКАЗА
# ==========================================================
async def webapp_order_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Ловит update.message.web_app_data (Mini App sendData()) и шлёт в группу заказов.
    """

    # 1) Забираем raw максимально надёжно (Telegram иногда по-разному кладёт message)
    raw = None
    if update.message and update.message.web_app_data:
        raw = update.message.web_app_data.data
    elif update.effective_message and update.effective_message.web_app_data:
        raw = update.effective_message.web_app_data.data

    if not raw:
        return

    logger.info("📦 MINIAPP RAW DATA: %s", raw)

    # 2) Парсим JSON
    try:
        data = json.loads(raw)
    except Exception as e:
        logger.exception("❌ JSON parse error: %s", e)
        if update.effective_message:
            await update.effective_message.reply_text("❌ Ошибка чтения заказа.")
        return

    if data.get("type") != "preorder":
        if update.effective_message:
            await update.effective_message.reply_text("⚠️ Это не предзаказ.")
        return

    # 3) Собираем текст заказа
    user = update.effective_user
    who = ""
    if user and user.username:
        who = f"@{user.username}"
    elif user:
        who = user.full_name
    else:
        who = "Неизвестно"

    phone = str(data.get("phone", "-"))
    desired_time = str(data.get("desired_time", "-"))
    comment = str(data.get("comment", "") or "")
    total = data.get("total", 0)
    items = data.get("items", []) or []

    lines = []
    for it in items:
        try:
            name = html.escape(str(it.get("name", "")))
            qty = html.escape(str(it.get("qty", "")))
            s = html.escape(str(it.get("sum", "")))
            lines.append(f"• {name} × {qty} = {s} ₽")
        except Exception:
            continue

    if not lines:
        lines = ["• (пусто)"]

    text_html = (
        "🛒 <b>НОВЫЙ ПРЕДЗАКАЗ (Mini App)</b>\n\n"
        f"👤 От: <b>{html.escape(who)}</b>\n"
        f"📞 Телефон: <b>{html.escape(phone)}</b>\n"
        f"⏰ Время: <b>{html.escape(desired_time)}</b>\n\n"
        + "\n".join(lines) +
        f"\n\n💰 <b>Итого:</b> {html.escape(str(total))} ₽"
    )

    if comment.strip():
        text_html += f"\n\n💬 Комментарий: <i>{html.escape(comment.strip())}</i>"

    # 4) Шлём в группы
    sent, errors = await notify_staff(context, text_html)

    # 5) Ответ пользователю
    if update.effective_message:
        if sent > 0:
            await update.effective_message.reply_text("✅ Предзаказ принят! Мы скоро свяжемся.")
        else:
            # максимально полезно — показать причину
            err_text = "\n".join(errors[:2]) if errors else "неизвестная ошибка"
            await update.effective_message.reply_text(
                "❌ Заказ дошёл до бота, но НЕ отправился в группу.\n\n"
                "Проверь:\n"
                "1) бот добавлен в группу заказов\n"
                "2) бот может писать в группе (лучше сделать админом)\n"
                "3) chat_id группы правильный (обычно -100...)\n\n"
                f"Ошибка: {err_text}"
            )


# ==========================================================
# 11) GLOBAL ERROR HANDLER
# ==========================================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error: %s", context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text("⚠️ Ошибка. Попробуй ещё раз или напиши администратору.")
    except Exception:
        pass


# ==========================================================
# 12) MAIN
# ==========================================================
def main() -> None:
    init_db(str(BASE_DIR / "schema.sql"))

    app = ApplicationBuilder().token(TOKEN).build()

    # commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("chatid", chatid_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))

    # callbacks
    app.add_handler(CallbackQueryHandler(go_home_cb, pattern="^go_home$"))
    app.add_handler(CallbackQueryHandler(open_menu_cb, pattern="^open_menu$"))
    app.add_handler(CallbackQueryHandler(open_events_cb, pattern="^open_events$"))
    app.add_handler(CallbackQueryHandler(tips_cb, pattern="^tips$"))

    # booking conversation
    booking_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(booking_entry, pattern="^book_start$")],
        states={
            B_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, b_date)],
            B_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, b_time)],
            B_GUESTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, b_guests)],
            B_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, b_name)],
            B_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, b_phone)],
            B_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, b_comment)],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
        allow_reentry=True,
    )
    app.add_handler(booking_conv)

    # mini app orders (ОБЯЗАТЕЛЬНО)
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, webapp_order_handler))

    # error handler
    app.add_error_handler(error_handler)

    logger.info("🤖 Бот запущен (polling)")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
