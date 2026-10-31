import os

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

try:
    from ref import validate_init_data, rpc, BOT_TOKEN, ADMIN_ID
except ImportError:
    from api.ref import validate_init_data, rpc, BOT_TOKEN, ADMIN_ID

router = APIRouter()

# Комиссия за вывод NFT в Stars (списывается с баланса). 0 = бесплатно.
WITHDRAW_FEE = float(os.environ.get("WITHDRAW_FEE", "30"))

ERRORS = {
    "not_found": "Предмет не найден или уже продан",
    "insufficient": "Недостаточно Stars для вывода",
}


class InitReq(BaseModel):
    init_data: str


class ItemReq(BaseModel):
    init_data: str
    item_id: int


def grant_item(user_id, item_key: str, source: str = "case"):
    """Выдать NFT пользователю. Вызывай из роута кейсов, когда предмет выпал:
        from inventory import grant_item
        grant_item(user["id"], "nft_example_1")
    """
    res = rpc("grant_item", {"p_user_id": str(user_id), "p_item_key": item_key, "p_source": source})
    if isinstance(res, list):
        res = res[0] if res else {}
    return res or {}


@router.post("/inventory/list")
async def inventory_list(req: InitReq):
    user = validate_init_data(req.init_data)
    items = rpc("get_inventory", {"p_user_id": str(user["id"])}) or []
    return {"items": items, "withdraw_fee": WITHDRAW_FEE}


@router.post("/inventory/sell")
async def inventory_sell(req: ItemReq):
    user = validate_init_data(req.init_data)
    res = rpc("sell_item", {"p_user_id": str(user["id"]), "p_item_id": req.item_id}) or {}
    if not res.get("success"):
        raise HTTPException(400, ERRORS.get(res.get("error"), "Не удалось продать предмет"))
    return res


@router.post("/inventory/withdraw")
async def inventory_withdraw(req: ItemReq):
    user = validate_init_data(req.init_data)
    res = rpc("withdraw_item", {
        "p_user_id": str(user["id"]),
        "p_item_id": req.item_id,
        "p_fee": WITHDRAW_FEE,
    }) or {}
    if not res.get("success"):
        raise HTTPException(400, ERRORS.get(res.get("error"), "Не удалось создать заявку"))

    text = (
        "🎁 НОВАЯ ЗАЯВКА НА ВЫВОД NFT\n\n"
        f"👤 Пользователь: {user.get('first_name', 'Без имени')}\n"
        f"🆔 Telegram ID: {user['id']}\n"
        f"🔗 Username: @{user.get('username') or 'отсутствует'}\n"
        f"🏷 Предмет: {res.get('name')}\n"
        f"🔢 ID предмета: {req.item_id}"
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
