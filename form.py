

def escape_html(text: str) -> str:
    """Escapes special characters for HTML parsing."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def format_tea_review(review_data: dict) -> str:
    """Formats review for Tea category (includes Product, Russian likes)."""
    category = review_data['category']
    product = escape_html(review_data.get('product', ''))
    rating = review_data['rating']
    likes_list = review_data['likes']
    review_text = escape_html(review_data['review_text'])
    category_tag = f"отзыв_{category}"

    # Handle likes for Tea (Russian)
    if set(likes_list) == {'taste', 'aroma', 'feeling'}:
        likes = "Всё: (Вкус, Аромат, Ощущение от чая)"
    else:
        likes_map = {'taste': 'Вкус', 'aroma': 'Аромат', 'feeling': 'Ощущение от чая'}
        likes = ', '.join([likes_map.get(l, l.capitalize()) for l in likes_list]) if likes_list else 'None'

    message = (
        f"📝 <b>Новый отзыв</b>\n\n"
        f"<b>Категория</b>: {category.capitalize()}\n"
        f"<b>Чай</b>: {product}\n"
        f"<b>Рейтинг</b>: {'⭐' * rating}\n"
        f"<b>Лучшие моменты</b>: {likes}\n"
        f"<b>Отзыв</b>: {review_text}\n\n"
        f"#отзыв #{category_tag}"
    )
    return message

def format_service_review(review_data: dict) -> str:
    """Formats review for Service category (no Product line)."""
    category = review_data['category']
    rating = review_data['rating']
    likes_list = review_data['likes']
    review_text = escape_html(review_data['review_text'])
    category_tag = f"отзыв_{category}"

    # Handle likes for Service (Russian)
    if set(likes_list) == {'quality', 'speed', 'professionalism'}:
        likes = "Всё (Качество сервиса, Скорость обслуживания, Профессионализм)"
    else:
        likes_map = {'quality': 'Качество сервиса', 'speed': 'Скорость обслуживания', 'professionalism': 'Профессионализм'}
        likes = ', '.join([likes_map.get(l, l.capitalize()) for l in likes_list]) if likes_list else 'None'

    message = (
        f"📝 <b>Новый отзыв</b>\n\n"
        f"<b>Категория</b>: {category.capitalize()}\n"
        f"<b>Рeйтинг</b>: {'⭐' * rating}\n"
        f"<b>Лучшие моменты</b>: {likes}\n"
        f"<b>Отзыв</b>: {review_text}\n\n"
        f"#отзыв #{category_tag}"
    )
    return message

def format_delivery_review(review_data: dict) -> str:
    """Formats review for Delivery category (no Product; Russian likes)."""
    category = review_data['category']
    rating = review_data['rating']
    likes_list = review_data['likes']
    review_text = escape_html(review_data['review_text'])
    category_tag = f"отзыв_{category}"

    # Handle likes for Delivery (Russian)
    if set(likes_list) == {'speed', 'cost', 'courier'}:
        likes = "Всё: (Скорость, Стоимость, Курьер - 🔥)"
    else:
        likes_map = {'speed': 'Скорость', 'cost': 'Стоимость', 'courier': 'Курьер - 🔥'}
        likes = ', '.join([likes_map.get(l, l.capitalize()) for l in likes_list]) if likes_list else 'None'

    message = (
        f"📝 <b>Новый отзыв</b>\n\n"
        f"<b>Категория</b>: {category.capitalize()}\n"
        f"<b>Рeйтинг</b>: {'⭐' * rating}\n"
        f"<b>Лучшие моменты</b>: {likes}\n"
        f"<b>Отзыв</b>: {review_text}\n\n"
        f"#отзыв #{category_tag}"
    )
    return message