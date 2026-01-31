import asyncio
import html
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
FALLBACK_DB_DIR = "/root/ReviewBot_Debug/Main/"
FALLBACK_DB_PATH = os.path.join(FALLBACK_DB_DIR, "reviews.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")

WEBAPP_HOST = os.environ.get("WEBAPP_HOST", "0.0.0.0")  # env: bind host
WEBAPP_PORT = int(os.environ.get("WEBAPP_PORT", "8080"))  # env: bind port
WEBAPP_URL = os.environ.get("WEBAPP_URL", f"http://{WEBAPP_HOST}:{WEBAPP_PORT}/debug")  # env: WebApp URL

# API prefix for debug endpoints (default: /api-debug)
API_PREFIX = (os.environ.get("API_PREFIX") or "/api-debug").strip()
if API_PREFIX and not API_PREFIX.startswith("/"):
    API_PREFIX = f"/{API_PREFIX}"
API_PREFIX = API_PREFIX.rstrip("/")
# env: optional bot name for logs
BOT_NAME = (os.environ.get("BOT_NAME") or "").strip()
# env: webhook path (e.g. /webhook/bot1)
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", "/webhook/bot")
# env: optional webhook secret token (validated on requests)
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")
# env: public base URL used for setWebhook (e.g. https://ipuerwebhookservice.ru)
PUBLIC_BASE_URL = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")

if WEBHOOK_PATH and not WEBHOOK_PATH.startswith("/"):
    WEBHOOK_PATH = f"/{WEBHOOK_PATH}"

LOG_PREFIX = f"[{BOT_NAME}] " if BOT_NAME else ""

ALLOWED_CATEGORIES = {"tea", "service", "delivery"}
ALLOWED_NAME_MODES = {"tg", "anon", "custom"}
ALLOWED_PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp"}
CATEGORY_TITLES = {"tea": "Чай", "service": "Сервис", "delivery": "Доставка"}
# Лимиты 
MAX_PHOTO_SIZE = 8 * 1024 * 1024  # 8 MB
TG_CAPTION_LIMIT = 1024
TG_MESSAGE_LIMIT = 4096
REVIEW_TEXT_PREFIX = ""
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


def _escape_html(text: str) -> str:
    return html.escape(text or "", quote=False)


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 0:
        return ""
    return text[: max(0, limit - 1)] + "…"


def _build_text_parts(review: dict) -> tuple[str, str, str, str]:
    """Return header, review_text, full_text, tags (raw, no HTML)."""
    category_title = CATEGORY_TITLES.get(review["category"], review["category"])
    stars = "⭐" * int(review.get("rating") or 0)
    liked = review.get("liked_most") or "Ничего"
    disliked = review.get("disliked_most") or ""

    lines = []
    name_mode = review.get("name_mode")
    if name_mode != "anon":
        published = None
        if name_mode == "tg" and review.get("tg_username"):
            published = f"@{review['tg_username']}"
        elif review.get("display_name"):
            published = review["display_name"]
        if published:
            lines.append(f"Опубликовано: {published}")

    if review["category"] != "tea":
        lines.append(f"Категория: {category_title}")
    if review["category"] == "tea" and review.get("tea_title"):
        lines.append(f"Чай: {review['tea_title']}")

    lines.append(f"Рейтинг: {stars or '—'}")
    lines.append(f"Понравилось: {liked}")
    if disliked:
        lines.append(f"Не понравилось: {disliked}")

    header_text = "\n".join(lines).strip()
    tags = f"#отзыв #отзыв_{category_title.lower()}"
    review_text = (review.get("text") or "").strip()

    full_text = header_text or ""
    if review_text:
        full_text = f"{full_text}\n\nОтзыв:\n{review_text}" if full_text else f"Отзыв:\n{review_text}"
    if tags:
        full_text = f"{full_text}\n\n{tags}" if full_text else tags

    return header_text, review_text, full_text, tags


