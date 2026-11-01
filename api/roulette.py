from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

try:
    from ref import validate_init_data, rpc
except ImportError:
    from api.ref import validate_init_data, rpc

router = APIRouter()

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


@router.post("/roulette/status")
async def roulette_status(req: Base):
    user = validate_init_data(req.init_data)
    return _one(rpc("roulette_status", {"p_user_id": str(user["id"])}))


@router.post("/roulette/catalog")
async def roulette_catalog(req: Base):
    validate_init_data(req.init_data)
    data = rpc("get_catalog", {})
    if isinstance(data, dict):
        data = data.get("items") or data.get("catalog") or []
    items = [
        {
            "id": x.get("id"),
            "name": x.get("name"),
            "price_stars": x.get("price_stars"),
        }
        for x in (data or [])
        if isinstance(x, dict) and x.get("id") is not None
    ]
    return {"items": items}


@router.post("/roulette/free")
async def roulette_free(req: Base):
    user = validate_init_data(req.init_data)
    return _check(rpc("roulette_free", {"p_user_id": str(user["id"])}))


@router.post("/roulette/paid")
async def roulette_paid(req: Base):
    user = validate_init_data(req.init_data)
    return _check(rpc("roulette_paid", {"p_user_id": str(user["id"])}))


@router.post("/roulette/sell")
async def roulette_sell(req: SellReq):
    user = validate_init_data(req.init_data)
    res = _check(rpc("sell_item", {"p_user_id": str(user["id"]), "p_nft_id": req.nft_id}))
    st = _one(rpc("roulette_status", {"p_user_id": str(user["id"])}))
    return {**res, **{k: st.get(k) for k in ("balance", "bamboo_balance") if k in st}}
