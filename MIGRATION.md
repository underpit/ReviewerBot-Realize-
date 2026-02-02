# ReviewBot Migration (DEBUG → PROD)

## 0) Facts (paths/containers)
- PROD DB: `/root/ipuer/reviewbot-webapp/data/reviews.db`
- DEBUG DB: `/root/ipuer/reviewbot-debug/data/reviews.db`
- PROD webapp: `https://ipuerwebhookservice.ru/` → `/api/*`
- DEBUG webapp: `https://ipuerwebhookservice.ru/debug/` → `/api-debug/*`

---

## 1) Database backup (mandatory)
```bash
sudo mkdir -p /root/ipuer/reviewbot-webapp/data/backups
sudo mkdir -p /root/ipuer/reviewbot-debug/data/backups

TS=$(date +%Y%m%d-%H%M%S)
sudo cp -a /root/ipuer/reviewbot-webapp/data/reviews.db \
  /root/ipuer/reviewbot-webapp/data/backups/reviews.db.$TS
sudo cp -a /root/ipuer/reviewbot-debug/data/reviews.db \
  /root/ipuer/reviewbot-debug/data/backups/reviews.db.$TS
```

## 2) Copy DEBUG DB into PROD (safe staging)
```bash
TS=$(date +%Y%m%d-%H%M%S)
sudo cp -a /root/ipuer/reviewbot-debug/data/reviews.db \
  /root/ipuer/reviewbot-webapp/data/reviews.db.debug-$TS
```

### If PROD must become DEBUG DB (most likely)
```bash
sudo cp -a /root/ipuer/reviewbot-debug/data/reviews.db \
  /root/ipuer/reviewbot-webapp/data/reviews.db
```

---

## 3) Stop using :latest (versioned tags)
Recommended tag format: `YYYY-MM-DD-NN`

### Build & push (local)
```bash
TAG=2026-01-29-01

docker buildx build --platform linux/amd64 \
  -t underpits/reviewbot-webapp:$TAG \
  -f ReviewerBot_WebApp/Dockerfile ReviewerBot_WebApp \
  --push

docker buildx build --platform linux/amd64 \
  -t underpits/reviewbot-debug:$TAG \
  -f ReviewerBot_Debug/Dockerfile ReviewerBot_Debug \
  --push
```

### Update docker-compose.yml on server
```yaml
reviewbot-webapp:
  image: underpits/reviewbot-webapp:2026-01-29-01

reviewbot-debug:
  image: underpits/reviewbot-debug:2026-01-29-01
```

### Pull + recreate
```bash
sudo docker compose pull reviewbot-webapp reviewbot-debug
sudo docker compose up -d --force-recreate reviewbot-webapp reviewbot-debug
```

---

## 4) Verification checklist
```bash
curl -I https://ipuerwebhookservice.ru/
curl -I https://ipuerwebhookservice.ru/debug/
curl -I https://ipuerwebhookservice.ru/api/reviews
curl -I https://ipuerwebhookservice.ru/api-debug/review
```

UI checks:
- PROD: submit review → goes to PROD channel.
- DEBUG: submit review → goes to TEST channel.
- DEBUG webapp shows Telegram user panel only when `?debug=1` or `/debug/`.

---

## 5) Rollback
```bash
# Restore PROD DB from backup:
sudo cp -a /root/ipuer/reviewbot-webapp/data/backups/reviews.db.YYYYMMDD-HHMMSS \
  /root/ipuer/reviewbot-webapp/data/reviews.db

# Revert image tag in docker-compose.yml and recreate
sudo docker compose up -d --force-recreate reviewbot-webapp reviewbot-debug
```
