import asyncio
import logging
import json
import sqlite3
import os  # ← ДОБАВИЛИ
import threading
import uuid
from datetime import datetime
from enum import IntEnum
import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from aiohttp import web
import telegram.error
import traceback
from tea import (
    tea_product, tea_photo, tea_photo_reprompt, tea_rating, tea_likes, tea_review_text
)
from service import (
    service_likes, service_likes_handler, service_rating_handler, service_review_text
)
from delivery import (
    delivery_likes, delivery_likes_handler, delivery_rating_handler, delivery_review_text
)
from form import (
    format_tea_review, format_service_review, format_delivery_review
)

# ========================================
# ФИКСИРОВАННЫЙ ПУТЬ К БД
# ========================================
# ========================================
# ПУТЬ К БД (поддержка старого файла из репозитория)
# ========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_env_file():
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
        # Не критично, просто продолжаем с переменными окружения
        pass


load_env_file()

DEFAULT_DB_PATH = os.path.join(BASE_DIR, "reviews.db")
FALLBACK_DB_DIR = "/root/RB2"
FALLBACK_DB_PATH = os.path.join(FALLBACK_DB_DIR, "reviews.db")

def resolve_db_path() -> str:
    """Choose DB path prioritizing env override and existing legacy files."""
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

DB_PATH = resolve_db_path()
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
WEBAPP_HOST = os.environ.get("WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.environ.get("WEBAPP_PORT", "8080"))
WEBAPP_URL = os.environ.get("WEBAPP_URL", f"http://{WEBAPP_HOST}:{WEBAPP_PORT}/webapp")
MAX_PHOTO_SIZE = 8 * 1024 * 1024  # 8 MB
ALLOWED_CATEGORIES = {"tea", "service", "delivery"}
ALLOWED_NAME_MODES = {"tg", "anon", "custom"}
ALLOWED_PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp"}
CATEGORY_TITLES = {"tea": "Чай", "service": "Сервис", "delivery": "Доставка"}

# Логируем путь при запуске
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["WebApp", "Помощь"]], resize_keyboard=True, one_time_keyboard=False
    )


def main_menu_inline() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("Открыть WebApp", web_app=WebAppInfo(WEBAPP_URL)),
            InlineKeyboardButton("В браузере", url=WEBAPP_URL),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


