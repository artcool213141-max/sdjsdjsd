import asyncio
import os
import re
import time
from typing import Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Header, HTTPException, Request

try:
    from ref import BOT_TOKEN, supabase
except ImportError:
    from api.ref import BOT_TOKEN, supabase

try:
    from withdraw import set_status, WITHDRAW_GROUP_ID
except ImportError:
    from api.withdraw import set_status, WITHDRAW_GROUP_ID

# MANDATORY_CHANNELS и is_member берём из tasks.py, чтобы список каналов был в одном месте
try:
    from tasks import MANDATORY_CHANNELS, is_member
except ImportError:
    from api.tasks import MANDATORY_CHANNELS, is_member

# Пополнение баланса (Stars): pre_checkout_query и successful_payment
try:
    from topup import handle_update as topup_update
except ImportError:
    from api.topup import handle_update as topup_update

router = APIRouter()

# Ссылка на твою мини-апку (страница index.html)
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://sdjsdjsd.vercel.app").rstrip("/")
# Секрет вебхука (необязательно, но желательно): тот же, что указан в setWebhook как secret_token
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
# Если 1 — каналы, где бот не может проверить подписку (не админ), пропускаются.
# Нужно для теста, пока бота не добавили в админы. Потом поставь 0.
SKIP_CHECK_ERRORS = os.environ.get("SUB_CHECK_SKIP_ERRORS", "0") == "1"

# Админы, которым доступна рассылка /rass (через запятую, можно задать в переменной ADMIN_IDS)
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "7140366080").split(",")
             if x.strip().isdigit()}

# Рассылка: сообщений в секунду (лимит Telegram ~30) и максимум секунд на всю рассылку
BROADCAST_RATE = 25
BROADCAST_DEADLINE = 270


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
    rows.append([{"text": "Я подписался 🐼", "callback_data": f"chk:{payload}"}])
    return {"inline_keyboard": rows}


def open_keyboard(payload: str):
    return {"inline_keyboard": [[
        {"text": "🚀 Открыть PandaGift", "web_app": {"url": webapp_link(payload)}}
    ]]}


SUB_TEXT = (
    "<b>🐼 Решил забрать наши NFT ??..</b>\n\n"
    "Чтобы ворваться в PandaGift и начать выводить гифты, сделай простое действие: "
    "подпишись на наших спонсоров ниже и нажми <b>«Я подписался»</b> 🚀"
)

CAPTION = (
    "🐼 <b>Панда в теме! Твой вход одобрен!</b>\n\n"
    "Подписка засчитана. Жми кнопку ниже, чтобы ворваться в игру "
    "и забрать свои первые NFT!"
)

# Текст, который видит пользователь после успешной проверки подписки
OPEN_TEXT = CAPTION


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


# ===================== Рассылка (/rass) =====================
# Состояние диалога хранится в таблице admin_state (см. broadcast.sql),
# потому что на Vercel память между запросами не сохраняется.
#
# Шаги (step): await_content -> await_btn_choice -> [await_btn_text -> await_btn_url] -> confirm
#
# Сообщение админа не пересобирается, а копируется пользователям через copyMessage:
# так сохраняются форматирование, ссылки, премиум-эмодзи и картинка с подписью.

def kb(rows):
    return {"inline_keyboard": rows}


CANCEL_ROW = [{"text": "❌ Отмена", "callback_data": "rass:cancel"}]


