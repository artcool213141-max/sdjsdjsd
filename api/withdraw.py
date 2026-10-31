import os
import httpx
from fastapi import HTTPException

try:
    from ref import BOT_TOKEN, supabase
except ImportError:
    from api.ref import BOT_TOKEN, supabase

# Группа, куда уходят заявки
WITHDRAW_GROUP_ID = os.environ.get("WITHDRAW_GROUP_ID", "-5372143983")

ERRORS = {
    "not_found": "Предмет не найден или уже продан",
    "insufficient": "Недостаточно Stars для вывода",
    "user_not_found": "Пользователь не найден",
}


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
    # Кнопка "Вывести" открывает личку с юзером, "Выполнено" и "Отклонить" — callback
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
    async with httpx.AsyncClient(timeout=10) as c:
        resp = await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload)
    return resp.json() if resp else {"ok": False}


async def create_withdraw_request(user: dict, res: dict, nft_id: int, fee: float):
    """
    res — ответ rpc('withdraw_item') из inventory.py, должен содержать:
      request_id, name, price_stars, price_ton
    Здесь записываем заявку в БД и отправляем в группу.
    """
    request_id = res.get("request_id")
    if not request_id:
        raise HTTPException(500, "withdraw_item did not return request_id")

    row = {
        "id": int(request_id),
        "user_id": str(user["id"]),
        "username": user.get("username"),
        "first_name": user.get("first_name"),
        "nft_id": int(nft_id),
        "name": res.get("name"),
        "price_stars": float(res.get("price_stars") or 0),
        "price_ton": float(res.get("price_ton") or 0),
        "fee_stars": float(fee or 0),
        "status": "pending",
        "group_chat_id": WITHDRAW_GROUP_ID,
    }
    supabase.table("withdraw_requests").upsert(row, on_conflict="id").execute()

    sent = await notify_group(row)
    if sent.get("ok"):
        msg_id = (sent.get("result") or {}).get("message_id")
        if msg_id:
            supabase.table("withdraw_requests").update(
                {"group_message_id": msg_id}
            ).eq("id", int(request_id)).execute()

    return {"success": True, "request_id": int(request_id)}


async def set_status(req_id: int, status: str, cq: dict):
    """Вызывается из bot.py по callback wd_done/wd_reject."""
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
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": _req_text(row),
                    "parse_mode": "HTML",
                    "reply_markup": {"inline_keyboard": []},  # убираем кнопки
                },
            )
    return {"ok": True, "status": status}
