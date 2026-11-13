import asyncio
import random
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

try:
    from ref import validate_init_data, rpc, supabase
except ImportError:
    from api.ref import validate_init_data, rpc, supabase

router = APIRouter()

# ---------------- ПЛАТНАЯ РУЛЕТКА: 150 звёзд ----------------
# Картинка мишки задаётся прямо в коде. Всё остальное (название, цена, инвентарь)
# берётся из базы по ID гифта.
MISHA_ID = 64
MISHA_IMAGE_URL = "https://github.com/artcool213141-max/sdjsdjsd/blob/main/misha.png?raw=true"

ROULETTE_PAID_PRICE = 150   # цена одного прокрута в звёздах

# (id гифта, шанс в %). 64 = твой мишка, выпадает с шансом 71%.
# Сумма шансов = 100.
ROULETTE_PAID_LOOT = [
    (MISHA_ID, 71.0),   # мишка (misha.png)
    (17, 12.0),
    (15, 8.0),
    (13, 5.0),
    (1, 2.0),
    (4, 1.2),
    (6, 0.5),
    (16, 0.2),
    (9, 0.07),
    (3, 0.02),
    (7, 0.01),
]

ERRORS = {
    "cooldown": "Бесплатный кейс ещё недоступен",
    "insufficient": "Недостаточно Stars",
    "user_not_found": "Пользователь не найден",
    "no_item": "Подарок не найден в инвентаре",
    "failed": "Не удалось выполнить действие",
}


class Base(BaseModel):
    init_data: str


class SellReq(Base):
    nft_id: int


def _one(res):
    if isinstance(res, list):
        res = res[0] if res else {}
    return res or {}


def _check(res):
    res = _one(res)
    if res.get("success") is False or res.get("error"):
        raise HTTPException(400, ERRORS.get(res.get("error"), res.get("error") or ERRORS["failed"]))
    return res


def _image(item: dict):
    """Для мишки подставляем картинку из кода, для остальных - из базы."""
    if item.get("id") == MISHA_ID:
        return MISHA_IMAGE_URL
    return item.get("image_url")


def _catalog_items() -> list:
    data = rpc("get_catalog", {})
    if isinstance(data, dict):
        data = data.get("items") or data.get("catalog") or []
    return [x for x in (data or []) if isinstance(x, dict) and x.get("id") is not None]


def _credit_referral_sync(user: dict):
    """Зачисляет реферала после первого открытия бесплатного кейса.
    Берёт пригласившего из pending_refs, пишет в referrals, помечает verified.
    Любая ошибка только логируется, на открытие кейса не влияет."""
    if supabase is None:
        return
    uid = int(user["id"])
    try:
        pr = (supabase.table("pending_refs").select("referrer_id,verified")
              .eq("user_id", uid).limit(1).execute().data)
        if not pr or pr[0].get("verified"):
            return
        ref_id = int(pr[0]["referrer_id"])
        if ref_id == uid:
            return

        ex = supabase.table("referrals").select("id").eq("referred_id", uid).limit(1).execute().data
        if not ex:
            # пригласивший должен существовать в users (внешний ключ)
            rr = supabase.table("users").select("id").eq("id", str(ref_id)).limit(1).execute().data
            if not rr:
                print("credit_ref: пригласивший не найден в users", ref_id)
                return
            supabase.table("referrals").insert({
                "referred_id": uid,
                "invited_id": str(uid),
                "referrer_id": str(ref_id),
                "is_premium": bool(user.get("is_premium", False)),
            }).execute()
            print("credit_ref OK:", uid, "<-", ref_id)

        supabase.table("pending_refs").update({
            "verified": True,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }).eq("user_id", uid).execute()
    except Exception as e:
        print("credit_ref ERROR:", uid, repr(e))


@router.post("/roulette/status")
async def roulette_status(req: Base):
    user = validate_init_data(req.init_data)
    return _one(rpc("roulette_status", {"p_user_id": str(user["id"])}))


@router.post("/roulette/catalog")
async def roulette_catalog(req: Base):
    validate_init_data(req.init_data)
    items = [
        {
            "id": x.get("id"),
            "name": x.get("name"),
            "price_stars": x.get("price_stars"),
            "image_url": _image(x),
        }
        for x in _catalog_items()
    ]
    return {"items": items}


@router.post("/roulette/free")
async def roulette_free(req: Base):
    user = validate_init_data(req.init_data)
    res = _check(rpc("roulette_free", {"p_user_id": str(user["id"])}))
    # кейс успешно открыт -> засчитываем реферала
    await asyncio.to_thread(_credit_referral_sync, user)
    return res


@router.post("/roulette/paid")
async def roulette_paid(req: Base):
    user = validate_init_data(req.init_data)
    uid = str(user["id"])

    cat = {x["id"]: x for x in _catalog_items()}
    pool = [(nid, w) for nid, w in ROULETTE_PAID_LOOT if nid in cat]
    if not pool:
        raise HTTPException(500, "В рулетке нет призов")

    nft_id = random.SystemRandom().choices(
        [p[0] for p in pool], weights=[p[1] for p in pool], k=1
    )[0]

    # списание 150 звёзд и выдача выпавшего гифта делает open_case
    res = _check(rpc("open_case", {
        "p_user_id": uid,
        "p_nft_id": nft_id,
        "p_currency": "stars",
        "p_price": ROULETTE_PAID_PRICE,
    }))

    item = cat[nft_id]
    return {
        **res,
        "nft": {
            "id": item.get("id"),
            "name": item.get("name"),
            "price_stars": item.get("price_stars"),
            "image_url": _image(item),
        },
        "price": ROULETTE_PAID_PRICE,
    }


@router.post("/roulette/sell")
async def roulette_sell(req: SellReq):
    user = validate_init_data(req.init_data)
    res = _check(rpc("sell_item", {"p_user_id": str(user["id"]), "p_nft_id": req.nft_id}))
    st = _one(rpc("roulette_status", {"p_user_id": str(user["id"])}))
    return {**res, **{k: st.get(k) for k in ("balance", "bamboo_balance") if k in st}}
