# ref.py — бэкенд реферальной системы (FastAPI + Supabase)

import os, hmac, hashlib, json, time
from urllib.parse import parse_qsl

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from supabase import create_client

router = APIRouter()

supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = os.environ["ADMIN_ID"]


def validate_init_data(init_data: str, max_age: int = 86400) -> dict:
    """Проверяет подпись Telegram и возвращает user (с is_premium). Подделать нельзя."""
    data = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = data.pop("hash", None)
    if not received_hash:
        raise HTTPException(401, "no hash")

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calc, received_hash):
        raise HTTPException(401, "bad signature")
    if time.time() - int(data.get("auth_date", 0)) > max_age:
        raise HTTPException(401, "expired")

    return json.loads(data["user"])


class RefReq(BaseModel):
    init_data: str
    start_param: str | None = None


class InitReq(BaseModel):
    init_data: str


class ClaimReq(BaseModel):
    init_data: str
    referral_id: int


class NotifyReq(BaseModel):
    init_data: str
    gift_name: str
    price: int


@router.get("/ref_ping")
async def ref_ping():
    """Проверка: открой https://ТВОЙ-ДОМЕН/ref_ping — должно быть {"ok": true}."""
    try:
        supabase.rpc("get_ref_stats", {"p_user_id": "0"}).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}


@router.post("/register_referral")
async def register_referral(req: RefReq):
    user = validate_init_data(req.init_data)

    if not req.start_param or not req.start_param.isdigit():
        return {"success": False, "reason": "no_ref"}

    res = supabase.rpc("register_referral", {
        "p_referred_id": int(user["id"]),
        "p_referrer_id": int(req.start_param),
        "p_is_premium": bool(user.get("is_premium", False)),
        "p_username": user.get("username") or user.get("first_name"),
    }).execute()
    return res.data


@router.post("/get_referrals")
async def get_referrals(req: InitReq):
    user = validate_init_data(req.init_data)
    res = supabase.rpc("get_referral_list", {"p_user_id": str(user["id"])}).execute()
    return res.data


@router.post("/claim_referral")
async def claim_referral(req: ClaimReq):
    user = validate_init_data(req.init_data)
    res = supabase.rpc("claim_referral", {
        "p_user_id": str(user["id"]),
        "p_ref_id": req.referral_id,
    }).execute()
    return res.data


@router.post("/claim_all_referrals")
async def claim_all_referrals(req: InitReq):
    user = validate_init_data(req.init_data)
    res = supabase.rpc("claim_all_referrals", {"p_user_id": str(user["id"])}).execute()
    return res.data


@router.get("/get_ref_stats/{user_id}")
async def get_ref_stats(user_id: int):
    res = supabase.rpc("get_ref_stats", {"p_user_id": str(user_id)}).execute()
    return res.data


@router.post("/notify_withdraw")
async def notify_withdraw(req: NotifyReq):
    user = validate_init_data(req.init_data)

    text = (
        "🎁 НОВАЯ ЗАЯВКА НА ВЫВОД\n\n"
        f"👤 Пользователь: {user.get('first_name', 'Без имени')}\n"
        f"🆔 Telegram ID: {user['id']}\n"
        f"🔗 Username: @{user.get('username') or 'отсутствует'}\n"
        f"🏷 Товар: {req.gift_name}\n"
        f"💰 Стоимость: {req.price} ⭐"
    )
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_ID, "text": text},
        )
    return {"success": r.status_code == 200}