def _fit_full_text(header_text: str, review_text: str, tags: str, limit: int) -> str:
    """Ensure message fits Telegram limit while keeping structure."""
    base = header_text or ""
    if review_text:
        base = f"{base}\n\nОтзыв:\n{review_text}" if base else f"Отзыв:\n{review_text}"
    suffix = f"\n\n{tags}" if tags else ""
    full = f"{base}{suffix}"
    if len(full) <= limit:
        return full

    if not review_text:
        return _truncate_text(full, limit)

    prefix = f"{header_text}\n\nОтзыв:\n" if header_text else "Отзыв:\n"
    available = limit - len(prefix) - len(suffix)
    if available <= 0:
        trimmed_header = _truncate_text(header_text, max(0, limit - len(suffix)))
        return f"{trimmed_header}{suffix}".strip()

    trimmed_review = _truncate_text(review_text, available)
    return f"{prefix}{trimmed_review}{suffix}".strip()


def _format_channel_summary(review: dict) -> str:
    header_text, _, _, tags = _build_text_parts(review)
    summary = header_text
    if tags:
        summary = f"{summary}\n\n{tags}" if summary else tags
    return summary


def _format_channel_caption(review: dict) -> str:
    """Short caption (<=1024)."""
    header_text, _, _, tags = _build_text_parts(review)
    caption = header_text
    if tags:
        caption = f"{caption}\n\n{tags}" if caption else tags
    return _truncate_text(caption, TG_CAPTION_LIMIT)


async def send_review_to_channel(
    bot: telegram.Bot,
    channel_id: str,
    *,
    review: dict,
    dry_run: bool = False,
) -> dict:
    """
    Robust sender with logging:
    - one message for non-tea or no photo
    - photo + caption if <=1024
    - photo + reply with review text if caption >1024
    """
    header_text, review_text, full_text, tags = _build_text_parts(review)
    requires_photo = review.get("category") == "tea"
    photo_path = review.get("photo_path")

    caption_raw = full_text
    caption_escaped = _escape_html(caption_raw)
    caption_len = len(caption_escaped)
    full_len = len(_escape_html(full_text))
    review_len = len(_escape_html(review_text))

    logger.info(
        "send_review.start",
        extra={
            "category": review.get("category"),
            "requires_photo": requires_photo,
            "channel_id": channel_id,
            "caption_len": caption_len,
            "full_text_len": full_len,
            "review_text_len": review_len,
            "has_photo": bool(photo_path),
            "photo_path": photo_path,
            "dry_run": dry_run,
        },
    )

    if requires_photo and not photo_path:
        logger.warning("send_review.missing_photo", extra={"channel_id": channel_id})
        return {"ok": False, "error": "Фото обязательно для категории tea"}

    try:
        async with CHANNEL_SEND_LOCK:
            # Rule A: non-tea or no photo -> single sendMessage only.
            if not requires_photo or not photo_path:
                single = _fit_full_text(header_text, review_text, tags, TG_MESSAGE_LIMIT)
                if dry_run:
                    return {"ok": True, "message_ids": ["dry_run_single"]}
                msg = await bot.send_message(
                    chat_id=channel_id,
                    text=_escape_html(single),
                    parse_mode="HTML",
                )
                logger.info("send_review.ok.single", extra={"message_id": msg.message_id})
                return {"ok": True, "message_ids": [msg.message_id]}

            # Rule C: tea + photo + text fits caption -> send one photo with full caption.
            if len(caption_escaped) <= TG_CAPTION_LIMIT:
                if dry_run:
                    return {"ok": True, "message_ids": ["dry_run_photo"]}
                with open(photo_path, "rb") as photo_file:
                    msg = await bot.send_photo(
                        chat_id=channel_id,
                        photo=photo_file,
                        caption=caption_escaped,
                        parse_mode="HTML",
                    )
                logger.info("send_review.ok.photo", extra={"message_id": msg.message_id})
                return {"ok": True, "message_ids": [msg.message_id]}

            # Rule B: tea + photo + caption > 1024 -> photo with short header, review as reply.
            caption_short = _format_channel_caption(review)
            caption_short_escaped = _escape_html(caption_short) if caption_short else None
            if dry_run:
                return {"ok": True, "message_ids": ["dry_run_photo", "dry_run_reply"]}
            with open(photo_path, "rb") as photo_file:
                photo_msg = await bot.send_photo(
                    chat_id=channel_id,
                    photo=photo_file,
                    caption=caption_short_escaped if caption_short_escaped else None,
                    parse_mode="HTML" if caption_short_escaped else None,
                )

            if review_text:
                reply_text = _truncate_text(f"Отзыв:\n{review_text}", TG_MESSAGE_LIMIT)
                try:
                    # Reply keeps visual width aligned with the photo message.
                    msg = await bot.send_message(
                        chat_id=channel_id,
                        text=_escape_html(reply_text),
                        parse_mode="HTML",
                        reply_to_message_id=photo_msg.message_id,
                        allow_sending_without_reply=True,
                    )
                except Exception:
                    # If replies are not supported, send after with separator.
                    fallback = f"———\n{reply_text}\n———"
                    msg = await bot.send_message(
                        chat_id=channel_id,
                        text=_escape_html(fallback),
                        parse_mode="HTML",
                    )
            else:
                msg = photo_msg

            logger.info(
                "send_review.ok.photo_reply",
                extra={"photo_message_id": photo_msg.message_id, "reply_message_id": msg.message_id},
            )
            return {"ok": True, "message_ids": [photo_msg.message_id, msg.message_id]}
    except Exception as e:
        logger.exception("send_review.failed", extra={"error": str(e)})
        return {"ok": False, "error": "Не удалось отправить в Telegram"}


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
    result = await send_review_to_channel(bot, CHANNEL_ID, review=review)
    if not result.get("ok"):
        return web.json_response(
            {"ok": False, "error": result.get("error", "Сохранено, но не отправлено в канал")},
            status=500,
        )

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


