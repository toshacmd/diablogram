# Установка на VPS — пошаговая инструкция

Рассчитано на то, что вы впервые настраиваете именно этот проект. Все команды
для Ubuntu 22.04/24.04 (рекомендуемая ОС).

## 0. Какой VPS нужен

| Масштаб | vCPU | RAM | Диск | Пример |
|---|---|---|---|---|
| До ~30 аккаунтов / 50 каналов | 2 | 4 ГБ | 40 ГБ SSD | стартовая конфигурация, достаточно с запасом |
| 30–100 аккаунтов | 4 | 8 ГБ | 60–80 ГБ SSD | если пул аккаунтов будет расти |
| 100+ аккаунтов | 4–8 | 12–16 ГБ | 100 ГБ SSD | на будущее, не для старта |

Почему так:
- Основная нагрузка — держать открытые MTProto-соединения (по одному на активный аккаунт) и писать записи в Postgres. Это не CPU-интенсивно (в основном ожидание сети), но каждое соединение +кэш сущностей Telethon съедает ощутимо RAM — ориентируйтесь на **~50–80 МБ на аккаунт** с запасом.
- Диск не хранит медиа (это вне scope сервиса) — используется только под Postgres и логи, поэтому 40 ГБ хватает надолго. SSD важен для отзывчивости Postgres при частой записи в журнал.
- Сеть: важна не столько полоса (трафик текстовый), сколько **стабильность и низкая задержка до Telegram** и до ваших прокси-серверов.

**Локация VPS:** выбирайте страну/дата-центр, откуда Telegram доступен напрямую и стабильно (Европа — Германия, Нидерланды, Финляндия и т.п. — хороший выбор). Не критично, что именно VPS «видит» Telegram напрямую своим IP — трафик каждого аккаунта, у которого прописан прокси, идёт через этот прокси, а не через IP сервера. Но сама связность сервера должна быть надёжной (это влияет на переподключения и на задержку до Postgres/бэкенда ИИ-провайдера).

**ОС:** Ubuntu 22.04 LTS или 24.04 LTS, x86_64.

## 1. Первоначальная настройка сервера

Подключитесь по SSH под root (или sudo-пользователем), затем:

```bash
apt update && apt upgrade -y

# отдельный пользователь вместо root — общая гигиена безопасности
adduser diablogram
usermod -aG sudo diablogram
su - diablogram
```

Базовый файрвол:

```bash
sudo ufw allow OpenSSH
sudo ufw enable
```

**Важно:** порт веб-панели (8000) в файрвол **не открываем** — панель без встроенной авторизации, доступ к ней нужно ограничить (см. шаг 6). Всё, что должно быть публично доступно — Postgres (5432) и порт воркера — наружу не открываются вовсе, они видны только внутри Docker-сети.

## 2. Установка Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker
docker --version
docker compose version
```

## 3. Перенос проекта на сервер

Вариант А (проще всего, если проекта ещё нет в git-репозитории на GitHub) — скопировать локальную папку на сервер через `rsync` (выполняется **с вашего Windows-компьютера**, в Git Bash / WSL):

```bash
rsync -avz --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
  "/c/Users/Администратор/Desktop/diablogram ai/" \
  diablogram@<IP_СЕРВЕРА>:/home/diablogram/app/
```

Вариант Б — если вы завели приватный репозиторий на GitHub, на сервере:

```bash
git clone <URL_РЕПОЗИТОРИЯ> /home/diablogram/app
```

Дальше все команды выполняются на сервере, в `/home/diablogram/app`.

Создайте `.env` (пока с пустыми значениями — заполним в шаге 5) и соберите образ — он понадобится и для генерации ключа шифрования (шаг 4.4), и для запуска:

```bash
cd /home/diablogram/app
cp .env.example .env
docker compose build
```

## 4. Получение всех необходимых ключей

### 4.1 Telegram API ID/Hash (один на всё приложение, используется всеми аккаунтами)

1. Зайдите на [my.telegram.org](https://my.telegram.org) под любым своим номером телефона.
2. **API development tools** → создайте приложение (название/платформа — любые, это не публичное приложение).
3. Скопируйте **App api_id** и **App api_hash**.

### 4.2 Бот для уведомлений (отдельный от аккаунтов-комментаторов!)

1. Напишите [@BotFather](https://t.me/BotFather) → `/newbot` → задайте имя и username.
2. BotFather пришлёт **токен** вида `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`.
3. Напишите этому новому боту **любое сообщение** (иначе он не сможет писать вам первым).
4. Узнайте свой numeric chat id — напишите [@userinfobot](https://t.me/userinfobot), он пришлёт ваш **Id**.

### 4.3 Ключ ИИ-провайдера

- Anthropic: [console.anthropic.com](https://console.anthropic.com) → API Keys → создать ключ.
- OpenAI: [platform.openai.com](https://platform.openai.com/api-keys) → создать ключ.

Нужен только один из двух — тот, который укажете в `AI_PROVIDER`.

### 4.4 Ключ шифрования session-строк/паролей прокси

Используем уже собранный на шаге 3 образ (в нём есть нужная библиотека `cryptography`):

```bash
docker compose run --rm web python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Скопируйте результат — это будет `SESSION_ENCRYPTION_KEY`. **Сохраните его отдельно в надёжном месте** (например, в менеджере паролей) — если он потеряется, все сохранённые session-строки и пароли прокси в базе станет невозможно расшифровать, и все аккаунты придётся добавлять заново.

