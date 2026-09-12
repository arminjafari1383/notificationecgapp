# Telegram Notifier — AI POLIFY

این سرویس مستقل با Python/FastAPI روی پورت **5000** اجرا می‌شود و به دیتابیس PostgreSQL همان `django-backend` وصل می‌شود.

## کاری که انجام می‌دهد

- وقتی `core_appuser.next_daily_claim_at` تمام شود، فقط یک‌بار برای همان سیکل پیام Telegram می‌فرستد و کاربر را به Mini App برمی‌گرداند تا `Claim 100 EPL` را بزند.
- Reminder مربوط به Referral را روزی یک‌بار می‌فرستد.
- اگر Level 1 هنوز Referral ندارد، پیام **1000 EPL برای اولین Referral مستقیم** می‌فرستد.
- بعد از آن، اولین Level خالی بین 2 تا 5 را پیدا می‌کند و پیام **500 EPL** می‌فرستد.
- اگر هر 5 Level حداقل یک Referral داشته باشند، Reminder روزانه Referral متوقف می‌شود.
- با SQLite داخلی (`/data/notifier.sqlite3`) جلوی ارسال تکراری گرفته می‌شود.
- این سرویس هیچ EPL اضافه نمی‌کند و هیچ موجودی را تغییر نمی‌دهد؛ محاسبه و Claim همچنان فقط در Django انجام می‌شود.

## 1) ساخت `.env`

```bash
cp .env.example .env
nano .env
```

حداقل این مقادیر را درست کنید:

```env
TELEGRAM_BOT_TOKEN=توکن_واقعی_BotFather
POSTGRES_PASSWORD=پسورد_همان_دیتابیس_Django
DOCKER_NETWORK=نام_شبکه_داکر_پروژه
```

برای دیدن network کانتینر Backend:

```bash
docker inspect django-backend --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}'
```

نام خروجی را داخل `DOCKER_NETWORK` بگذارید.

## 2) Build و اجرا

```bash
docker compose -f docker-compose.notifier.yml up -d --build
```

بررسی:

```bash
docker ps | grep telegram-notifier
curl http://127.0.0.1:5000/health
```

لاگ:

```bash
docker logs -f telegram-notifier
```

## 3) تست ارسال پیام برای یک Telegram ID

```bash
docker exec telegram-notifier python -m app.send_test TELEGRAM_ID
```

مثال:

```bash
docker exec telegram-notifier python -m app.send_test 123456789
```

## 4) Permission لازم برای پیام‌های خودکار

برای اینکه ربات بتواند بعداً به کاربر پیام خصوصی بدهد، کاربر باید اجازه ارسال پیام به Bot را داده باشد. فایل `frontend_write_access.patch` تغییر پیشنهادی برای `Timer.jsx` است که از Telegram Mini App API متد `requestWriteAccess()` را صدا می‌زند.

اگر این اجازه داده نشده باشد یا کاربر Bot را Block کرده باشد، Telegram خطای 403 می‌دهد. سرویس این خطا را ثبت می‌کند و به‌صورت مداوم اسپم Retry نمی‌کند.

## نکته درباره «آیا لینک واقعاً برای دوست ارسال شده؟»

Telegram Bot API به Backend اعلام نمی‌کند که کاربر در پنجره Share واقعاً پیام را برای یک دوست ارسال کرده یا فقط پنجره را بسته است. بنابراین این سرویس از **نتیجه واقعی Referral در دیتابیس** استفاده می‌کند: تا زمانی که Referral موردنظر در Level مربوطه ثبت نشده، Reminder ادامه دارد. این روش قابل اتکاتر از ثبت صرفاً کلیک روی دکمه Share است.

## زمان Reminder Referral

پیش‌فرض ساعت 12:00 به وقت `Asia/Tehran` است:

```env
REFERRAL_REMINDER_HOUR=12
REFERRAL_REMINDER_MINUTE=0
```

برای مثال ساعت 20:30:

```env
REFERRAL_REMINDER_HOUR=20
REFERRAL_REMINDER_MINUTE=30
```
