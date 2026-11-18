import os, hmac, hashlib, json, time, asyncio
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

BOT_USERNAME = os.environ.get("BOT_USERNAME", "PandaGift_app")
APP_SHORT_NAME = os.environ.get("APP_SHORT_NAME", "app")

# ===== Картинка для реферального сообщения =====
# Порядок: 1) готовый file_id из env  2) локальный файл img/reffo.jpg рядом с кодом
# 3) скачиваем с GitHub САМИ (с нашего сервера) и один раз заливаем в Telegram.
# После этого Telegram больше ничего не качает по ссылке, а использует file_id.
REF_FILE_ID = os.environ.get("REF_FILE_ID", "")
REF_IMAGE_URL = "https://raw.githubusercontent.com/artcool213141-max/sdjsdjsd/main/img/reffo.jpg"
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REF_LOCAL_PATHS = [
    os.path.join(_BASE_DIR, "img", "reffo.jpg"),
    os.path.join(os.getcwd(), "img", "reffo.jpg"),
]

_file_id_cache = {"id": REF_FILE_ID}
_file_id_lock = asyncio.Lock()

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
    "<b>🐼 Залетай в PandaGift и забирай своё!</b>\n"
    "<b>Крути бесплатный кейс раз в 24 часа</b>\n"
    "<u>Быстрые выплаты без лишних комиссий</u>\n"
    "<u>Панда уже заряжена на победу</u>\n"
    "\n"
    "👇 Жми кнопку и выводи NFT"
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


async def _load_image_bytes() -> Optional[bytes]:
    """Берём картинку с диска, а если её нет, качаем с GitHub с нашего сервера."""
    for p in REF_LOCAL_PATHS:
        if os.path.isfile(p):
            with open(p, "rb") as f:
                print("ref image: loaded from disk", p)
                return f.read()
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            r = await c.get(REF_IMAGE_URL)
        if r.status_code == 200 and r.content:
            print("ref image: downloaded from GitHub,", len(r.content), "bytes")
            return r.content
        print("ref image: GitHub status", r.status_code)
    except Exception as e:
        print("ref image: download error", repr(e))
    return None


async def get_ref_file_id(force: bool = False) -> Optional[str]:
    """Возвращает file_id картинки. При первом вызове заливает её в Telegram."""
    if _file_id_cache["id"] and not force:
        return _file_id_cache["id"]

    async with _file_id_lock:
        if _file_id_cache["id"] and not force:
            return _file_id_cache["id"]

        if not ADMIN_ID:
            print("ref image: ADMIN_ID missing, cannot upload")
            return None

        content = await _load_image_bytes()
        if not content:
            return None

        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                    data={"chat_id": ADMIN_ID, "disable_notification": "true"},
                    files={"photo": ("reffo.jpg", content, "image/jpeg")},
                )
                data = r.json()
                if not data.get("ok"):
                    print("ref image: sendPhoto error:", data)
                    return None

                msg = data["result"]
                file_id = msg["photo"][-1]["file_id"]
                _file_id_cache["id"] = file_id
                print("ref image: file_id cached. Put it in env REF_FILE_ID:", file_id)

                # убираем служебное сообщение из чата админа
                try:
                    await c.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage",
                        json={"chat_id": ADMIN_ID, "message_id": msg["message_id"]},
                    )
                except Exception:
                    pass
                return file_id
        except Exception as e:
            print("ref image: upload exception:", repr(e))
            return None


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
        "REF_FILE_ID": bool(_file_id_cache["id"]),
    }


@router.get("/ref_image_check")
async def ref_image_check():
    """Заливает картинку в Telegram и сообщает, получилось ли. file_id смотри в логах."""
    fid = await get_ref_file_id()
    return {"ok": bool(fid)}


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


async def _save_prepared(uid: int, result: dict):
    async with httpx.AsyncClient(timeout=15) as c:
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
    return r.json()


# ===== Красивая реферальная рассылка (фото + текст + кнопка) =====
@router.post("/share_ref")
async def share_ref(req: InitReq):
    user = validate_init_data(req.init_data)
    uid = int(user["id"])

    # Обычная реферальная ссылка на чат с ботом (а не прямая ссылка на мини-апку)
    link = f"https://t.me/{BOT_USERNAME}?start={uid}"
    keyboard = {"inline_keyboard": [[{"text": "Бесплатный кейс", "url": link}]]}

    try:
        file_id = await get_ref_file_id()

        # Основной путь: картинка из file_id (надёжно, Telegram ничего не качает)
        if file_id:
            result = {
                "type": "photo",
                "id": f"ref{uid}",
                "photo_file_id": file_id,
                "caption": REF_CAPTION,
                "parse_mode": "HTML",
                "reply_markup": keyboard,
            }
            data = await _save_prepared(uid, result)
            if data.get("ok"):
                return {"ok": True, "id": data["result"]["id"]}

            print("savePreparedInlineMessage (file_id) error:", data)
            # file_id мог устареть или быть от другого бота: заливаем заново и пробуем ещё раз
            file_id = await get_ref_file_id(force=True)
            if file_id:
                result["photo_file_id"] = file_id
                data = await _save_prepared(uid, result)
                if data.get("ok"):
                    return {"ok": True, "id": data["result"]["id"]}
                print("savePreparedInlineMessage (retry) error:", data)

        # Запасной путь: по ссылке (может не сработать, если Telegram не качает с GitHub)
        result = {
            "type": "photo",
            "id": f"ref{uid}",
            "photo_url": REF_IMAGE_URL,
            "thumbnail_url": REF_IMAGE_URL,
            "caption": REF_CAPTION,
            "parse_mode": "HTML",
            "reply_markup": keyboard,
        }
        data = await _save_prepared(uid, result)
        if data.get("ok"):
            return {"ok": True, "id": data["result"]["id"]}

        print("savePreparedInlineMessage (url) error:", data)
        return {"ok": False, "reason": "tg_error"}
    except Exception as e:
        print("share_ref exception:", repr(e))
        return {"ok": False, "reason": "exception"}
