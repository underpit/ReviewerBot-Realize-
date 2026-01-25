import asyncio
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime
from typing import Optional, Tuple
import signal
import telegram
from aiohttp import web
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# -------------------------------------------------------------
# Константы и окружение
# -------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "reviews.db")
FALLBACK_DB_DIR = "/root/RB2"
FALLBACK_DB_PATH = os.path.join(FALLBACK_DB_DIR, "reviews.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")

WEBAPP_HOST = os.environ.get("WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.environ.get("WEBAPP_PORT", "8081"))
WEBAPP_URL = os.environ.get("WEBAPP_URL", f"http://{WEBAPP_HOST}:{WEBAPP_PORT}/webapp")

ALLOWED_CATEGORIES = {"tea", "service", "delivery"}
ALLOWED_NAME_MODES = {"tg", "anon", "custom"}
ALLOWED_PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp"}
CATEGORY_TITLES = {"tea": "Чай", "service": "Сервис", "delivery": "Доставка"}
# Лимиты 
MAX_PHOTO_SIZE = 8 * 1024 * 1024  # 8 MB
TG_CAPTION_LIMIT = 1024
TG_MESSAGE_LIMIT = 4096
REVIEW_TEXT_PREFIX = "💬 Отзыв:"
CHANNEL_SEND_LOCK = asyncio.Lock()

def load_env_file() -> None:
    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        pass


def resolve_db_path() -> str:
    env_path = os.environ.get("RB_REVIEWS_DB")
    if env_path:
        resolved = os.path.abspath(env_path)
    elif os.path.exists(DEFAULT_DB_PATH):
        resolved = DEFAULT_DB_PATH
    elif os.path.exists(FALLBACK_DB_PATH):
        resolved = FALLBACK_DB_PATH
    else:
        resolved = DEFAULT_DB_PATH

    db_dir = os.path.dirname(resolved)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    return resolved


load_env_file()
DB_PATH = resolve_db_path()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# -------------------------------------------------------------
# Токены/каналы
# -------------------------------------------------------------
bot_config: dict[str, str] = {}
try:
    with open("bot_config.json", "r", encoding="utf-8") as f:
        bot_config = json.load(f)
except FileNotFoundError:
    bot_config = {}
except json.JSONDecodeError as e:
    logger.error("Bot config error: %s", e)

env_token = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
env_channel = (
    os.environ.get("CHANNEL_ID")
    or os.environ.get("TELEGRAM_CHANNEL_ID")
    or os.environ.get("BOT_CHANNEL_ID")
)

BOT_TOKEN = env_token or bot_config.get("telegram_bot_token")
CHANNEL_ID = env_channel or bot_config.get("channel_id")

if not BOT_TOKEN or not CHANNEL_ID:
    raise RuntimeError("BOT_TOKEN and CHANNEL_ID are required via env or bot_config.json")

# -------------------------------------------------------------
# База данных
# -------------------------------------------------------------


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            product TEXT,
            photo_id TEXT,
            rating INTEGER NOT NULL,
            likes TEXT NOT NULL,
            review_text TEXT NOT NULL,
            is_anonymous BOOLEAN NOT NULL,
            timestamp TEXT NOT NULL,
            is_deleted BOOLEAN NOT NULL DEFAULT 0,
            user_name TEXT
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS web_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            user_id INTEGER,
            category TEXT NOT NULL,
            name_mode TEXT NOT NULL,
            display_name TEXT NOT NULL,
            tea_title TEXT,
            rating INTEGER NOT NULL,
            liked_most TEXT NOT NULL DEFAULT 'Ничего',
            text TEXT NOT NULL,
            photo_path TEXT,
            raw_payload TEXT,
            tg_username TEXT
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_web_reviews_user ON web_reviews(user_id)")
    conn.commit()
    conn.close()
    os.makedirs(UPLOAD_DIR, exist_ok=True)


init_db()
logger.info("База данных: %s", DB_PATH)

# -------------------------------------------------------------
# Telegram helpers
# -------------------------------------------------------------


def webapp_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("Оставить отзыв", web_app=WebAppInfo(WEBAPP_URL)),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


async def send_webapp_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat and update.effective_chat.type != "private":
        return
    text = "Чтобы оставить отзыв, воспользуйтесь кнопкой ниже:"
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        reply_markup=webapp_keyboard(),
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_webapp_link(update, context)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_webapp_link(update, context)


async def callback_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
    await send_webapp_link(update, context)


async def text_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_webapp_link(update, context)


async def webapp_data_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Логируем web_app_data, если Mini App решит присылать данные через sendData."""
    msg = update.effective_message
    data = msg.web_app_data.data if msg and msg.web_app_data else ""
    logger.info("Received web_app_data: %s", data)
    if msg:
        await msg.reply_text("Данные из WebApp получены. Спасибо!")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Update %s caused error %s", update, context.error)


# -------------------------------------------------------------
# WebApp backend
# -------------------------------------------------------------


def _bad_request(message: str) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=400)


def _validate_payload(payload_raw: str) -> Tuple[dict, Optional[str]]:
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        return {}, "Некорректный JSON в поле payload"
    if not isinstance(payload, dict):
        return {}, "payload должен быть объектом"

    category = payload.get("category")
    if category not in ALLOWED_CATEGORIES:
        return {}, "Неверная категория"

    name_mode = payload.get("nameMode") or payload.get("name_mode")
    if name_mode not in ALLOWED_NAME_MODES:
        return {}, "Неверный режим имени"

    display_name = (payload.get("displayName") or payload.get("display_name") or "").strip()
    if name_mode == "anon":
        display_name = "Гость"
    if not display_name or len(display_name) < 2:
        return {}, "Укажите имя (>= 2 символов)"

    try:
        rating = int(payload.get("rating"))
    except (TypeError, ValueError):
        return {}, "Оценка должна быть числом 1-5"
    if rating < 1 or rating > 5:
        return {}, "Оценка должна быть в диапазоне 1-5"

    text = (payload.get("text") or "").strip()
    if len(text) < 3:
        return {}, "Текст отзыва слишком короткий"

    liked_most = (payload.get("likedMost") or payload.get("liked_most") or "Ничего").strip() or "Ничего"
    disliked_most = (payload.get("dislikedMost") or payload.get("disliked_most") or "").strip()
    tea_title = payload.get("teaTitle") or payload.get("tea_title")
    if category == "tea":
        tea_title = (tea_title or "").strip()
        if len(tea_title) < 2:
            return {}, "Название чая обязательно (>= 2 символа)"
    else:
        tea_title = (tea_title or "").strip() or None

    created_at = payload.get("createdAt") or payload.get("created_at") or datetime.utcnow().isoformat()
    tg_user = payload.get("tgUser") or payload.get("tg_user") or {}
    user_id = tg_user.get("id")
    tg_username = tg_user.get("username") if isinstance(tg_user, dict) else None

    review = {
        "created_at": created_at,
        "user_id": user_id,
        "category": category,
        "name_mode": name_mode,
        "display_name": display_name,
        "tea_title": tea_title,
        "rating": rating,
        "liked_most": liked_most,
        "disliked_most": disliked_most,
        "text": text,
        "photo_path": None,
        "raw_payload": payload_raw,
        "tg_username": tg_username,
    }
    return review, None


async def _save_photo(part) -> Tuple[Optional[str], Optional[str]]:
    filename = part.filename
    if not filename:
        return None, "Файл фото отсутствует"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_PHOTO_EXT:
        return None, "Неверный формат фото (разрешены jpg/png/webp)"

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    dest_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}{ext}")
    size = 0
    try:
        with open(dest_path, "wb") as f:
            while True:
                chunk = await part.read_chunk(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_PHOTO_SIZE:
                    raise ValueError("Размер фото превышает 8MB")
                f.write(chunk)
    except Exception as e:
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except OSError:
                pass
        return None, str(e)

    return dest_path, None


def _insert_web_review(review: dict) -> int:
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    product = review.get("tea_title") if review.get("category") == "tea" else None
    photo_id = review.get("photo_path")
    user_id = review.get("user_id")
    if user_id is None:
        user_id = 0
    is_anonymous = 1 if review.get("name_mode") == "anon" else 0
    user_name = (review.get("display_name") or "Гость").strip() or "Гость"
    timestamp = review.get("created_at") or datetime.utcnow().isoformat()

    c.execute(
        """
        INSERT INTO reviews (
            user_id, category, product, photo_id,
            rating, likes, review_text,
            is_anonymous, timestamp, user_name
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            review.get("category"),
            product,
            photo_id,
            int(review.get("rating") or 0),
            (review.get("liked_most") or "Ничего"),
            (review.get("text") or "").strip(),
            is_anonymous,
            timestamp,
            user_name,
        ),
    )

    review_id = c.lastrowid
    conn.commit()
    conn.close()
    return review_id

