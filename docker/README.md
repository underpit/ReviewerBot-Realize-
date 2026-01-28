# ReviewerBot_Debug контейнер

## Сборка
```bash
docker build -t reviewbot-debug:local .
```

## Обязательные переменные окружения
- `BOT_TOKEN` — токен Telegram бота
- `CHANNEL_ID` — ID канала, куда публиковать отзывы

## Рекомендуемые переменные
- `PUBLIC_BASE_URL` — публичный URL (например `https://ipuerwebhookservice.ru`)
- `WEBHOOK_PATH` — путь вебхука (по умолчанию `/webhook/bot`)
- `WEBHOOK_SECRET` — секрет для Telegram webhook (проверяется заголовок `X-Telegram-Bot-Api-Secret-Token`)
- `WEBAPP_URL` — публичный URL WebApp (например `https://ipuerwebhookservice.ru/debug`)
- `RB_REVIEWS_DB` — путь к SQLite базе (например `/app/data/reviews.db`)

## Локальный запуск
```bash
docker run --rm -p 8080:8080 \
  -e BOT_TOKEN="YOUR_TOKEN" \
  -e CHANNEL_ID="-1001234567890" \
  -e PUBLIC_BASE_URL="https://ipuerwebhookservice.ru" \
  -e WEBHOOK_PATH="/webhook/bot" \
  -e WEBAPP_URL="http://localhost:8080/debug" \
  -e RB_REVIEWS_DB="/app/data/reviews.db" \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/uploads:/app/uploads" \
  reviewbot-debug:local
```

## Healthcheck
- `GET /health` на порту `8080`