async def send_webapp_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет основную ссылку на WebApp и кнопку."""
    if update.effective_chat.type != "private":
        return
    text = "Открыть форму отзывов в WebApp:"
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        reply_markup=main_menu_inline(),
    )

# ========================================
# Инициализация БД
# ========================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Create table if not exists
    c.execute('''
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
            is_deleted BOOLEAN NOT NULL DEFAULT 0
        )
    ''')
    # Add user_name column if missing
    try:
        c.execute("ALTER TABLE reviews ADD COLUMN user_name TEXT;")
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e):
            logger.error(f"Error adding user_name column: {e}")
    # WebApp reviews table (non-destructive for legacy data)
    c.execute('''
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
            raw_payload TEXT
        )
    ''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_web_reviews_user ON web_reviews(user_id)")
    conn.commit()
    conn.close()
    os.makedirs(UPLOAD_DIR, exist_ok=True)

init_db()  # Создаём при старте

# Лог при запуске
logger.info(f"База данных: {DB_PATH}")
if os.path.exists(DB_PATH):
    logger.info(f"БД найдена, размер: {os.path.getsize(DB_PATH)} байт")
else:
    logger.info("БД не найдена — будет создана при первом сохранении")

try:
    with open("bot_config.json", "r", encoding="utf-8") as f:
        bot_config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError) as e:
    logger.error(f"Bot config error: {e}")
    bot_config = {"telegram_bot_token": "fallback_token", "channel_id": "@fallback_channel"}
BOT_TOKEN = bot_config["telegram_bot_token"]
CHANNEL_ID = bot_config["channel_id"]
# States as IntEnum for readability and faster lookups
class States(IntEnum):
    CATEGORY = 0
    MORE_REVIEWS = 1
    TEA_PRODUCT = 2
    TEA_PHOTO = 3
    TEA_RATING = 4
    TEA_LIKES = 5
    TEA_REVIEW_TEXT = 6
    SERVICE_LIKES = 7
    SERVICE_RATING = 8
    SERVICE_REVIEW_TEXT = 9
    DELIVERY_LIKES = 10
    DELIVERY_RATING = 11
    DELIVERY_REVIEW_TEXT = 12
    PREVIEW = 13
    EDIT_MENU = 14
    EDIT_RATING = 15
    EDIT_LIKES = 16
    EDIT_PRODUCT = 17
    EDIT_REVIEW = 18
    FINAL_CONFIRM = 19
    NAME = 20
    HISTORY = 21
    DELETE_SPECIFIC = 22
    EDIT_PERSON = 23  # New state for editing person/name
# Common rating keyboard (optimized, used in all rating handlers)
def get_rating_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("1", callback_data="rate_1"),
            InlineKeyboardButton("2", callback_data="rate_2"),
            InlineKeyboardButton("3", callback_data="rate_3"),
            InlineKeyboardButton("4", callback_data="rate_4"),
            InlineKeyboardButton("5", callback_data="rate_5"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)
# Category routing dict (unchanged)
CATEGORY_ROUTING = {
    'чай': {'product': tea_product, 'likes_state': States.TEA_LIKES, 'rating_state': States.TEA_RATING, 'review_state': States.TEA_REVIEW_TEXT, 'format': format_tea_review, 'has_product': True},
    'сервис': {'product': None, 'likes_state': States.SERVICE_LIKES, 'rating_state': States.SERVICE_RATING, 'review_state': States.SERVICE_REVIEW_TEXT, 'format': format_service_review, 'has_product': False},
    'доставка': {'product': None, 'likes_state': States.DELIVERY_LIKES, 'rating_state': States.DELIVERY_RATING, 'review_state': States.DELIVERY_REVIEW_TEXT, 'format': format_delivery_review, 'has_product': False},
}
def _sync_pending(context: ContextTypes.DEFAULT_TYPE):
    """Копирует pending_data → pending_review, чтобы превью всегда был свежим."""
    pd = context.user_data.get('pending_data', {})
    context.user_data['pending_review'] = pd.copy()


# ========================================
# WebApp helpers & API
# ========================================
def _bad_request(message: str) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=400)


def _validate_payload(payload_raw: str) -> tuple[dict, str | None]:
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
        "text": text,
        "photo_path": None,
        "raw_payload": payload_raw,
        "tg_username": tg_username,
    }
    return review, None


async def _save_photo(part) -> tuple[str | None, str | None]:
    """Сохраняет фото из multipart-части. Возвращает (path, error)."""
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
    c.execute(
        '''
        INSERT INTO web_reviews (created_at, user_id, category, name_mode, display_name, tea_title, rating, liked_most, text, photo_path, raw_payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            review["created_at"],
            review["user_id"],
            review["category"],
            review["name_mode"],
            review["display_name"],
            review["tea_title"],
            review["rating"],
            review["liked_most"],
            review["text"],
            review["photo_path"],
            review.get("raw_payload"),
        ),
    )
    review_id = c.lastrowid
    conn.commit()
    conn.close()
    return review_id


async def _send_to_channel(bot, review: dict) -> None:
    category_title = CATEGORY_TITLES.get(review["category"], review["category"])
    stars = "⭐" * int(review.get("rating") or 0)
    liked = review.get("liked_most") or "Ничего"

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
    lines.append(f"Отзыв: {review['text']}")
    lines.append("")

    tag_category = category_title.lower()
    lines.append(f"#отзыв #отзыв_{tag_category}")

    message = "\n".join(lines)

    if review.get("photo_path"):
        with open(review["photo_path"], "rb") as photo_file:
            await bot.send_photo(chat_id=CHANNEL_ID, photo=photo_file, caption=message)
    else:
        await bot.send_message(chat_id=CHANNEL_ID, text=message)


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
        logger.error(f"DB insert error (web_reviews): {e}")
        return web.json_response({"ok": False, "error": "Не удалось сохранить отзыв"}, status=500)

    bot = request.app.get("bot")
    try:
        await _send_to_channel(bot, review)
    except Exception as e:
        logger.error(f"Send to channel failed for web review {review_id}: {e}")
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
    user_filter = None
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
        where_clause = ""
        if user_filter is not None:
            where_clause = "WHERE user_id = ?"
            params.append(user_filter)
        params.extend([limit + 1, offset])
        c.execute(
            f"""
            SELECT * FROM web_reviews
            {where_clause}
            ORDER BY datetime(created_at) DESC
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
        logger.error(f"DB read error (web_reviews): {e}")
        return web.json_response({"ok": False, "error": "Не удалось получить отзывы"}, status=500)

    has_more = len(rows) > limit
    rows = rows[:limit]
    items = []
    for row in rows:
        items.append({
            "id": row["id"],
            "created_at": row["created_at"],
            "createdAt": row["created_at"],
            "category": row["category"],
            "rating": row["rating"],
            "display_name": row["display_name"],
            "displayName": row["display_name"],
            "tea_title": row["tea_title"],
            "teaTitle": row["tea_title"],
            "liked_most": row["liked_most"] or "Ничего",
            "likedMost": row["liked_most"] or "Ничего",
            "text": row["text"],
        })

    return web.json_response({"items": items, "hasMore": has_more})


