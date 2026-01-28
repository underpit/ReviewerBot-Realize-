# ReviewerBot_WebApp контейнер

## Сборка
```bash
docker build -t reviewbot-webapp:local .
```

## Обязательные переменные окружения
- `BOT_TOKEN` — токен Telegram бота
- `CHANNEL_ID` — ID канала, куда публиковать отзывы

## Рекомендуемые переменные
- `WEBAPP_URL` — публичный URL WebApp (например `https://ipuerwebhookservice.ru/`)
- `WEBAPP_PORT` — порт HTTP сервера (по умолчанию `8080` в контейнере)
- `WEBAPP_HOST` — bind host (по умолчанию `0.0.0.0`)
- `RB_REVIEWS_DB` — путь к SQLite базе (например `/app/data/reviews.db`)

## Локальный запуск
```bash
docker run --rm -p 8080:8080 \
  -e BOT_TOKEN="YOUR_TOKEN" \
  -e CHANNEL_ID="-1001234567890" \
  -e WEBAPP_URL="http://localhost:8080/" \
  -e RB_REVIEWS_DB="/app/data/reviews.db" \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/uploads:/app/uploads" \
  reviewbot-webapp:local
```

## Healthcheck
- `GET /health` на порту `8080`