async def say(chat_id, text: str, markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if markup:
        payload["reply_markup"] = markup
    return await tg_call("sendMessage", payload)


async def answer_cb(cq: dict, text: Optional[str] = None, alert: bool = False):
    payload = {"callback_query_id": cq["id"]}
    if text:
        payload["text"] = text
        payload["show_alert"] = alert
    await tg_call("answerCallbackQuery", payload)


async def strip_keyboard(cq: dict):
    msg = cq.get("message") or {}
    if msg.get("chat") and msg.get("message_id"):
        await tg_call("editMessageReplyMarkup", {
            "chat_id": msg["chat"]["id"],
            "message_id": msg["message_id"],
            "reply_markup": {"inline_keyboard": []},
        })


def st_get(aid: int):
    rows = supabase.table("admin_state").select("*").eq("admin_id", str(aid)).execute().data
    return rows[0] if rows else None


def st_set(aid: int, **fields):
    supabase.table("admin_state").upsert(
        {"admin_id": str(aid), **fields}, on_conflict="admin_id"
    ).execute()


def st_reset(aid: int, step: str):
    st_set(aid, step=step, src_chat_id=None, src_msg_id=None, btn_text=None, btn_url=None)


def st_clear(aid: int):
    # Возвращает удалённые строки: пустой список значит, что состояния уже нет
    return supabase.table("admin_state").delete().eq("admin_id", str(aid)).execute().data or []


def all_user_ids():
    ids, start, page = [], 0, 1000
    while True:
        rows = (supabase.table("users").select("id")
                .range(start, start + page - 1).execute().data or [])
        ids += [r["id"] for r in rows]
        if len(rows) < page:
            break
        start += page
    return ids


def normalize_url(s: str) -> Optional[str]:
    s = (s or "").strip()
    if not s or re.search(r"\s", s):
        return None
    if s.startswith("@"):
        s = "https://t.me/" + s[1:]
    elif re.match(r"^(t\.me|telegram\.me)/", s, re.I):
        s = "https://" + s
    if not s.lower().startswith(("https://", "http://", "tg://")):
        return None
    return s


def btn_markup(state: dict):
    if state.get("btn_text") and state.get("btn_url"):
        return kb([[{"text": state["btn_text"], "url": state["btn_url"]}]])
    return None


async def show_confirm(aid: int, chat_id: int, state: dict):
    markup = btn_markup(state)
    payload = {
        "chat_id": chat_id,
        "from_chat_id": state["src_chat_id"],
        "message_id": state["src_msg_id"],
    }
    if markup:
        payload["reply_markup"] = markup
    preview = await tg_call("copyMessage", payload)
    if not preview.get("ok"):
        await say(chat_id, "⚠️ Не удалось показать превью: "
                           f"{preview.get('description', 'неизвестная ошибка')}\n\n"
                           "Начните заново: /rass")
        st_clear(aid)
        return

    total = len(all_user_ids())
    st_set(aid, step="confirm")
    await say(chat_id,
              f"👆 Так будет выглядеть рассылка.\nПолучателей: <b>{total}</b>\n\nОтправить?",
              kb([[{"text": "✅ Подтвердить и отправить", "callback_data": "rass:go"}], CANCEL_ROW]))


async def admin_message(msg: dict) -> bool:
    """Обрабатывает сообщение админа. Возвращает True, если оно относится к рассылке."""
    aid = msg["from"]["id"]
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""

    if cmd == "/rass":
        st_reset(aid, "await_content")
        await say(chat_id,
                  "📣 <b>Новая рассылка</b>\n\n"
                  "Отправьте текст или картинку с подписью. "
                  "Форматирование и премиум-эмодзи сохранятся.\n\n"
                  "Отмена: /cancel",
                  kb([CANCEL_ROW]))
        return True

    if cmd == "/cancel":
        if st_clear(aid):
            await say(chat_id, "Рассылка отменена.")
            return True
        return False

    if cmd:  # любые другие команды (например /start) обрабатываются как обычно
        return False

    state = st_get(aid)
    if not state:
        return False
    step = state.get("step")

    if step == "await_content":
        if not (msg.get("text") or msg.get("photo")):
            await say(chat_id, "Поддерживается только текст или картинка с подписью. Пришлите ещё раз.")
            return True
        st_set(aid, src_chat_id=chat_id, src_msg_id=msg["message_id"], step="await_btn_choice")
        await say(chat_id, "Добавить кнопку под сообщением?",
                  kb([[{"text": "➕ Да", "callback_data": "rass:btn_yes"},
                       {"text": "Нет", "callback_data": "rass:btn_no"}],
                      CANCEL_ROW]))
        return True

    if step == "await_btn_text":
        if not text or len(text) > 64:
            await say(chat_id, "Название кнопки должно быть текстом до 64 символов. Пришлите ещё раз.")
            return True
        st_set(aid, btn_text=text, step="await_btn_url")
        await say(chat_id, "Теперь пришлите ссылку для кнопки (например https://t.me/name).",
                  kb([CANCEL_ROW]))
        return True

    if step == "await_btn_url":
        url = normalize_url(text)
        if not url:
            await say(chat_id, "Не похоже на ссылку. Она должна начинаться с https:// или t.me/. Пришлите ещё раз.")
            return True
        st_set(aid, btn_url=url)
        state["btn_url"] = url
        await show_confirm(aid, chat_id, state)
        return True

    # await_btn_choice / confirm: ждём нажатия кнопки
    await say(chat_id, "Нажмите одну из кнопок выше или отправьте /cancel.")
    return True


async def send_copy(client: httpx.AsyncClient, uid, src_chat, src_msg, markup) -> str:
    try:
        chat_id = int(uid)
    except (TypeError, ValueError):
        return "fail"
    payload = {"chat_id": chat_id, "from_chat_id": src_chat, "message_id": src_msg}
    if markup:
        payload["reply_markup"] = markup
    for _ in range(3):
        try:
            r = await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/copyMessage", json=payload)
            data = r.json()
        except Exception:
            return "fail"
        if data.get("ok"):
            return "ok"
        code = data.get("error_code")
        if code == 429:
            wait = int((data.get("parameters") or {}).get("retry_after", 1))
            await asyncio.sleep(wait + 1)
            continue
        if code == 403:
            return "blocked"
        return "fail"
    return "fail"


async def run_broadcast(aid: int, chat_id: int, cq: dict):
    # Забираем состояние атомарно: повторное нажатие или повтор вебхука рассылку не запустит
    taken = st_clear(aid)
    if not taken:
        await answer_cb(cq, "Рассылка уже запущена или отменена.", alert=True)
        return
    state = taken[0]
    await answer_cb(cq, "🚀 Рассылка запущена")
    await strip_keyboard(cq)

    ids = all_user_ids()
    markup = btn_markup(state)
    counts = {"ok": 0, "blocked": 0, "fail": 0}
    done = 0
    t0 = time.monotonic()

    await say(chat_id, f"🚀 Рассылка началась. Получателей: <b>{len(ids)}</b>.")

    async with httpx.AsyncClient(timeout=15) as client:
        for i in range(0, len(ids), BROADCAST_RATE):
            if time.monotonic() - t0 > BROADCAST_DEADLINE:
                break
            t_batch = time.monotonic()
            batch = ids[i:i + BROADCAST_RATE]
            results = await asyncio.gather(*[
                send_copy(client, uid, state["src_chat_id"], state["src_msg_id"], markup)
                for uid in batch
            ])
            for r in results:
                counts[r] += 1
            done += len(batch)
            spent = time.monotonic() - t_batch
            if spent < 1:
                await asyncio.sleep(1 - spent)

    report = (
        "✅ <b>Рассылка завершена</b>\n\n"
        f"Доставлено: <b>{counts['ok']}</b>\n"
        f"Заблокировали бота: <b>{counts['blocked']}</b>\n"
        f"Ошибок: <b>{counts['fail']}</b>"
    )
    if done < len(ids):
        report += f"\n\n⚠️ Не хватило времени: не отправлено {len(ids) - done} из {len(ids)}."
    await say(chat_id, report)


async def handle_rass_callback(cq: dict):
    uid = cq["from"]["id"]
    if uid not in ADMIN_IDS:
        await answer_cb(cq)
        return

    action = (cq.get("data") or "")[5:]
    msg = cq.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id", uid)

    if action == "cancel":
        st_clear(uid)
        await answer_cb(cq, "Отменено")
        await strip_keyboard(cq)
        await say(chat_id, "Рассылка отменена.")
        return

    state = st_get(uid)
    if not state:
        await answer_cb(cq, "Сессия устарела. Начните заново: /rass", alert=True)
        return
    step = state.get("step")

    if action == "btn_yes" and step == "await_btn_choice":
        st_set(uid, step="await_btn_text")
        await answer_cb(cq)
        await strip_keyboard(cq)
        await say(chat_id, "Пришлите название кнопки (до 64 символов).", kb([CANCEL_ROW]))
    elif action == "btn_no" and step == "await_btn_choice":
        await answer_cb(cq)
        await strip_keyboard(cq)
        await show_confirm(uid, chat_id, state)
    elif action == "go" and step == "confirm":
        await run_broadcast(uid, chat_id, cq)
    else:
        await answer_cb(cq, "Это действие уже неактуально.", alert=True)


async def handle_update(update: dict):
    # Платежи Stars: проверка перед оплатой и зачисление после неё (api/topup.py).
    # Должно стоять ПЕРВЫМ, иначе блок ниже с `return` съест successful_payment.
    if update.get("pre_checkout_query") or (update.get("message") or {}).get("successful_payment"):
        await topup_update(update)
        return

    msg = update.get("message")
    if msg:
        chat = msg.get("chat") or {}
        frm = msg.get("from") or {}
        if chat.get("type") == "private" and frm.get("id") in ADMIN_IDS:
            if await admin_message(msg):
                return

        text = msg.get("text") or ""
        if text.startswith("/start") and chat.get("type") == "private" and msg.get("from"):
            parts = text.split(maxsplit=1)
            payload = clean_payload(parts[1]) if len(parts) > 1 else ""
            await handle_start(chat["id"], msg["from"]["id"], payload)
        return

    cq = update.get("callback_query")
    if not cq:
        return
    data = cq.get("data") or ""
    if data.startswith("chk:"):
        await handle_check(cq)
    elif data.startswith("rass:"):
        await handle_rass_callback(cq)


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
