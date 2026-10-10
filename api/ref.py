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

# Цены только на сервере, клиенту верить нельзя
GIFTS = {
    "Сердечко": 15, "Мишка": 15,
    "Роза": 25, "Подарок": 25,
    "Торт": 50, "Букет": 50, "Ракета": 50,
    "Кубок": 100, "Кольцо": 100, "Бриллиант": 100,
}


def validate_init_data(init_data: str, max_age: int = 86400) -> dict:
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


def rpc(name: str, params: dict):
    try:
        return supabase.rpc(name, params).execute().data
    except Exception as e:
        print(f"RPC {name} error:", repr(e))
        raise HTTPException(500, f"db error: {str(e)[:200]}")


class RefReq(BaseModel):
    init_data: str
    start_param: str | None = None


class InitReq(BaseModel):
    init_data: str


class ClaimReq(BaseModel):
    init_data: str
    referral_id: int


class PromoReq(BaseModel):
    init_data: str
    code: str


class BuyReq(BaseModel):
    init_data: str
    gift_name: str


@router.get("/ref_ping")
async def ref_ping():
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

    return rpc("register_referral", {
        "p_referred_id": int(user["id"]),
        "p_referrer_id": int(req.start_param),
        "p_is_premium": bool(user.get("is_premium", False)),
        "p_username": user.get("username") or user.get("first_name"),
    })


@router.post("/get_referrals")
async def get_referrals(req: InitReq):
    user = validate_init_data(req.init_data)
    return rpc("get_referral_list", {"p_user_id": str(user["id"])})


@router.post("/claim_referral")
async def claim_referral(req: ClaimReq):
    user = validate_init_data(req.init_data)
    return rpc("claim_referral", {"p_user_id": str(user["id"]), "p_ref_id": req.referral_id})


@router.post("/claim_all_referrals")
async def claim_all_referrals(req: InitReq):
    user = validate_init_data(req.init_data)
    return rpc("claim_all_referrals", {"p_user_id": str(user["id"])})


@router.get("/get_ref_stats/{user_id}")
async def get_ref_stats(user_id: int):
    return rpc("get_ref_stats", {"p_user_id": str(user_id)})


@router.post("/activate_promo")
async def activate_promo(req: PromoReq):
    user = validate_init_data(req.init_data)
    code = req.code.strip()
    if not code:
        raise HTTPException(400, "Введите промокод")

    res = rpc("activate_promo", {"p_user_id": str(user["id"]), "p_code": code})
    if not res.get("success"):
        raise HTTPException(400, res.get("error") or "Не удалось активировать")
    return res


@router.post("/buy_gift")
async def buy_gift(req: BuyReq):
    user = validate_init_data(req.init_data)
    price = GIFTS.get(req.gift_name)
    if price is None:
        raise HTTPException(400, "Неизвестный подарок")

    res = rpc("buy_gift", {
        "p_user_id": str(user["id"]),
        "p_gift": req.gift_name,
        "p_price": price,
    })
    if not res.get("success"):
        return res

    text = (
        "🎁 НОВАЯ ЗАЯВКА НА ВЫВОД\n\n"
        f"👤 Пользователь: {user.get('first_name', 'Без имени')}\n"
        f"🆔 Telegram ID: {user['id']}\n"
        f"🔗 Username: @{user.get('username') or 'отсутствует'}\n"
        f"🏷 Товар: {req.gift_name}\n"
        f"💰 Стоимость: {price} ⭐"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": ADMIN_ID, "text": text},
            )
    except Exception as e:
        # заказ уже записан в gift_orders, админ увидит его в базе
        print("notify admin error:", repr(e))

    return res