async def serve_index(request: web.Request) -> web.Response:
    return web.FileResponse(path=os.path.join(BASE_DIR, "index.html"))


async def serve_promo(request: web.Request) -> web.Response:
    promo_path = os.path.join(BASE_DIR, "promo.json")
    if not os.path.exists(promo_path):
        return web.json_response({"error": "promo.json not found"}, status=404)
    try:
        with open(promo_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return web.json_response(data)
    except Exception as e:
        logger.error(f"Promo file read error: {e}")
        return web.json_response({"error": "promo.json invalid"}, status=500)


def build_web_app() -> web.Application:
    app = web.Application()
    app["bot"] = telegram.Bot(BOT_TOKEN)
    app.router.add_get("/", serve_index)
    app.router.add_get("/webapp", serve_index)
    app.router.add_get("/promo.json", serve_promo)
    app.router.add_post("/api/review", api_create_review)
    app.router.add_get("/api/reviews", api_get_reviews)
    app.router.add_static("/uploads/", path=UPLOAD_DIR, show_index=False)
    return app


def start_web_server_background():
    """Запускает aiohttp WebApp в отдельном потоке, чтобы не мешать polling."""
    def _run():
        asyncio.set_event_loop(asyncio.new_event_loop())
        web.run_app(build_web_app(), host=WEBAPP_HOST, port=WEBAPP_PORT, handle_signals=False)

    thread = threading.Thread(target=_run, name="webapp-server", daemon=True)
    thread.start()
    logger.info(f"WebApp сервер запущен на http://{WEBAPP_HOST}:{WEBAPP_PORT}")
    return thread


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """На /start просто отдаем ссылку на WebApp."""
    await send_webapp_link(update, context)


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Help теперь просто отдает ссылку на WebApp."""
    await send_webapp_link(update, context)


async def restart_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Resets conversation data and shows the main menu."""
    query = getattr(update, "callback_query", None)
    if query:
        await query.answer()
    context.user_data.clear()
    await send_webapp_link(update, context)
    return ConversationHandler.END


async def start_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the review process by asking for name."""
    if update.message.chat.type != "private":
        await update.message.reply_text("Please use this bot in a private chat.")
        return ConversationHandler.END
    # Initialize reviews list
    context.user_data['reviews'] = []
    user = update.message.from_user
    nickname = f"@{user.username}" if user.username else user.first_name
    keyboard = [
        [
            InlineKeyboardButton(f"Использовать {nickname}", callback_data="use_username"),
            InlineKeyboardButton("Анонимно", callback_data="anonymous"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Как к вам обращаться? (Можете написать свое имя)", reply_markup=reply_markup)
    return States.NAME
async def history_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles 'История' button, shows user's past reviews from DB in oldest-to-newest order."""
    if update.message.chat.type != "private":
        await update.message.reply_text("Please use this bot in a private chat.")
        return ConversationHandler.END
    user_id = update.effective_user.id
    logger.info(f"History called for user {user_id}")  # <<< DEBUG LOG
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT * FROM reviews WHERE user_id = ? AND is_deleted = 0 ORDER BY timestamp ASC', (user_id,))
    reviews = c.fetchall()
    conn.close()
    logger.info(f"Found {len(reviews)} reviews for user {user_id}")  # <<< <<< DEBUG LOG
    if not reviews:
        await update.message.reply_text("У вас пока нет отзывов.")
        return ConversationHandler.END
    await update.message.reply_text(f"Ваши прошлые отзывы (от старых к новым, всего {len(reviews)}):")
    # <<< PAGINATION: Show first 5, then button for more
    for i, review in enumerate(reviews[:5], 1):  # Limit to 5
        review_data = {
            'category': review[2],
            'product': review[3] or '',
            'photo': review[4],
            'rating': review[5],
            'likes': json.loads(review[6]),
            'review_text': review[7],
            'user_name': review[9] if review[8] == 0 else None  # <<< FIXED: From DB, index 9=user_name, 8=is_anonymous
        }
        format_func = CATEGORY_ROUTING[review_data['category']]['format']
        formatted = format_func(review_data)
        preview = f"{review_data['category'].capitalize()}: {review_data.get('product', '') or review_data['review_text'][:20]}... (Автор: {review_data['user_name'] or 'Анонимно'})"
        timestamp = review[10]  # <<< FIXED: timestamp now index 10
        if review_data['photo']:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=review_data['photo'],
                caption=f"Отзыв {i} ({timestamp}): {preview}\n\n{formatted}",
                parse_mode="HTML"
            )
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"Отзыв {i} ({timestamp}): {preview}\n\n{formatted}",
                parse_mode="HTML"
            )
    if len(reviews) > 5:
        keyboard = [[InlineKeyboardButton("Показать больше", callback_data="show_more_history")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Показаны первые 5. Нажмите для остальных.", reply_markup=reply_markup)
    await update.message.reply_text("Это ваши прошлые отзывы. Хотите оставить новый?", reply_markup=main_menu_keyboard())
    return ConversationHandler.END
# <<< NEW: Handler for "Show more" button (add to conv_handler states if needed)
async def show_more_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    # Logic to show next 5, etc. (implement pagination fully if needed)
    await query.edit_message_text("Покажи больше — реализуй пагинацию здесь (offset в user_data).")
    return ConversationHandler.END
async def name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles name selection or input."""
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == 'use_username':
        user = query.from_user
        user_name = f"@{user.username}" if user.username else user.first_name
        context.user_data['user_name'] = user_name
    elif data == 'anonymous':
        context.user_data['user_name'] = None
    # Proceed to category selection
    keyboard = [
        [
            InlineKeyboardButton("ЧАЙ", callback_data="чай"),
            InlineKeyboardButton("СЕРВИС", callback_data="сервис"),
            InlineKeyboardButton("ДОСТАВКА", callback_data="доставка"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.reply_text("На что пишем отзыв?", reply_markup=reply_markup)
    return States.CATEGORY
async def name_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles custom name text input."""
    context.user_data['user_name'] = update.message.text
    # Proceed to category
    keyboard = [
        [
            InlineKeyboardButton("ЧАЙ", callback_data="чай"),
            InlineKeyboardButton("СЕРВИС", callback_data="сервис"),
            InlineKeyboardButton("ДОСТАВКА", callback_data="доставка"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("На что пишем отзыв?", reply_markup=reply_markup)
    return States.CATEGORY
async def edit_person_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles editing person/name, shows selection step."""
    query = update.callback_query
    await query.answer()
    user = query.from_user
    nickname = f"@{user.username}" if user.username else user.first_name
    keyboard = [
        [
            InlineKeyboardButton(f"Использовать {nickname}", callback_data="use_username_edit"),
            InlineKeyboardButton("Анонимно", callback_data="anonymous_edit"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Измените, как к вам обращаться? (Можете написать свое имя)", reply_markup=reply_markup)
    return States.EDIT_PERSON
async def edit_person_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles callback for edited name."""
    query = update.callback_query
    await query.answer()
    data = query.data
    pending_data = context.user_data.get('pending_data', {})
    if data == 'use_username_edit':
        user = query.from_user
        user_name = f"@{user.username}" if user.username else user.first_name
        pending_data['user_name'] = user_name
    elif data == 'anonymous_edit':
        pending_data['user_name'] = None
    context.user_data['pending_data'] = pending_data
    _sync_pending(context)
    category = pending_data['category']
    format_func = CATEGORY_ROUTING[category]['format']
    formatted = format_func(pending_data)
    keyboard = [
        [
            InlineKeyboardButton("Подтвердить", callback_data="confirm"),
            InlineKeyboardButton("Изменить", callback_data="edit"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(f"Вот ваш обновленный комментарий:\n\n{formatted}", reply_markup=reply_markup, parse_mode="HTML")
    context.user_data.pop('editing', None)
    return States.PREVIEW
async def edit_person_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles text input for edited name."""
    pending_data = context.user_data.get('pending_data', {})
    pending_data['user_name'] = update.message.text
    context.user_data['pending_data'] = pending_data
    _sync_pending(context)
    category = pending_data['category']
    format_func = CATEGORY_ROUTING[category]['format']
    formatted = format_func(pending_data)
    keyboard = [
        [
            InlineKeyboardButton("Подтвердить", callback_data="confirm"),
            InlineKeyboardButton("Изменить", callback_data="edit"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Вот ваш обновленный комментарий:\n\n{formatted}", reply_markup=reply_markup, parse_mode="HTML")
    context.user_data.pop('editing', None)
    return States.PREVIEW
async def category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles category selection and routes using dict."""
    query = update.callback_query
    await query.answer()
    selected_category = query.data
    context.user_data['current_category'] = selected_category
    routing = CATEGORY_ROUTING.get(selected_category, {})
    if not routing:
        await query.edit_message_text("Unknown category.")
        return ConversationHandler.END
    if routing['has_product']:
        await query.edit_message_text(f"Выбранная категория: {selected_category.upper()} \n\nВведите название продукта:")
        return States.TEA_PRODUCT
    else:
        await query.edit_message_text(f"Выбранная категория: {selected_category.capitalize()}")
        if routing.get('likes_state'):
            if selected_category == 'сервис':
                await service_likes(query, context)
            elif selected_category == 'доставка':
                await delivery_likes(query, context)
        return routing['likes_state']
async def more_reviews(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles 'more reviews' selection."""
    query = update.callback_query
    await query.answer()
    if query.data == 'more_yes':
        keyboard = [
            [
                InlineKeyboardButton("ЧАЙ", callback_data="чай"),
                InlineKeyboardButton("СЕРВИС", callback_data="сервис"),
                InlineKeyboardButton("ДОСТАВКА", callback_data="доставка"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("На что пишем отзыв?", reply_markup=reply_markup)
        return States.CATEGORY
    elif query.data == 'more_no':
        keyboard = [
            [InlineKeyboardButton("Опубликовать", callback_data="publish")],
            [
                InlineKeyboardButton("Удалить конкретный пост!", callback_data="delete_specific"),
                InlineKeyboardButton("Удалить всё!", callback_data="delete_all")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Подтвердите публикацию ваших отзывов:", reply_markup=reply_markup)
        return States.FINAL_CONFIRM
async def final_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles final confirmation buttons for posting reviews."""
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == 'publish':
        await post_reviews(update, context)
        await query.edit_message_text("Отзывы опубликованы! Спасибо!")
        promo_text = ""
        try:
            with open('promo.json', 'r', encoding='utf-8') as f:
                promo = json.load(f)
                promo_text = f"{promo.get('text', '')} Промокод: {promo.get('code', '')}"
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Promo JSON error: {e}")
        if promo_text:
            await query.message.reply_text(promo_text)
        context.user_data.clear()
        return ConversationHandler.END
    elif data == 'delete_specific':
        reviews = context.user_data.get('reviews', [])
        if not reviews:
            await query.edit_message_text("Нет отзывов для удаления в текущей сессии.")
            return States.FINAL_CONFIRM
        await query.message.reply_text("Ваши отзывы в текущей сессии:")
        for i, review in enumerate(reviews, 1):
            format_func = CATEGORY_ROUTING[review['category']]['format']
            formatted = format_func(review)
            if review.get('photo'):
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=review['photo'],
                    caption=f"Отзыв №{i}:\n\n{formatted}",
                    parse_mode="HTML"
                )
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"Отзыв №{i}:\n\n{formatted}",
                    parse_mode="HTML"
                )
        keyboard = []
        for i in range(len(reviews)):
            keyboard.append([InlineKeyboardButton(f"Отзыв №{i+1}", callback_data=f"delete_review_{i}")])
        keyboard.append([InlineKeyboardButton("Назад", callback_data="back_to_confirm")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("Выберите отзыв для удаления:", reply_markup=reply_markup)
        return States.DELETE_SPECIFIC
    elif data == 'delete_all':
        context.user_data['reviews'] = []
        await query.edit_message_text("Все отзывы удалены.")
        await query.message.reply_text("Приветствую! Выберите действие:", reply_markup=main_menu_keyboard())
        return ConversationHandler.END
async def delete_specific_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles selection of a specific review to delete."""
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "back_to_confirm":
        keyboard = [
            [InlineKeyboardButton("Опубликовать", callback_data="publish")],
            [
                InlineKeyboardButton("Удалить конкретный пост!", callback_data="delete_specific"),
                InlineKeyboardButton("Удалить всё!", callback_data="delete_all")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Подтвердите публикацию ваших отзывов:", reply_markup=reply_markup)
        return States.FINAL_CONFIRM
    if data.startswith("delete_review_"):
        index = int(data.split("_")[-1])
        reviews = context.user_data.get('reviews', [])
        if 0 <= index < len(reviews):
            deleted_review = reviews.pop(index)
            await query.edit_message_text(f"Отзыв №{index + 1} удален.")
        else:
            await query.edit_message_text("Ошибка: Отзыв не найден.")
        keyboard = [
            [InlineKeyboardButton("Опубликовать", callback_data="publish")],
            [
                InlineKeyboardButton("Удалить конкретный пост!", callback_data="delete_specific"),
                InlineKeyboardButton("Удалить всё!", callback_data="delete_all")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("Подтвердите публикацию ваших отзывов:", reply_markup=reply_markup)
        return States.FINAL_CONFIRM
async def preview_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles preview confirm/edit."""
    query = update.callback_query
    await query.answer()
    if query.data == 'confirm':
        pending_review = context.user_data.get('pending_data', {}).copy()
        if pending_review:
            context.user_data['reviews'].append(pending_review)
            context.user_data.pop('pending_review', None)
            context.user_data.pop('pending_data', None)
        keyboard = [
            [InlineKeyboardButton("Да", callback_data="more_yes")],
            [InlineKeyboardButton("Нет", callback_data="more_no")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Хотите оставить ещё один отзыв?", reply_markup=reply_markup)
        return States.MORE_REVIEWS
    elif query.data == 'edit':
        category = context.user_data.get('pending_data', {}).get('category', '')
        has_product = CATEGORY_ROUTING.get(category, {}).get('has_product', False)
        keyboard = [[InlineKeyboardButton("Лицо", callback_data="edit_person")]]
        if has_product:
            keyboard.append([InlineKeyboardButton("Продукт", callback_data="edit_product")])
        keyboard += [
            [InlineKeyboardButton("Рейтинг", callback_data="edit_rating")],
            [InlineKeyboardButton("Лучшие моменты", callback_data="edit_likes")],
            [InlineKeyboardButton("Отзыв", callback_data="edit_review")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Что хотите изменить?", reply_markup=reply_markup)
        return States.EDIT_MENU
async def edit_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles edit menu selection."""
    query = update.callback_query
    await query.answer()
    data = query.data
    context.user_data['editing'] = True
    if data == 'edit_person':
        return await edit_person_handler(update, context)
    elif data == 'edit_rating':
        reply_markup = get_rating_keyboard()
        await query.edit_message_text("Измените рейтинг:", reply_markup=reply_markup)
        return States.EDIT_RATING
    elif data == 'edit_likes':
        category = context.user_data.get('pending_data', {}).get('category', '')
        selected = set(context.user_data.get('pending_data', {}).get('likes', []))
        context.user_data['current_likes'] = selected
        if category == 'чай':
            options = {'taste': 'ВКУС', 'aroma': 'АРОМАТ', 'feeling': 'ОЩУЩЕНИЕ ОТ ЧАЯ'}
            all_selected = len(selected) == len(options)
            keyboard = [
                [
                    InlineKeyboardButton(f"{'✅ ' if 'taste' in selected else ''}{options['taste']}", callback_data="likes_taste"),
                    InlineKeyboardButton(f"{'✅ ' if 'aroma' in selected else ''}{options['aroma']}", callback_data="likes_aroma"),
                ],
                [
                    InlineKeyboardButton(f"{'✅ ' if 'feeling' in selected else ''}{options['feeling']}", callback_data="likes_feeling"),
                    InlineKeyboardButton(f"{'✅ ' if all_selected else ''}ВСЁ", callback_data="likes_all"),
                ],
                [InlineKeyboardButton("Готово", callback_data="likes_done")],
            ]
        elif category == 'сервис':
            options = {'quality': 'КАЧЕСТВО', 'speed': 'СКОРОСТЬ', 'professionalism': 'ПРОФЕССИОНАЛИЗМ'}
            all_selected = len(selected) == len(options)
            keyboard = [
                [
                    InlineKeyboardButton(f"{'✅ ' if 'quality' in selected else ''}{options['quality']}", callback_data="likes_quality"),
                    InlineKeyboardButton(f"{'✅ ' if 'speed' in selected else ''}{options['speed']}", callback_data="likes_speed"),
                ],
                [
                    InlineKeyboardButton(f"{'✅ ' if 'professionalism' in selected else ''}{options['professionalism']}", callback_data="likes_professionalism"),
                    InlineKeyboardButton(f"{'✅ ' if all_selected else ''}ВСЁ", callback_data="likes_all"),
                ],
                [InlineKeyboardButton("Готово", callback_data="likes_done")],
            ]
        elif category == 'доставка':
            options = {'speed': 'СКОРОСТЬ', 'cost': 'СТОИМОСТЬ', 'courier': 'КУРЬЕР - 🔥'}
            all_selected = len(selected) == len(options)
            keyboard = [
                [
                    InlineKeyboardButton(f"{'✅ ' if 'speed' in selected else ''}{options['speed']}", callback_data="likes_speed"),
                    InlineKeyboardButton(f"{'✅ ' if 'cost' in selected else ''}{options['cost']}", callback_data="likes_cost"),
                ],
                [
                    InlineKeyboardButton(f"{'✅ ' if 'courier' in selected else ''}{options['courier']}", callback_data="likes_courier"),
                    InlineKeyboardButton(f"{'✅ ' if all_selected else ''}ВСЁ", callback_data="likes_all"),
                ],
                [InlineKeyboardButton("Готово", callback_data="likes_done")],
            ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Измените, что понравилось больше всего:", reply_markup=reply_markup)
        return States.EDIT_LIKES
    elif data == 'edit_product':
        await query.edit_message_text("Измените название продукта:")
        return States.EDIT_PRODUCT
    elif data == 'edit_review':
        await query.edit_message_text("Измените текст отзыва:")
        return States.EDIT_REVIEW
async def edit_rating_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles edit rating."""
    query = update.callback_query
    await query.answer()
    rating = int(query.data.split('_')[1])
    pending_data = context.user_data.get('pending_data', {})
    pending_data['rating'] = rating
    context.user_data['pending_data'] = pending_data
    _sync_pending(context)
    category = pending_data['category']
    format_func = CATEGORY_ROUTING[category]['format']
    formatted = format_func(pending_data)
    keyboard = [
        [
            InlineKeyboardButton("Подтвердить", callback_data="confirm"),
            InlineKeyboardButton("Изменить", callback_data="edit"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(f"Вот ваш обновленный комментарий:\n\n{formatted}", reply_markup=reply_markup, parse_mode="HTML")
    context.user_data.pop('editing', None)
    return States.PREVIEW
async def edit_likes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles edit likes (route to category-specific handler)."""
    category = context.user_data.get('pending_data', {}).get('category', '')
    if category == 'чай':
        result = await tea_likes(update, context)
        _sync_pending(context)  # <<< ADD: Sync after likes change
        return result
    elif category == 'сервис':
        result = await service_likes_handler(update, context)
        _sync_pending(context)  # <<< ADD
        return result
    elif category == 'доставка':
        result = await delivery_likes_handler(update, context)
        _sync_pending(context)  # <<< ADD
        return result
async def edit_product_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles edit product."""
    pending_data = context.user_data.get('pending_data', {})
    pending_data['product'] = update.message.text
    context.user_data['pending_data'] = pending_data
    _sync_pending(context)
    category = pending_data['category']
    format_func = CATEGORY_ROUTING[category]['format']
    formatted = format_func(pending_data)
    keyboard = [
        [
            InlineKeyboardButton("Подтвердить", callback_data="confirm"),
            InlineKeyboardButton("Изменить", callback_data="edit"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Вот ваш обновленный комментарий:\n\n{formatted}", reply_markup=reply_markup, parse_mode="HTML")
    context.user_data.pop('editing', None)
    return States.PREVIEW
async def edit_review_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles edit review text."""
    pending_data = context.user_data.get('pending_data', {})
    pending_data['review_text'] = update.message.text
    context.user_data['pending_data'] = pending_data
    _sync_pending(context)
    category = pending_data['category']
    format_func = CATEGORY_ROUTING[category]['format']
    formatted = format_func(pending_data)
    keyboard = [
        [
            InlineKeyboardButton("Подтвердить", callback_data="confirm"),
            InlineKeyboardButton("Изменить", callback_data="edit"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Вот ваш обновленный комментарий:\n\n{formatted}", reply_markup=reply_markup, parse_mode="HTML")
    context.user_data.pop('editing', None)
    return States.PREVIEW
async def post_reviews(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reviews = context.user_data.get('reviews', [])
    if not reviews:
        logger.warning("No reviews to post.")
        if hasattr(update, 'callback_query'):
            await update.callback_query.message.reply_text("Нет отзывов для публикации.")
        return

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
    except Exception as e:
        logger.error(f"DB connection failed: {str(e)}")
        if hasattr(update, 'callback_query'):
            await update.callback_query.message.reply_text("Ошибка: Не удалось подключиться к базе данных.")
        return

    post_success = True
    for review in reviews:
        category = review.get('category', 'unknown')
        photo = review.get('photo')
        user_name = review.get('user_name', 'None')
        try:
            if category == 'чай':
                message = format_tea_review(review)
            elif category == 'сервис':
                message = format_service_review(review)
            elif category == 'доставка':
                message = format_delivery_review(review)
            else:
                logger.error(f"Invalid category: {category}")
                message = "Неизвестная отзыва."
            max_len = 1024 if photo else 4096
            if len(message) > max_len:
                logger.error(f"Message too long for {category}: {len(message)} > {max_len} chars. Truncating...")
                message = message[:max_len - 3] + "..."

            logger.info(f"Posting review for {category}, user: {user_name}, has_photo: {bool(photo)}, len: {len(message)}")

            # Send to channel
            if photo:
                await context.bot.send_photo(chat_id=CHANNEL_ID, photo=photo, caption=message, parse_mode="HTML")
            else:
                await context.bot.send_message(chat_id=CHANNEL_ID, text=message, parse_mode="HTML")

            # DB insert (separate try to not affect send success)
            try:
                c.execute('''
                    INSERT INTO reviews (user_id, category, product, photo_id, rating, likes, review_text, is_anonymous, user_name, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    update.effective_user.id,
                    review['category'],
                    review.get('product'),
                    review.get('photo'),
                    review['rating'],
                    json.dumps(review['likes']),
                    review['review_text'],
                    1 if review.get('user_name') is None else 0,
                    review.get('user_name'),
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                ))
                conn.commit()
            except Exception as db_e:
                logger.error(f"DB insert failed for {category}: {str(db_e)}\n{traceback.format_exc()}")
                post_success = False  # Flag for user message

        except telegram.error.BadRequest as e:
            logger.error(f"BadRequest posting {category}: {str(e)} - Message: {message[:100]}...")
            post_success = False
        except telegram.error.Unauthorized as e:
            logger.error(f"Unauthorized posting {category}: {str(e)} - Check bot is channel admin!")
            post_success = False
        except Exception as e:
            logger.error(f"Unexpected error posting {category}: {str(e)}\n{traceback.format_exc()}")
            post_success = False

    conn.close()

    if not post_success and hasattr(update, 'callback_query'):
        await update.callback_query.message.reply_text("Извините, произошла ошибка при публикации одного из отзывов (проверьте логи).")
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the review process."""
    await update.message.reply_text("Отзыв отменен.")
    context.user_data.clear()
    return ConversationHandler.END
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors."""
    logger.error(f"Update {update} caused error {context.error}")
def main() -> None:
    """Run the bot."""
    start_web_server_background()
    application = Application.builder().token(BOT_TOKEN).read_timeout(30).write_timeout(30).connect_timeout(30).pool_timeout(30).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_handler))
    # Любые сообщения/колбэки возвращают ссылку на WebApp
    application.add_handler(MessageHandler(filters.ALL, send_webapp_link))
    application.add_handler(CallbackQueryHandler(send_webapp_link, pattern=".*"))
    application.add_error_handler(error_handler)
    application.run_polling(drop_pending_updates=True)
if __name__ == "__main__":
    main()
