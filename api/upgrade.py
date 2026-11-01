import random

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

try:
    from ref import validate_init_data, rpc
except ImportError:
    from api.ref import validate_init_data, rpc

router = APIRouter()

# Шансы такие же, как в старой версии:
#   шанс = (цена_моего / цена_цели) * 100 * 0.8, но не меньше 10% и не больше 85%
#   цель не может быть дороже моего предмета больше чем в 7.5 раза
EDGE = 0.8
MIN_CHANCE = 10.0
MAX_CHANCE = 85.0
MAX_MULT = 7.5

# Криптостойкий генератор: результат нельзя предсказать
_rng = random.SystemRandom()

ERRORS = {
    "not_found": "Этого предмета нет в твоём инвентаре",
}


def calc_chance(from_price: float, to_price: float) -> float:
    chance = (from_price / to_price) * 100 * EDGE
    return min(max(chance, MIN_CHANCE), MAX_CHANCE)


class UpgradeReq(BaseModel):
    init_data: str
    from_nft: int   # id NFT из nft_catalog, который кладём в апгрейд
    to_nft: int     # id NFT из nft_catalog, который хотим получить


@router.post("/upgrade/run")
async def upgrade_run(req: UpgradeReq):
    user = validate_init_data(req.init_data)

    if req.from_nft == req.to_nft:
        raise HTTPException(400, "Выбери другую цель")

    # Цены берём только из каталога на сервере, с фронта им верить нельзя
    catalog = rpc("get_catalog", {}) or []
    prices = {int(c["id"]): float(c.get("price_stars") or 0) for c in catalog}

    from_price = prices.get(req.from_nft)
    to_price = prices.get(req.to_nft)
    if not from_price or not to_price:
        raise HTTPException(400, "Предмет не найден в каталоге")
    if to_price <= from_price:
        raise HTTPException(400, "Цель должна быть дороже твоего предмета")
    if to_price > from_price * MAX_MULT:
        raise HTTPException(400, "Слишком дорогая цель")

    chance = calc_chance(from_price, to_price)
    win = (_rng.random() * 100) <= chance

    # Одна транзакция в базе: забирает предмет, при победе выдаёт новый
    res = rpc("upgrade_item", {
        "p_user_id": str(user["id"]),
        "p_from_nft": req.from_nft,
        "p_to_nft": req.to_nft,
        "p_win": win,
    })
    if isinstance(res, list):
        res = res[0] if res else {}
    res = res or {}
    if not res.get("success"):
        raise HTTPException(400, ERRORS.get(res.get("error"), "Не удалось выполнить апгрейд"))

    return {
        "success": True,
        "win": win,
        "chance": round(chance, 2),
        "from_nft": req.from_nft,
        "to_nft": req.to_nft,
    }
