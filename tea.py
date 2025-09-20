from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from form import format_tea_review 

# Hardcode state numbers (no import from main to avoid cycle)
TEA_PHOTO = 3
TEA_RATING = 4
TEA_LIKES = 5
TEA_REVIEW_TEXT = 6
PREVIEW = 13
MORE_REVIEWS = 1  # Hardcode for return after review

async def tea_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the product name and asks for photo (Tea category - no skip)."""
    context.user_data['current_product'] = update.message.text

    await update.message.reply_text("Прикрепите фото продукта.")
    return TEA_PHOTO

async def tea_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the photo and shows rating buttons (Tea category)."""
    if update.message.photo:
        photo_file = update.message.photo[-1].file_id
        context.user_data['current_photo'] = photo_file
        await update.message.reply_text("Фото успешно добавлено!")

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

        await update.message.reply_text("Оцените чай по шкале:", reply_markup=reply_markup)
        return TEA_RATING
    else:
        # Reprompt if not photo
        return TEA_PHOTO

async def tea_photo_reprompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Reprompts for photo if non-photo message sent."""
    await update.message.reply_text("Прикрепите фото продукта")
    return TEA_PHOTO

async def tea_rating(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles rating selection (Tea category)."""
    query = update.callback_query
    await query.answer()
    rating = int(query.data.split('_')[1])
    context.user_data['current_rating'] = rating

    await query.edit_message_text(f"Ваша оценка: {rating} ({'⭐' * rating})")

    # Show likes buttons for Tea (Russian)
    selected = set()
    context.user_data['current_likes'] = selected
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
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.reply_text("Что понравилось  больше всего? ( Выберите 1 или несколько пунктов)", reply_markup=reply_markup)
    return TEA_LIKES

async def tea_likes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles likes selection (Tea category)."""
    query = update.callback_query
    await query.answer()
    data = query.data
    selected = context.user_data['current_likes']
    options = ['taste', 'aroma', 'feeling']

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
            pending_data = context.user_data.get('pending_data', {})
            pending_data['likes'] = list(selected)
            context.user_data['pending_data'] = pending_data

            formatted = format_tea_review(pending_data)
            keyboard = [
                [
                    InlineKeyboardButton("Подтвердить", callback_data="confirm"),
                    InlineKeyboardButton("Изменить", callback_data="edit"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(f"Вот ваш комментарий, проверьте перед отправкой, в дальнейшем его нельзя будет изменить:\n\n{formatted}", reply_markup=reply_markup, parse_mode="HTML") 
            context.user_data.pop('editing', None)  
            return 13  # PREVIEW
        else:
            await query.edit_message_text("Отлично! А теперь напишите пару строк в произвольной форме", reply_markup=None)
            return 6  # TEA_REVIEW_TEXT

    # Update buttons by editing the message
    all_selected = len(selected) == len(options)
    keyboard_options = {'taste': 'ВКУС', 'aroma': 'АРОМАТ', 'feeling': 'ОЩУЩЕНИЕ О ЧАЯ'}
    keyboard = [
        [
            InlineKeyboardButton(f"{'✅ ' if 'taste' in selected else ''}{keyboard_options['taste']}", callback_data="likes_taste"),
            InlineKeyboardButton(f"{'✅ ' if 'aroma' in selected else ''}{keyboard_options['aroma']}", callback_data="likes_aroma"),
        ],
        [
            InlineKeyboardButton(f"{'✅ ' if 'feeling' in selected else ''}{keyboard_options['feeling']}", callback_data="likes_feeling"),
            InlineKeyboardButton(f"{'✅ ' if all_selected else ''}Все", callback_data="likes_all"),
        ],
        [InlineKeyboardButton("Готово", callback_data="likes_done")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.edit_message_text("Что понравилось  больше всего? ( Выберите 1 или несколько пунктов)", reply_markup=reply_markup)
    except Exception as e:
        if "Message is not modified" in str(e):
            pass  # Ignore if no change
        else:
            logging.getLogger(__name__).error(f"Error editing likes message: {e}")

    return TEA_LIKES

async def tea_review_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the review text and shows preview (Tea category)."""
    context.user_data['current_review_text'] = update.message.text

    # Prepare review data for preview
    review_data = {
        'category': context.user_data['current_category'],
        'product': context.user_data.get('current_product', ''),
        'photo': context.user_data.get('current_photo', None),
        'rating': context.user_data['current_rating'],
        'likes': list(context.user_data.get('current_likes', set())),
        'review_text': context.user_data['current_review_text'],
    }

    # Format for preview
    formatted_review = format_tea_review(review_data)

    # Show preview with buttons (plain text, no parse_mode)
    keyboard = [
        [
            InlineKeyboardButton("Подтвердить", callback_data="confirm"),
            InlineKeyboardButton("Изменить", callback_data="edit"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Paste the preview text here
    await update.message.reply_text(f"Вот ваш комментарий, проверьте перед отправкой, в дальнейшем его нельзя будет изменить:\n\n{formatted_review}", reply_markup=reply_markup, parse_mode="HTML")

    # Store pending review for confirmation
    context.user_data['pending_data'] = review_data.copy()
    context.user_data['pending_review'] = review_data.copy()

    # Clear current data (keep pending for confirm)
    context.user_data.pop('current_product', None)
    context.user_data.pop('current_photo', None)
    context.user_data.pop('current_rating', None)
    context.user_data.pop('current_likes', None)
    context.user_data.pop('current_review_text', None)

    return 13  # PREVIEW