def _split_text(text: str, limit: int) -> list[str]:
    text = text or ""
    if len(text) <= limit:
        return [text]
    parts = []
    while text:
        parts.append(text[:limit])
        text = text[limit:]
    return parts


def _format_channel_caption(review: dict) -> str:
    """Короткий caption (<=1024), без простыни текста."""
    category_title = CATEGORY_TITLES.get(review["category"], review["category"])
    stars = "⭐" * int(review.get("rating") or 0)
    liked = review.get("liked_most") or "Ничего"
    disliked = review.get("disliked_most") or ""

    lines = ["📝 Новый отзыв", ""]

    name_mode = review.get("name_mode")
    if name_mode != "anon":
        published = None
        if name_mode == "tg" and review.get("tg_username"):
            published = f"@{review['tg_username']}"
        elif review.get("display_name"):
            published = review["display_name"]
        if published:
            lines.append(f"Опубликовано: {published}")

    lines.append(f"Категория: {category_title}")
    if review["category"] == "tea" and review.get("tea_title"):
        lines.append(f"Чай: {review['tea_title']}")

    lines.append(f"Рейтинг: {stars or '—'}")
    lines.append(f"Лучшие моменты: {liked}")
    if disliked:
        lines.append(f"Не понравилось: {disliked}")
    lines.append("")
    lines.append(f"#отзыв #отзыв_{category_title.lower()}")

    caption = "\n".join(lines)
    return caption[:TG_CAPTION_LIMIT]