## 5. Редактирование `.env`

Файл уже создан на шаге 3 (`cp .env.example .env`) — теперь его нужно заполнить реальными значениями:

```bash
cd /home/diablogram/app
nano .env
```

Что и куда вписывается (файл `.env` в корне проекта):

```ini
# Оставить как есть — это внутренний адрес Postgres из docker-compose.yml,
# менять не нужно, если вы не меняли имя/пароль пользователя БД там же.
DATABASE_URL=postgresql+asyncpg://diablogram:diablogram@db:5432/diablogram

# Из шага 4.1
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef1234567890abcdef1234567890

# Из шага 4.2
NOTIFIER_BOT_TOKEN=123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NOTIFIER_OWNER_CHAT_ID=987654321

# Из шага 4.3 — выберите провайдера и впишите соответствующий ключ
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ANTHROPIC_MODEL=claude-haiku-4-5-20251001
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini

# Из шага 4.4
SESSION_ENCRYPTION_KEY=<вставить сгенерированный ключ>

WEB_HOST=0.0.0.0
WEB_PORT=8000
```

**Важно:** `DATABASE_URL` в `.env` должен указывать на хост `db` (имя сервиса в `docker-compose.yml`), а не на `localhost` — контейнеры видят друг друга по именам сервисов внутри Docker-сети.

Обратите внимание: пароль пользователя БД в `docker-compose.yml` (`POSTGRES_PASSWORD: diablogram`) — для личного инструмента за файрволом это ок, но если хотите, замените на свой и в `docker-compose.yml`, и в `DATABASE_URL` синхронно (пароль должен совпадать в обоих местах).

## 6. Первый запуск

```bash
docker compose up -d --build
docker compose logs -f migrate    # проверить, что миграции применились без ошибок (Ctrl+C, когда контейнер завершится)
docker compose ps                  # web и worker должны быть Up
```

Проверить, что панель поднялась:

```bash
docker compose logs web --tail=50
```

## 7. Доступ к веб-панели (без открытия наружу)

У панели пока нет встроенной авторизации, поэтому **не открывайте порт 8000 в файрвол**. Самый простой и безопасный способ — SSH-туннель:

С вашего компьютера:

```bash
ssh -L 8000:localhost:8000 diablogram@<IP_СЕРВЕРА>
```

Пока туннель открыт, панель доступна в браузере на `http://localhost:8000`.

Если нужен постоянный доступ без туннеля каждый раз — поставьте nginx с Basic Auth и HTTPS (даю как отдельный, не обязательный шаг, отпишите если понадобится настроить).

## 8. Заполнение панели

Следуйте разделу «Типичный порядок настройки» в [README.md](README.md):
1. Добавьте аккаунты — session-строку для каждого получаете через `docker compose run --rm web python scripts/generate_session.py` (запросит номер телефона/код/2FA прямо в терминале).
2. Добавьте каналы.
3. Закрепите аккаунты за каналами (на странице аккаунта) — при этом сервис сам попробует вступить в канал от имени аккаунта.
4. Настройте персоны, подписи, диапазоны и контент-фильтр в «Настройках».

## 9. Обновление проекта после изменений в коде

Вариант А (rsync):
```bash
rsync -avz --exclude='.venv' --exclude='__pycache__' --exclude='.git' --exclude='.env' \
  "/c/Users/Администратор/Desktop/diablogram ai/" \
  diablogram@<IP_СЕРВЕРА>:/home/diablogram/app/
```
Вариант Б (git): `git pull` на сервере.

Затем:
```bash
docker compose up -d --build
```
Миграции применятся автоматически (сервис `migrate` в `docker-compose.yml`).

## 10. Резервное копирование

Всё важное состояние (аккаунты, сессии, подписи, журнал, настройки) — в Postgres:

```bash
docker compose exec db pg_dump -U diablogram diablogram > backup_$(date +%F).sql
```

Настройте это командой в cron (`crontab -e`), например, ежедневно в 4:00:
```
0 4 * * * cd /home/diablogram/app && docker compose exec -T db pg_dump -U diablogram diablogram > /home/diablogram/backups/backup_$(date +\%F).sql
```

## 11. Мониторинг

```bash
docker compose logs -f worker   # видно подключения аккаунтов, join каналов, публикацию комментариев
docker compose logs -f web
```

Уведомления о лимитах/банах аккаунтов приходят от бота-уведомителя прямо в Telegram (шаг 4.2) — отдельно в логи ходить для этого не нужно.
