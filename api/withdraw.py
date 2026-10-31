import os
import httpx
from fastapi import HTTPException, APIRouter

try:
    from ref import BOT_TOKEN, supabase
except ImportError:
    from api.ref import BOT_TOKEN, supabase

# Создаем роутер, чтобы index.py мог его подключить
router = APIRouter()

# Группа, куда уходят заявки
WITHDRAW_GROUP_ID = os.environ.get("WITHDRAW_GROUP_ID", "-5372143983")


def _req_row(req_id: int):
    rows = supabase.table("withdraw_requests").select("*").eq("id", req_id).limit(1).execute().data
    return rows[0] if rows else None


def _req_text(r: dict) -> str:
    return (
        "📤 <b>Заявка на вывод NFT</b>\n\n"
        f"🆔 Заявка: <b>#{r['id']}</b>\n"
        f"👤 Юзер: <b>{r.get('first_name') or 'Без имени'}</b> (@{r.get('username') or 'нет'})\n"
        f"🔗 Telegram ID: <code>{r['user_id']}</code>\n"
        f"🎁 Предмет: <b>{r.get('name') or '—'}</b>\n"
        f"⭐ Цена в Stars: <b>{r.get('price_stars') or 0}</b>\n"
        f"💎 Цена в TON: <b>{r.get('price_ton') or 0}</b>\n"
        f"📋 Статус: <b>{'Ожидание' if r['status']=='pending' else ('Выведено' if r['status']=='done' else 'Отклонено')}</b>"
    )


def _kb(r: dict):
    return {"inline_keyboard": [[
        {"text": "💬 Вывести", "url": f"tg://user?id={r['user_id']}"},
        {"text": "✅ Выполнено", "callback_data": f"wd_done:{r['id']}"},
        {"text": "❌ Отклонить", "callback_data": f"wd_reject:{r['id']}"},
    ]]}


async def notify_group(r: dict, reply_markup=None) -> dict:
    payload = {
        "chat_id": r.get("group_chat_id") or WITHDRAW_GROUP_ID,
        "text": _req_text(r),
        "parse_mode": "HTML",
        "reply_markup": reply_markup or _kb(r),
    }
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload)
            data = resp.json()
            if not data.get("ok"):
                print("TG ERROR ПРИ ОТПРАВКЕ В ГРУППУ:", data)
            return data
    except Exception as e:
        print("notify_group exception:", repr(e))
        return {"ok": False}


async def create_withdraw_request(user: dict, res: dict, nft_id: int, fee: float):
    request_id = res.get("request_id")
    if not request_id:
        raise HTTPException(500, "withdraw_item did not return request_id")

    # Мы НЕ делаем insert/upsert, потому что база уже создала заявку.
    # Мы просто берем её из базы.
    row = _req_row(int(request_id))
    
    if not row:
        raise HTTPException(500, "Заявка не найдена в базе после создания")

    # Отправляем в группу
    sent = await notify_group(row)
    if sent.get("ok"):
        msg_id = (sent.get("result") or {}).get("message_id")
        if msg_id:
            supabase.table("withdraw_requests").update(
                {"group_message_id": msg_id}
            ).eq("id", int(request_id)).execute()

    return {"success": True, "request_id": int(request_id)}


async def set_status(req_id: int, status: str, cq: dict):
    if status not in ("done", "rejected"):
        return {"ok": False}
    row = _req_row(req_id)
    if not row:
        return {"ok": False, "description": "Заявка не найдена"}

    supabase.table("withdraw_requests").update(
        {"status": status, "updated_at": "now()"}
    ).eq("id", req_id).execute()
    row["status"] = status

    msg = cq.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    message_id = msg.get("message_id")
    if chat_id and message_id:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                await c.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
                    json={
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "text": _req_text(row),
                        "parse_mode": "HTML",
                        "reply_markup": {"inline_keyboard": []},
                    },
                )
        except Exception as e:
            print("editMessageText error:", repr(e))
    return {"ok": True, "status": status}