def _format_channel_summary(review: dict) -> str:
    category_title = CATEGORY_TITLES.get(review["category"], review["category"])
    stars = "⭐" * int(review.get("rating") or 0)
    liked = review.get("liked_most") or "Ничего"
    disliked = review.get("disliked_most") or ""

    lines = ["📝 Новый отзыв", ""]

    name_mode = review.get("name_mode")
    if name_mode != "anon":
        published = None
        if name_mode == "tg" and review.get("tg_username"):
            published = f"@{review['tg_username']}"
        elif review.get("display_name"):
            published = review["display_name"]
        if published:
            lines.append(f"Опубликовано: {published}")

    lines.append(f"Категория: {category_title}")

    if review["category"] == "tea" and review.get("tea_title"):
        lines.append(f"Чай: {review['tea_title']}")

    lines.append(f"Рейтинг: {stars or '—'}")
    lines.append(f"Лучшие моменты: {liked}")
    if disliked:
        lines.append(f"Не понравилось: {disliked}")
    lines.append("")

    tag_category = category_title.lower()
    lines.append(f"#отзыв #отзыв_{tag_category}")

    return "\n".join(lines)


def _format_review_text(review: dict) -> str:
    text = (review.get("text") or "").strip()
    if not text:
        return ""
    return f"{REVIEW_TEXT_PREFIX}\n{text}"


