import os, hmac, hashlib, json, time
from typing import Optional
from urllib.parse import parse_qsl

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from supabase import create_client

router = APIRouter()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = os.environ.get("ADMIN_ID", "")

REF_IMAGE_URL = os.environ.get("REF_IMAGE_URL", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "FixStarsOfficial_bot")
APP_SHORT_NAME = os.environ.get("APP_SHORT_NAME", "app")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_URL and SUPABASE_SERVICE_KEY else None

# Цены только на сервере, клиенту верить нельзя
GIFTS = {
    "Сердечко": 15, "Мишка": 15,
    "Роза": 25, "Подарок": 25,
    "Торт": 50, "Букет": 50, "Ракета": 50,
    "Кубок": 100, "Кольцо": 100, "Бриллиант": 100,
}

PROMO_ERRORS = {
    "not_found": "Промокод не найден",
    "inactive": "Промокод отключён",
    "exhausted": "Промокод закончился",
    "already_used": "Вы уже активировали этот промокод",
    "user_not_found": "Пользователь не найден",
}

REF_CAPTION = (
    "<b>🎃 Страшно прибыльный Хэллоуин в FixStars! 👻</b>\n"
    "Тёмные силы принесли сокровища, а мы подготовили жутко выгодные задания! "
    "Переходи на тёмную сторону и забирай свои Telegram Stars без вложений.\n\n"
    "<b>🎃 Что тебя ждёт:</b>\n"
    "🦇 Простые квесты: подписки, просмотры и репосты\n"
    "🕸 Приглашай друзей — забирай их «души» (и звёздные бонусы!)\n"
    "⚡️ Быстрые и честные выплаты прямо в Telegram\n\n"
    "🕯 Не бойся — начать зарабатывать можно прямо сейчас!\n"
    "👇 Жми кнопку ниже и забирай свою добычу:"
)


def validate_init_data(init_data: str, max_age: int = 86400) -> dict:
    if not BOT_TOKEN:
        raise HTTPException(500, "BOT_TOKEN missing")
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
    if supabase is None:
        raise HTTPException(500, "supabase env missing")
    try:
        return supabase.rpc(name, params).execute().data
    except Exception as e:
        print(f"RPC {name} error:", repr(e))
        raise HTTPException(500, f"db error: {str(e)[:200]}")


class RefReq(BaseModel):
    init_data: str
    start_param: Optional[str] = None


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


@router.get("/env_check")
async def env_check():
    return {
        "SUPABASE_URL": bool(SUPABASE_URL),
        "SUPABASE_SERVICE_KEY": bool(SUPABASE_SERVICE_KEY),
        "BOT_TOKEN": bool(BOT_TOKEN),
        "ADMIN_ID": bool(ADMIN_ID),
        "REF_IMAGE_URL": bool(REF_IMAGE_URL),
    }


@router.get("/ref_ping")
async def ref_ping():
    if supabase is None:
        return {"ok": False, "error": "SUPABASE_URL or SUPABASE_SERVICE_KEY missing"}
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
    code = (req.code or "").strip()
    if not code or len(code) > 64:
        raise HTTPException(400, "Введите промокод")

    res = rpc("activate_promo", {"p_user_id": str(user["id"]), "p_code": code})
    if isinstance(res, list):
        res = res[0] if res else {}
    res = res or {}

    if not res.get("success"):
        err = res.get("error")
        raise HTTPException(400, PROMO_ERRORS.get(err, err or "Не удалось активировать"))

    return {
        "success": True,
        "reward": float(res.get("reward") or 0),
        "new_balance": float(res.get("new_balance") or 0),
    }


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
        print("notify admin error:", repr(e))

    return res


# ===== Красивая реферальная рассылка (фото + текст + кнопка) =====
@router.post("/share_ref")
async def share_ref(req: InitReq):
    user = validate_init_data(req.init_data)
    uid = int(user["id"])

    if not REF_IMAGE_URL:
        return {"ok": False, "reason": "no_image"}

    # Обычная реферальная ссылка на чат с ботом (а не прямая ссылка на мини-апку)
    link = f"https://t.me/{BOT_USERNAME}?start={uid}"
    result = {
        "type": "photo",
        "id": f"ref{uid}",
        "photo_url": REF_IMAGE_URL,
        "thumbnail_url": REF_IMAGE_URL,
        "caption": REF_CAPTION,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [[{"text": "Забрать добычу", "url": link}]]},
    }
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/savePreparedInlineMessage",
                json={
                    "user_id": uid,
                    "result": result,
                    "allow_user_chats": True,
                    "allow_bot_chats": True,
                    "allow_group_chats": True,
                    "allow_channel_chats": True,
                },
            )
        data = r.json()
        if not data.get("ok"):
            print("savePreparedInlineMessage error:", data)
            return {"ok": False, "reason": "tg_error"}
        return {"ok": True, "id": data["result"]["id"]}
    except Exception as e:
        print("share_ref exception:", repr(e))
        return {"ok": False, "reason": "exception"}