async def api_debug_preview(request: web.Request) -> web.Response:
    payload_raw = None
    try:
        if "application/json" in (request.content_type or ""):
            data = await request.json()
            if isinstance(data, dict) and "payload" in data:
                payload_raw = data.get("payload")
            else:
                payload_raw = json.dumps(data, ensure_ascii=False)
        else:
            payload_raw = await request.text()
    except Exception:
        return _bad_request("Не удалось разобрать JSON")

    if not payload_raw:
        return _bad_request("Тело запроса пустое")

    review, err = _validate_payload(payload_raw)
    if err:
        return _bad_request(err)

    header_text, review_text, full_text, tags = _build_text_parts(review)
    summary = _format_channel_summary(review)
    caption = _format_channel_caption(review)

    logger.info(
        "Debug preview: category=%s name_mode=%s rating=%s",
        review.get("category"),
        review.get("name_mode"),
        review.get("rating"),
    )

    return web.json_response(
        {
            "ok": True,
            "summary": summary,
            "caption": caption,
            "header_text": header_text,
            "review_text": review_text,
            "full_text": full_text,
            "requires_photo": review.get("category") == "tea",
        }
    )


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


async def healthcheck(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def telegram_webhook(request: web.Request) -> web.Response:
    if WEBHOOK_SECRET:
        header_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        query_token = request.query.get("token")
        if header_token != WEBHOOK_SECRET and query_token != WEBHOOK_SECRET:
            logger.warning("%sWebhook secret mismatch from %s", LOG_PREFIX, request.remote)
            return web.Response(status=401, text="unauthorized")

    try:
        payload = await request.json()
    except Exception:
        return web.Response(status=400, text="invalid json")

    update = Update.de_json(payload, request.app["bot"])
    if update:
        application: Application = request.app["tg_app"]
        try:
            application.update_queue.put_nowait(update)
        except asyncio.QueueFull:
            async def _process() -> None:
                try:
                    await application.process_update(update)
                except Exception:
                    logger.exception("%sFailed to process update", LOG_PREFIX)

            asyncio.create_task(_process())

    return web.Response(status=200, text="ok")


def build_web_app(bot: telegram.Bot, application: Application) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app["tg_app"] = application
    app.router.add_get("/", serve_index)
    app.router.add_get("/webapp", serve_index)
    app.router.add_get("/debug", serve_index)
    app.router.add_get("/debug/", serve_index)
    app.router.add_get("/health", healthcheck)
    app.router.add_get("/debug/promo.json", serve_promo)
    app.router.add_post(WEBHOOK_PATH, telegram_webhook)
    app.router.add_post("/api/review", api_create_review)
    app.router.add_get("/api/reviews", api_get_reviews)
    app.router.add_post(f"{API_PREFIX}/review", api_create_review)
    app.router.add_get(f"{API_PREFIX}/review", api_get_reviews)
    app.router.add_post(f"{API_PREFIX}/preview", api_debug_preview)
    app.router.add_static("/uploads/", path=UPLOAD_DIR, show_index=False)
    return app


async def start_web_server(bot: telegram.Bot, application: Application) -> web.AppRunner:
    app = build_web_app(bot, application)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=WEBAPP_HOST, port=WEBAPP_PORT)
    await site.start()
    logger.info("WebApp сервер запущен на %s", WEBAPP_URL)
    return runner