async def _send_to_channel(bot: telegram.Bot, review: dict) -> None:
    summary_message = _format_channel_summary(review)
    caption = _format_channel_caption(review)
    review_text_message = _format_review_text(review)

    async with CHANNEL_SEND_LOCK:
        if review.get("photo_path"):
            if len(summary_message) <= TG_CAPTION_LIMIT:
                with open(review["photo_path"], "rb") as photo_file:
                    await bot.send_photo(chat_id=CHANNEL_ID, photo=photo_file, caption=caption)

                if review_text_message:
                    for chunk in _split_text(review_text_message, TG_MESSAGE_LIMIT):
                        if chunk.strip():
                            await bot.send_message(chat_id=CHANNEL_ID, text=chunk)
            else:
                for chunk in _split_text(summary_message, TG_MESSAGE_LIMIT):
                    if chunk.strip():
                        await bot.send_message(chat_id=CHANNEL_ID, text=chunk)
                if review_text_message:
                    for chunk in _split_text(review_text_message, TG_MESSAGE_LIMIT):
                        if chunk.strip():
                            await bot.send_message(chat_id=CHANNEL_ID, text=chunk)
                with open(review["photo_path"], "rb") as photo_file:
                    await bot.send_photo(chat_id=CHANNEL_ID, photo=photo_file)
        else:
            for chunk in _split_text(summary_message, TG_MESSAGE_LIMIT):
                if chunk.strip():
                    await bot.send_message(chat_id=CHANNEL_ID, text=chunk)
            if review_text_message:
                for chunk in _split_text(review_text_message, TG_MESSAGE_LIMIT):
                    if chunk.strip():
                        await bot.send_message(chat_id=CHANNEL_ID, text=chunk)


async def api_create_review(request: web.Request) -> web.Response:
    if "multipart/form-data" not in (request.content_type or ""):
        return _bad_request("Используйте multipart/form-data для загрузки")
    reader = await request.multipart()
    payload_raw = None
    photo_path = None

    async for part in reader:
        if part.name == "payload":
            payload_raw = await part.text()
        elif part.name == "photo":
            photo_path, err = await _save_photo(part)
            if err:
                return _bad_request(err)

    if not payload_raw:
        return _bad_request("Поле payload обязательно")

    review, err = _validate_payload(payload_raw)
    if err:
        return _bad_request(err)

    if review["category"] == "tea" and not photo_path:
        return _bad_request("Для категории tea необходимо фото")

    review["photo_path"] = photo_path
    try:
        review_id = await asyncio.to_thread(_insert_web_review, review)
    except Exception as e:
        logger.error("DB insert error (web_reviews): %s", e)
        return web.json_response({"ok": False, "error": "Не удалось сохранить отзыв"}, status=500)

    logger.info(
        "Web review saved id=%s (cat=%s, photo=%s, user=%s)",
        review_id,
        review["category"],
        bool(review["photo_path"]),
        review.get("user_id"),
    )

    bot: telegram.Bot = request.app["bot"]
    try:
        await _send_to_channel(bot, review)
    except Exception as e:
        logger.error("Send to channel failed for web review %s: %s", review_id, e)
        return web.json_response({"ok": False, "error": "Сохранено, но не отправлено в канал"}, status=500)

    return web.json_response({"ok": True, "id": review_id}, status=201)


