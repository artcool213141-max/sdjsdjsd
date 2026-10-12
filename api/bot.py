import os, re
from typing import Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Header, HTTPException, Request

try:
    from ref import BOT_TOKEN
except ImportError:
    from api.ref import BOT_TOKEN

# MANDATORY_CHANNELS и is_member берём из tasks.py, чтобы список каналов был в одном месте
try:
    from tasks import MANDATORY_CHANNELS, is_member
except ImportError:
    from api.tasks import MANDATORY_CHANNELS, is_member

router = APIRouter()

# Ссылка на твою мини-апку (страница index.html)
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://sdjsdjsd.vercel.app").rstrip("/")
# Секрет вебхука (необязательно, но желательно): тот же, что указан в setWebhook как secret_token
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
# Если 1 — каналы, где бот не может проверить подписку (не админ), пропускаются.
# Нужно для теста, пока бота не добавили в админы. Потом поставь 0.
SKIP_CHECK_ERRORS = os.environ.get("SUB_CHECK_SKIP_ERRORS", "0") == "1"


async def tg_call(method: str, payload: dict):
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=payload)
        data = r.json()
        if not data.get("ok"):
            print("tg_call error:", method, data.get("description"))
        return data
    except Exception as e:
        print("tg_call exception:", method, repr(e))
        return {"ok": False}


def clean_payload(s: str) -> str:
    # payload из /start <payload> (реферальный id); оставляем только безопасные символы
    return re.sub(r"[^A-Za-z0-9_\-]", "", s or "")[:32]


def webapp_link(payload: str) -> str:
    if not payload:
        return WEBAPP_URL
    sep = "&" if "?" in WEBAPP_URL else "?"
    return f"{WEBAPP_URL}{sep}ref={quote(payload)}"


async def missing_channels(user_id: int):
    missing = []
    for ch in MANDATORY_CHANNELS:
        m = await is_member(ch["id"], user_id)
        if m is True:
            continue
        if m is None and SKIP_CHECK_ERRORS:
            continue
        missing.append(ch)
    return missing


def sub_keyboard(missing, payload: str):
    rows = [[{"text": f"📢 {ch['name']}", "url": f"https://t.me/{ch['id'].lstrip('@')}"}]
            for ch in missing]
    rows.append([{"text": "Я подписался ✅", "callback_data": f"chk:{payload}"}])
    return {"inline_keyboard": rows}


def open_keyboard(payload: str):
    return {"inline_keyboard": [[
        {"text": "🚀 Открыть FixStars", "web_app": {"url": webapp_link(payload)}}
    ]]}


SUB_TEXT = (
    "⭐ <b>Добро пожаловать в FixStars!</b>\n\n"
    "Чтобы начать зарабатывать Stars, подпишитесь на наших спонсоров, "
    "а затем нажмите «Я подписался»."
)
OPEN_TEXT = (
    "✅ <b>Подписка подтверждена!</b>\n\n"
    "Нажмите кнопку ниже, чтобы открыть приложение и начать зарабатывать Stars ⭐"
)


async def handle_start(chat_id: int, user_id: int, payload: str):
    missing = await missing_channels(user_id)
    if missing:
        await tg_call("sendMessage", {
            "chat_id": chat_id,
            "text": SUB_TEXT,
            "parse_mode": "HTML",
            "reply_markup": sub_keyboard(missing, payload),
        })
    else:
        await tg_call("sendMessage", {
            "chat_id": chat_id,
            "text": OPEN_TEXT,
            "parse_mode": "HTML",
            "reply_markup": open_keyboard(payload),
        })


async def handle_check(cq: dict):
    user_id = cq["from"]["id"]
    payload = clean_payload((cq.get("data") or "")[4:])
    msg = cq.get("message") or {}

    missing = await missing_channels(user_id)
    if missing:
        await tg_call("answerCallbackQuery", {
            "callback_query_id": cq["id"],
            "text": "Вы подписались ещё не на все каналы!",
            "show_alert": True,
        })
        return

    await tg_call("answerCallbackQuery", {"callback_query_id": cq["id"]})
    if msg.get("chat") and msg.get("message_id"):
        await tg_call("editMessageText", {
            "chat_id": msg["chat"]["id"],
            "message_id": msg["message_id"],
            "text": OPEN_TEXT,
            "parse_mode": "HTML",
            "reply_markup": open_keyboard(payload),
        })
    else:
        await tg_call("sendMessage", {
            "chat_id": user_id,
            "text": OPEN_TEXT,
            "parse_mode": "HTML",
            "reply_markup": open_keyboard(payload),
        })


async def handle_update(update: dict):
    msg = update.get("message")
    if msg:
        text = msg.get("text") or ""
        chat = msg.get("chat") or {}
        if text.startswith("/start") and chat.get("type") == "private" and msg.get("from"):
            parts = text.split(maxsplit=1)
            payload = clean_payload(parts[1]) if len(parts) > 1 else ""
            await handle_start(chat["id"], msg["from"]["id"], payload)
        return

    cq = update.get("callback_query")
    if cq and (cq.get("data") or "").startswith("chk:"):
        await handle_check(cq)


@router.post("/bot/webhook")
async def bot_webhook(request: Request,
                      x_telegram_bot_api_secret_token: Optional[str] = Header(None)):
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(403, "forbidden")
    try:
        update = await request.json()
        await handle_update(update)
    except Exception as e:
        print("bot_webhook exception:", repr(e))
    # Всегда отвечаем 200, чтобы Telegram не слал одно и то же обновление повторно
    return {"ok": True}
