from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from form import format_service_review

# Hardcode state numbers (no import from main to avoid cycle)
SERVICE_LIKES = 7
SERVICE_RATING = 8
SERVICE_REVIEW_TEXT = 9
MORE_REVIEWS = 1  # Hardcode for return after review

async def service_likes(update_or_query, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Initial show of likes buttons for service (called after category selection)."""
    # Detect if first arg is CallbackQuery (manual call) or Update (normal handler)
    if hasattr(update_or_query, 'callback_query'):
        query = update_or_query.callback_query
    else:
        query = update_or_query  # It's the query itself from manual call

    # Answer if it's a query (safe for both)
    if hasattr(query, 'answer'):
        await query.answer()

    selected = set()
    context.user_data['current_likes'] = selected
    options = {'quality': 'КАЧЕСТВО', 'speed': 'СКОРОСТЬ', 'professionalism': 'ПРОФЕССИОНАЛИЗМ'}

    keyboard = [
        [
            InlineKeyboardButton(f"{'✅ ' if 'quality' in selected else ''}{options['quality']}", callback_data="likes_quality"),
            InlineKeyboardButton(f"{'✅ ' if 'speed' in selected else ''}{options['speed']}", callback_data="likes_speed"),
        ],
        [
            InlineKeyboardButton(f"{'✅ ' if 'professionalism' in selected else ''}{options['professionalism']}", callback_data="likes_professionalism"),
            InlineKeyboardButton("ВСЁ", callback_data="likes_all"),
        ],
        [InlineKeyboardButton("Готово", callback_data="likes_done")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text("Что понравилось больше всего?", reply_markup=reply_markup)
    return SERVICE_LIKES

async def service_likes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles likes selection callbacks for service."""
    query = update.callback_query
    await query.answer()
    data = query.data
    selected = context.user_data['current_likes']
    options = ['quality', 'speed', 'professionalism']

    if data == 'likes_all':
        if len(selected) == len(options):
            selected.clear()
        else:
            selected.update(options)
    elif data.startswith('likes_') and data != 'likes_all' and data != 'likes_done':
        like = data.split('_')[1]
        if like in selected:
            selected.remove(like)
        else:
            if like in options:
                selected.add(like)
    elif data == 'likes_done':
        editing = context.user_data.get('editing', False)
        if editing:
            # MARK: Editing mode - update pending_data and rebuild preview
            pending_data = context.user_data.get('pending_data', {})
            pending_data['likes'] = list(selected)
            context.user_data['pending_data'] = pending_data

            formatted = format_service_review(pending_data)
            keyboard = [
                [
                    InlineKeyboardButton("Подтвердить", callback_data="confirm"),
                    InlineKeyboardButton("Изменить", callback_data="edit"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(f"Вот ваш комментарий, проверьте перед отправкой, в дальнейшем его нельзя будет изменить:\n\n{formatted}", reply_markup=reply_markup, parse_mode="HTML")
            context.user_data.pop('editing', None)  # Clear flag
            return 13  # PREVIEW
        # Proceed to rating
        else: 

            # Show rating buttons
            keyboard = [
                [
                    InlineKeyboardButton("1", callback_data="rate_1"),
                    InlineKeyboardButton("2", callback_data="rate_2"),
                    InlineKeyboardButton("3", callback_data="rate_3"),
                    InlineKeyboardButton("4", callback_data="rate_4"),
                    InlineKeyboardButton("5", callback_data="rate_5"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.message.reply_text("Отлично! Оцените наш сервис по шкале", reply_markup=reply_markup)
            return SERVICE_RATING

    # Update buttons
    all_selected = len(selected) == len(options)
    keyboard_options = {'quality': 'КАЧЕСТВО', 'speed': 'СКОРОСТЬ', 'professionalism': 'ПРОФЕССИОНАЛИЗМ'}
    keyboard = [
        [
            InlineKeyboardButton(f"{'✅ ' if 'quality' in selected else ''}{keyboard_options['quality']}", callback_data="likes_quality"),
            InlineKeyboardButton(f"{'✅ ' if 'speed' in selected else ''}{keyboard_options['speed']}", callback_data="likes_speed"),
        ],
        [
            InlineKeyboardButton(f"{'✅ ' if 'professionalism' in selected else ''}{keyboard_options['professionalism']}", callback_data="likes_professionalism"),
            InlineKeyboardButton(f"{'✅ ' if all_selected else ''}ВСЁ", callback_data="likes_all"),
        ],
        [InlineKeyboardButton("Готово", callback_data="likes_done")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.edit_message_text("Что понравилось больше всего?", reply_markup=reply_markup)
    except Exception as e:
        if "Message is not modified" in str(e):
            pass
        else:
<<<<<<< HEAD
            logging.getLogger(__name__).error(f"Error editing likes message: {e}")
=======
            logger.error(f"Error editing likes message: {e}")
>>>>>>> afbe91a (Initial commit from server)

    return SERVICE_LIKES

async def service_rating_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles rating selection for service."""
    query = update.callback_query
    await query.answer()
    rating = int(query.data.split('_')[1])
    context.user_data['current_rating'] = rating

    await query.edit_message_text(f"Оценка выбрана: {rating} ({'⭐' * rating})")

    await query.message.reply_text("Отлично! А теперь напишите пару строк в произвольной форме")
    return SERVICE_REVIEW_TEXT

async def service_review_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the review text and shows preview (Service category)."""
    context.user_data['current_review_text'] = update.message.text

    # Prepare review data for preview
    review_data = {
        'category': context.user_data['current_category'],
        'product': '',  # No product for service
        'photo': None,  # No photo for service
        'rating': context.user_data['current_rating'],
        'likes': list(context.user_data.get('current_likes', [])),
        'review_text': context.user_data['current_review_text'],
<<<<<<< HEAD
=======
        'user_name': context.user_data.get('user_name', None)  # Added user_name
>>>>>>> afbe91a (Initial commit from server)
    }

    # Format for preview
    formatted_review = format_service_review(review_data)

    # Show preview with buttons
    keyboard = [
        [
            InlineKeyboardButton("Подтвердить", callback_data="confirm"),
            InlineKeyboardButton("Изменить", callback_data="edit"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(f"Вот ваш комментарий, проверьте перед отправкой, в дальнейшем его нельзя будет изменить:\n\n{formatted_review}", reply_markup=reply_markup, parse_mode="HTML")

    # Store pending review for confirmation
    context.user_data['pending_data'] = review_data.copy()
    context.user_data['pending_review'] = review_data.copy()

    # Clear current data (keep pending for confirm)
    context.user_data.pop('current_rating', None)
    context.user_data.pop('current_likes', None)
    context.user_data.pop('current_review_text', None)

    return 13 # PREVIEW