async def api_get_reviews(request: web.Request) -> web.Response:
    try:
        offset = int(request.query.get("offset", "0"))
    except ValueError:
        offset = 0
    try:
        limit = int(request.query.get("limit", "10"))
    except ValueError:
        limit = 10
    if limit > 50:
        limit = 50
    if offset < 0:
        offset = 0

    user_id_param = request.query.get("user_id")
    user_filter: Optional[int] = None
    if user_id_param:
        try:
            user_filter = int(user_id_param)
        except ValueError:
            return _bad_request("user_id должен быть числом")

    def _fetch():
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()

            params = []
            where = "WHERE is_deleted = 0"

            if user_filter is not None:
                where += " AND user_id = ?"
                params.append(user_filter)

            params.extend([limit + 1, offset])

            c.execute(
                f"""
                SELECT
                    id,
                    timestamp,
                    user_name,
                    category,
                    product,
                    rating,
                    likes,
                    review_text,
                    photo_id
                FROM reviews
                {where}
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                params,
            )
            rows = c.fetchall()
            conn.close()
            return rows


    try:
        rows = await asyncio.to_thread(_fetch)
    except Exception as e:
        logger.error("DB read error (web_reviews): %s", e)
        return web.json_response({"ok": False, "error": "Не удалось получить отзывы"}, status=500)

    has_more = len(rows) > limit
    rows = rows[:limit]
    items = []
    for row in rows:
        items.append(
            {
                "id": row["id"],
                "created_at": row["timestamp"],
                "createdAt": row["timestamp"],
                "category": row["category"],
                "rating": row["rating"],
                "display_name": row["user_name"] or "Гость",
                "displayName": row["user_name"] or "Гость",
                "tea_title": row["product"],
                "teaTitle": row["product"],
                "liked_most": row["likes"] or "Ничего",
                "likedMost": row["likes"] or "Ничего",
                "text": row["review_text"] or "",
                "photo": (("/" + row["photo_id"]) if (row["photo_id"] and str(row["photo_id"]).startswith("uploads/")) else None),
            }
        )

    return web.json_response({"items": items, "hasMore": has_more})


async def serve_index(_: web.Request) -> web.Response:
    return web.FileResponse(path=os.path.join(BASE_DIR, "index.html"))


async def serve_promo(_: web.Request) -> web.Response:
    promo_path = os.path.join(BASE_DIR, "promo.json")
    if not os.path.exists(promo_path):
        return web.json_response({"error": "promo.json not found"}, status=404)
    try:
        with open(promo_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return web.json_response(data)
    except Exception as e:
        logger.error("Promo file read error: %s", e)
        return web.json_response({"error": "promo.json invalid"}, status=500)


def build_web_app(bot: telegram.Bot) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app.router.add_get("/", serve_index)
    app.router.add_get("/webapp", serve_index)
    app.router.add_get("/promo.json", serve_promo)
    app.router.add_post("/api/review", api_create_review)
    app.router.add_get("/api/reviews", api_get_reviews)
    app.router.add_static("/uploads/", path=UPLOAD_DIR, show_index=False)
    return app


async def start_web_server(bot: telegram.Bot) -> web.AppRunner:
    app = build_web_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=WEBAPP_HOST, port=WEBAPP_PORT)
    await site.start()
    logger.info("WebApp сервер запущен на %s", WEBAPP_URL)
    return runner


# -------------------------------------------------------------
# Entrypoint
# -------------------------------------------------------------

async def async_main() -> None:
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .pool_timeout(30)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, webapp_data_handler))
    application.add_handler(CallbackQueryHandler(callback_fallback, pattern=".*"))
    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.StatusUpdate.WEB_APP_DATA & ~filters.COMMAND,
            text_fallback,
        )
    )
    application.add_error_handler(error_handler)

    runner = await start_web_server(application.bot)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    try:
        # Полный “ручной” жизненный цикл PTB (без updater.idle и без run_polling)
        await application.initialize()
        await application.start()

        # стартуем polling
        await application.updater.start_polling(drop_pending_updates=True)

        # держим процесс живым
        await stop_event.wait()

    finally:
        # Останавливаем polling корректно (чтобы не было "Updater is still running!")
        try:
            if application.updater and application.updater.running:
                await application.updater.stop()
        except Exception:
            pass

        try:
            await application.stop()
        except Exception:
            pass

        try:
            await application.shutdown()
        except Exception:
            pass

        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(async_main())
