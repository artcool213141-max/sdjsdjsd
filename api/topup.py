"""
Пополнение баланса FixStars: Telegram Stars (инвойс) + TON (TonConnect).
Роутер FastAPI под вашу структуру (supabase, api/*.py).

Эндпоинты:
  POST /topup/stars_invoice  {init_data, amount}  -> {link, id}
  POST /topup/ton_create     {init_data, amount}  -> {id, address, nano, payload}
  POST /topup/ton_check      {init_data, id}      -> {status}

Из api/bot.py (вебхук) вызывается:  await handle_update(update)

Переменные окружения: TON_WALLET, TON_PER_STAR (по умолчанию 150),
TONCENTER_KEY (необязательно). BOT_TOKEN и supabase берутся из api/ref.py.
"""
import os
import hmac
import json
import time
import base64
import hashlib
import secrets
from datetime import datetime, timezone
from urllib.parse import parse_qsl

import httpx
from fastapi import APIRouter, HTTPException

# BOT_TOKEN и supabase берём оттуда же, откуда их берут bot.py и tasks.py
try:
    from ref import BOT_TOKEN, supabase
except ImportError:
    from api.ref import BOT_TOKEN, supabase

router = APIRouter()

TON_WALLET = os.environ.get("TON_WALLET", "")
TONCENTER_KEY = os.environ.get("TONCENTER_KEY", "")
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ---------- проверка подписи Telegram WebApp ----------
def verify_init_data(init_data: str):
    try:
        data = dict(parse_qsl(init_data, strict_parsing=True))
        got = data.pop("hash")
        check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, got):
            return None
        if time.time() - int(data.get("auth_date", 0)) > 86400:
            return None
        return json.loads(data["user"])
    except Exception:
        return None


def auth(req: dict) -> dict:
    u = verify_init_data(req.get("init_data", ""))
    if not u:
        raise HTTPException(status_code=401, detail="bad init_data")
    if supabase is None:
        raise HTTPException(status_code=500, detail="supabase env missing")
    return u


# ---------- Stars ----------
@router.post("/topup/stars_invoice")
async def stars_invoice(req: dict):
    u = auth(req)
    try:
        amount = int(req.get("amount"))
    except Exception:
        raise HTTPException(status_code=400, detail="bad amount")
    if not 1 <= amount <= 10000:
        raise HTTPException(status_code=400, detail="amount 1..10000")

    topup_id = secrets.token_hex(8)
    supabase.table("topups").insert({
        "id": topup_id, "user_id": str(u["id"]), "method": "stars",
        "amount": amount, "credited": amount, "status": "pending",
    }).execute()

    async with httpx.AsyncClient(timeout=15) as c:
        r = (await c.post(f"{TG_API}/createInvoiceLink", json={
            "title": "Пополнение PandaGift",
            "description": f"Пополнение баланса на {amount} Stars",
            "payload": topup_id,
            "provider_token": "",
            "currency": "XTR",
            "prices": [{"label": "Stars", "amount": amount}],
        })).json()
    if not r.get("ok"):
        raise HTTPException(status_code=500, detail=r.get("description", "telegram error"))
    return {"link": r["result"], "id": topup_id}


async def handle_update(update: dict):
    """Вызывается из api/bot.py для pre_checkout_query и successful_payment."""
    if supabase is None:
        return

    # 1) Telegram спрашивает, можно ли принять платёж (ответить надо за 10 сек)
    pcq = update.get("pre_checkout_query")
    if pcq:
        res = supabase.table("topups").select("status").eq("id", pcq.get("invoice_payload", "")).execute()
        ok = bool(res.data and res.data[0]["status"] == "pending")
        payload = {"pre_checkout_query_id": pcq["id"], "ok": ok}
        if not ok:
            payload["error_message"] = "Счёт недействителен"
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(f"{TG_API}/answerPreCheckoutQuery", json=payload)
        return

    # 2) Оплата прошла: запись в БД + начисление баланса (атомарно, один раз)
    sp = (update.get("message") or {}).get("successful_payment")
    if sp and sp.get("currency") == "XTR":
        supabase.rpc("credit_topup", {
            "p_id": sp["invoice_payload"],
            "p_charge": sp["telegram_payment_charge_id"],
        }).execute()


# ---------- TON ----------
def comment_payload(text: str) -> str:
    """BOC (base64) одной ячейки с текстовым комментарием: 32 нулевых бита + utf8."""
    data = b"\x00\x00\x00\x00" + text.encode()
    cell = bytes([0, len(data) * 2]) + data                # d1=0 (нет ссылок), d2=2*len
    boc = (b"\xb5\xee\x9c\x72"                              # magic
           + bytes([0x01, 0x01, 0x01, 0x01, 0x00,          # size=1, off=1, cells=1, roots=1, absent=0
                    len(cell), 0x00])                       # tot_cells_size, root index
           + cell)
    return base64.b64encode(boc).decode()


@router.post("/topup/ton_create")
async def ton_create(req: dict):
    u = auth(req)
    if not TON_WALLET:
        raise HTTPException(status_code=500, detail="TON_WALLET not set")
    try:
        amount = float(req.get("amount"))
    except Exception:
        raise HTTPException(status_code=400, detail="bad amount")
    if not 0.1 <= amount <= 1000:
        raise HTTPException(status_code=400, detail="amount 0.1..1000")

    topup_id = "fs" + secrets.token_hex(6)                  # он же комментарий к переводу
    credited = amount                                       # TON зачисляется на отдельный TON-баланс
    supabase.table("topups").insert({
        "id": topup_id, "user_id": str(u["id"]), "method": "ton",
        "amount": amount, "credited": credited, "status": "pending",
    }).execute()
    return {"id": topup_id, "address": TON_WALLET, "nano": int(round(amount * 1e9)),
            "payload": comment_payload(topup_id), "credited": credited}


@router.post("/topup/ton_check")
async def ton_check(req: dict):
    u = auth(req)
    topup_id = str(req.get("id", ""))
    res = supabase.table("topups").select("*").eq("id", topup_id).eq("method", "ton").execute()
    if not res.data or res.data[0]["user_id"] != str(u["id"]):
        raise HTTPException(status_code=404, detail="not found")
    row = res.data[0]
    if row["status"] == "paid":
        return {"status": "paid"}

    # старше часа и не оплачен — просрочен
    created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
    if (datetime.now(timezone.utc) - created).total_seconds() > 3600:
        supabase.table("topups").update({"status": "expired"}).eq("id", topup_id).eq("status", "pending").execute()
        return {"status": "expired"}

    headers = {"X-API-Key": TONCENTER_KEY} if TONCENTER_KEY else {}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = (await c.get("https://toncenter.com/api/v3/transactions",
                             params={"account": TON_WALLET, "limit": 50, "sort": "desc"},
                             headers=headers)).json()
    except Exception as e:
        print("ton_check error:", repr(e))
        return {"status": "pending"}

    need = int(round(float(row["amount"]) * 1e9))
    for tx in r.get("transactions", []):
        m = tx.get("in_msg") or {}
        comment = ((m.get("message_content") or {}).get("decoded") or {}).get("comment")
        aborted = (tx.get("description") or {}).get("aborted")
        if comment == topup_id and int(m.get("value") or 0) >= need and not aborted:
            supabase.rpc("credit_topup", {"p_id": topup_id, "p_hash": tx["hash"]}).execute()
            return {"status": "paid"}
    return {"status": "pending"}