async def configure_webhook(bot: telegram.Bot) -> None:
    if not PUBLIC_BASE_URL:
        logger.warning("%sPUBLIC_BASE_URL is not set; webhook registration skipped", LOG_PREFIX)
        return
    url = f"{PUBLIC_BASE_URL}{WEBHOOK_PATH}"
    try:
        await bot.set_webhook(url=url, secret_token=WEBHOOK_SECRET or None)
        logger.info("%sWebhook установлен: %s", LOG_PREFIX, url)
    except Exception as e:
        logger.error("%ssetWebhook failed: %s", LOG_PREFIX, e)
        raise

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

    runner = await start_web_server(application.bot, application)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    try:
        # Полный “ручной” жизненный цикл PTB (без polling)
        await application.initialize()
        await application.start()
        await configure_webhook(application.bot)
        if os.environ.get("RUN_SEND_TESTS") == "1":
            await _run_send_tests(application.bot)

        # держим процесс живым
        await stop_event.wait()

    finally:
        try:
            await application.stop()
        except Exception:
            pass

        try:
            await application.shutdown()
        except Exception:
            pass

        await runner.cleanup()


async def _run_send_tests(bot: telegram.Bot) -> None:
    """Dry-run tests for send logic (no Telegram calls)."""
    logger.info("send_tests.start")
    tests = [
        {
            "name": "tea_short_caption",
            "review": {
                "category": "tea",
                "name_mode": "tg",
                "tg_username": "tester",
                "display_name": "Tester",
                "tea_title": "Test Tea",
                "rating": 5,
                "liked_most": "Вкус",
                "disliked_most": "Послевкусие",
                "text": "Короткий отзыв",
                "photo_path": "/tmp/fake.jpg",
            },
        },
        {
            "name": "tea_long_caption",
            "review": {
                "category": "tea",
                "name_mode": "tg",
                "tg_username": "tester",
                "display_name": "Tester",
                "tea_title": "Test Tea",
                "rating": 5,
                "liked_most": "Вкус",
                "disliked_most": "Послевкусие",
                "text": "Длинный " * 300,
                "photo_path": "/tmp/fake.jpg",
            },
        },
        {
            "name": "service_no_photo",
            "review": {
                "category": "service",
                "name_mode": "custom",
                "display_name": "User",
                "rating": 4,
                "liked_most": "Скорость",
                "text": "Сервис норм",
                "photo_path": None,
            },
        },
        {
            "name": "tea_missing_photo",
            "review": {
                "category": "tea",
                "name_mode": "custom",
                "display_name": "User",
                "tea_title": "Test Tea",
                "rating": 4,
                "liked_most": "Вкус",
                "text": "Нет фото",
                "photo_path": None,
            },
        },
    ]

    for t in tests:
        res = await send_review_to_channel(
            bot,
            CHANNEL_ID,
            review=t["review"],
            dry_run=True,
        )
        logger.info("send_tests.case", extra={"case": t["name"], "result": res})
    logger.info("send_tests.done")


if __name__ == "__main__":
    asyncio.run(async_main())
