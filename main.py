import logging
import json
from enum import IntEnum
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

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

# Set up logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Cached bot_config (load once)
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

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /start command, shows button to start review."""
    if update.message.chat.type != "private":
        await update.message.reply_text("Please use this bot in a private chat.")
        return

    keyboard = ReplyKeyboardMarkup([["Оставить отзыв"]], resize_keyboard=True, one_time_keyboard=False)
    await update.message.reply_text("Приветствую! Нажмите на кнопку «ОСТАВИТЬ ОТЗЫВ» ниже ", reply_markup=keyboard)

async def start_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the review process by asking for category."""
    if update.message.chat.type != "private":
        await update.message.reply_text("Please use this bot in a private chat.")
        return ConversationHandler.END

    # Initialize reviews list
    context.user_data['reviews'] = []

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
        return States.TEA_PRODUCT  # Tea has product
    else:
        # No product - start likes
        await query.edit_message_text(f"Выбранная категория: {selected_category.capitalize()}")
        if routing.get('likes_state'):
            if selected_category == 'сервис':
                await service_likes(query, context)
            elif selected_category == 'доставка':
                await delivery_likes(query, context)
        return routing['likes_state']

async def preview_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles preview buttons (confirm or edit)."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == 'confirm':
        # Append from latest pending_data (includes edits)
        pending_data = context.user_data.pop('pending_data', None)
        if pending_data:
            context.user_data['reviews'].append(pending_data)
        context.user_data.pop('pending_review', None)  # Clean up old
        await query.edit_message_text("Отзыв подтвержден!")

        # Proceed to more reviews
        keyboard = [
            [
                InlineKeyboardButton("Да", callback_data="more_yes"),
                InlineKeyboardButton("Нет", callback_data="more_no"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.reply_text("Хотите оценить что-то еще?", reply_markup=reply_markup)
        return States.MORE_REVIEWS
    elif data == 'edit':
        # Restore pending_data from pending_review for edits
        pending_review = context.user_data.get('pending_review', {})
        context.user_data['pending_data'] = pending_review.copy()  # Ensure defined

        # Show edit menu based on category
        category = context.user_data.get('current_category', '')
        keyboard = []
        routing = CATEGORY_ROUTING.get(category, {})
        if routing.get('has_product', False):
            keyboard = [
                [InlineKeyboardButton("Название чая", callback_data="edit_product")],
                [InlineKeyboardButton("Оценка", callback_data="edit_rating")],
                [InlineKeyboardButton("Органолептика", callback_data="edit_likes")],
                [InlineKeyboardButton("Отзыв", callback_data="edit_review")],
            ]
        else:
            keyboard = [
                [InlineKeyboardButton("Оценка", callback_data="edit_rating")],
                [InlineKeyboardButton("Лучшие моменты", callback_data="edit_likes")],
                [InlineKeyboardButton("Отзыв", callback_data="edit_review")],
            ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Что вы хотели изменить?", reply_markup=reply_markup)
        return States.EDIT_MENU

async def edit_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles edit menu selection."""
    query = update.callback_query
    await query.answer()
    data = query.data
    category = context.user_data.get('current_category', '')

    if data == 'edit_product':
        # Only for tea
        await query.edit_message_text("Введите название продукта заново.")
        return States.EDIT_PRODUCT
    elif data == 'edit_rating':
        # Show rating buttons again
        reply_markup = get_rating_keyboard()
        await query.message.reply_text("Выберите рейтинг заново.", reply_markup=reply_markup)
        return States.EDIT_RATING
    elif data == 'edit_likes':
        # Set editing flag
        context.user_data['editing'] = True
        # Restore current_likes from pending_data
        pending_data = context.user_data.get('pending_data', {})
        context.user_data['current_likes'] = set(pending_data.get('likes', []))  # Restore likes
        # Call category likes with full update
        if category == 'чай':
            await tea_likes(update, context)  # Pass update
            return States.TEA_LIKES
        elif category == 'сервис':
            await service_likes_handler(update, context)  # Pass update
            return States.SERVICE_LIKES
        elif category == 'доставка':
            await delivery_likes_handler(update, context)  # Pass update
            return States.DELIVERY_LIKES
    elif data == 'edit_review':
        await query.edit_message_text("Напишите отзыв заново.")
        return States.EDIT_REVIEW

    return States.PREVIEW

async def edit_product_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles editing product name (Tea only)."""
    new_product = update.message.text
    pending_data = context.user_data.get('pending_data', {})
    pending_data['product'] = new_product
    context.user_data['pending_data'] = pending_data  # Save back
    await update.message.reply_text("Название продукта обновлено. Возвращаемся к предпросмотру.")

    # Rebuild preview
    category = context.user_data.get('current_category', '')
    if category == 'чай':
        formatted = format_tea_review(pending_data)
    keyboard = [
        [
            InlineKeyboardButton("Подтвердить", callback_data="confirm"),
            InlineKeyboardButton("Изменить", callback_data="edit"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Вот ваш комментарий, проверьте перед отправкой:\n\n{formatted}", reply_markup=reply_markup, parse_mode="HTML")
    return States.PREVIEW

async def edit_review_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles editing review text (common)."""
    new_review = update.message.text
    pending_data = context.user_data.get('pending_data', {})
    pending_data['review_text'] = new_review
    context.user_data['pending_data'] = pending_data  # Save back
    await update.message.reply_text("Отзыв обновлен. Возвращаемся к предпросмотру.")

    # Rebuild preview
    category = context.user_data.get('current_category', '')
    if category == 'чай':
        formatted = format_tea_review(pending_data)
    elif category == 'сервис':
        formatted = format_service_review(pending_data)
    elif category == 'доставка':
        formatted = format_delivery_review(pending_data)
    else:
        formatted = "Preview error."

    keyboard = [
        [
            InlineKeyboardButton("Подтвердить", callback_data="confirm"),
            InlineKeyboardButton("Изменить", callback_data="edit"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Вот ваш комментарий, проверьте перед отправкой:\n\n{formatted}", reply_markup=reply_markup, parse_mode="HTML")
    return States.PREVIEW

async def edit_likes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles editing likes (reuses category likes)."""
    query = update.callback_query
    await query.answer()
    category = context.user_data.get('current_category', '')
    if category == 'чай':
        await tea_likes(update, context)
        return States.TEA_LIKES
    elif category == 'сервис':
        await service_likes_handler(update, context)
        return States.SERVICE_LIKES
    elif category == 'доставка':
        await delivery_likes_handler(update, context)
        return States.DELIVERY_LIKES
    return States.PREVIEW

async def edit_rating_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles re-rating after edit."""
    query = update.callback_query
    await query.answer()
    rating = int(query.data.split('_')[1])
    pending_data = context.user_data.get('pending_data', {})
    pending_data['rating'] = rating
    context.user_data['pending_data'] = pending_data  # Save back

    await query.edit_message_text(f"Рейтинг обновлен: {rating} ({'⭐' * rating})")

    # Rebuild preview
    category = context.user_data.get('current_category', '')
    if category == 'чай':
        formatted = format_tea_review(pending_data)
    elif category == 'сервис':
        formatted = format_service_review(pending_data)
    elif category == 'доставка':
        formatted = format_delivery_review(pending_data)
    else:
        formatted = "Preview error."

    keyboard = [
        [
            InlineKeyboardButton("Подтвердить", callback_data="confirm"),
            InlineKeyboardButton("Изменить", callback_data="edit"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.reply_text(f"Вот ваш комментарий, проверьте перед отправкой:\n\n{formatted}", reply_markup=reply_markup, parse_mode="HTML")
    return States.PREVIEW

async def more_reviews(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles whether to add more reviews."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == 'more_yes':
        await query.edit_message_text("Отлично! Давайте начнем новый отзыв.")
        # Back to category selection
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
    elif data == 'more_no':
        try:
            await query.edit_message_text("Спасибо! Отправляем все отзывы в канал.")
            # Post all reviews
            await post_reviews(update, context)
            # Thank you message
            thank_msg = "Спасибо за искренний отзыв! Все отзывы публикуются в нашем канале @chayniy_ohotnik"
            await query.message.reply_text(thank_msg)
            # Separate promo message from JSON
            if PROMO_TEXT:
                await query.message.reply_text(PROMO_TEXT)
        except Exception as e:
            logger.error(f"Error in posting reviews: {e}")
            await query.message.reply_text("Извините, произошла ошибка при публикации отзывов. Попробуйте позже.")
        finally:
            # Clear data
            context.user_data.clear()
        return ConversationHandler.END

async def post_reviews(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Posts all collected reviews to the channel using category-specific formatting."""
    reviews = context.user_data.get('reviews', [])
    for review in reviews:
        category = review['category']
        photo = review.get('photo')
        # Get formatted message based on category
        if category == 'чай':
            message = format_tea_review(review)
        elif category == 'сервис':
            message = format_service_review(review)
        elif category == 'доставка':
            message = format_delivery_review(review)
        else:
            # Fallback
            message = "Неизвестная отзыва."
        try:
            if photo:
                await context.bot.send_photo(chat_id=CHANNEL_ID, photo=photo, caption=message, parse_mode="HTML")
            else:
                await context.bot.send_message(chat_id=CHANNEL_ID, text=message, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error posting review for {category}: {e}")
            # User-friendly error
            if hasattr(update, 'callback_query'):
                await update.callback_query.message.reply_text("Извините, произошла ошибка при публикации одного из отзывов.")

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
    application = Application.builder().token(BOT_TOKEN).read_timeout(30).write_timeout(30).connect_timeout(30).pool_timeout(30).build()

    # Handler for /start
    application.add_handler(CommandHandler("start", start_command))

    # Conversation handler for review collection
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("Оставить отзыв"), start_review)],
        states={
            States.CATEGORY: [CallbackQueryHandler(category)],
            # Tea states
            States.TEA_PRODUCT: [MessageHandler(filters.TEXT & ~filters.COMMAND, tea_product)],
            States.TEA_PHOTO: [
                MessageHandler(filters.PHOTO, tea_photo),
                MessageHandler(filters.ALL & ~filters.PHOTO & ~filters.COMMAND, tea_photo_reprompt),  # Reprompt for non-photo
            ],
            States.TEA_RATING: [CallbackQueryHandler(tea_rating, pattern="^rate_")],
            States.TEA_LIKES: [CallbackQueryHandler(tea_likes, pattern="^likes_")],
            States.TEA_REVIEW_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, tea_review_text)],
            # Service states (new flow)
            States.SERVICE_LIKES: [CallbackQueryHandler(service_likes_handler, pattern="^likes_")],
            States.SERVICE_RATING: [CallbackQueryHandler(service_rating_handler, pattern="^rate_")],
            States.SERVICE_REVIEW_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, service_review_text)],
            # Delivery states (new flow)
            States.DELIVERY_LIKES: [CallbackQueryHandler(delivery_likes_handler, pattern="^likes_")],
            States.DELIVERY_RATING: [CallbackQueryHandler(delivery_rating_handler, pattern="^rate_")],
            States.DELIVERY_REVIEW_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, delivery_review_text)],
            # Common preview and edit states
            States.PREVIEW: [CallbackQueryHandler(preview_handler, pattern="^(confirm|edit)$")],
            States.EDIT_MENU: [CallbackQueryHandler(edit_menu_handler, pattern="^edit_")],
            States.EDIT_RATING: [CallbackQueryHandler(edit_rating_handler, pattern="^rate_")],
            States.EDIT_LIKES: [CallbackQueryHandler(edit_likes_handler, pattern="^likes_")],
            States.EDIT_PRODUCT: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_product_handler)],
            States.EDIT_REVIEW: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_review_handler)],
            # Common more reviews
            States.MORE_REVIEWS: [CallbackQueryHandler(more_reviews, pattern="^more_")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )

    application.add_handler(conv_handler)
    application.add_error_handler(error_handler)

    # Start the bot
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()