import random

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

try:
    from ref import validate_init_data, rpc
except ImportError:
    from api.ref import validate_init_data, rpc

router = APIRouter()

# 100 Stars = 1.3 TON
TON_PER_STAR = 1.3 / 100

# =====================================================================
#  ЛУТ КЕЙСОВ — БАЛАНС: Стабильный окуп (раз в 2-3 прокрута) + Джекпот 5-7%
# =====================================================================
CASES = {
"bear": {
        "name": "Bear", "price": 149,
        "loot": [
            (61, 71.0),    # Минус / Слив (5 ⭐) — поднят для контроля профита
            (17, 12.0),    # Возврат / Окуп (410 ⭐) — понижен
            (15, 8.0),     # Окуп (420 ⭐) — понижен
            (13, 5.0),     # Окуп (428 ⭐) — понижен
            (1, 2.0),      # Окуп (455 ⭐) — понижен
            (4, 1.2),      # Крупный окуп (459 ⭐) — понижен
            (6, 0.5),      # (475 ⭐) — понижен
            (16, 0.2),     # (479 ⭐) — понижен
            (9, 0.07),     # (480 ⭐) — понижен
            (3, 0.02),     # (490 ⭐) — понижен
            (7, 0.01)      # (497 ⭐) — редкий вин
        ]
    },
    "ring": {
        "name": "Ring", "price": 199,
        "loot": [
            (62, 55.0),    # Минус / Слив (10 ⭐)
            (18, 18.0),    # Возврат / Окуп (399 ⭐)
            (1, 12.0),     # Окуп (455 ⭐)
            (4, 8.0),      # Окуп (459 ⭐)
            (7, 3.5),      # Крупный окуп (497 ⭐)
            (12, 2.0),     # Джекпот ~5.5% (500 ⭐)
            (6, 1.0),      # (475 ⭐)
            (13, 0.3),     # (428 ⭐)
            (15, 0.2)      # (420 ⭐)
        ]
    },
    "lip": {
        "name": "Lip", "price": 249,
        "loot": [
            (61, 45.0),    # Минус (5 ⭐)
            (62, 18.0),    # Минус (10 ⭐)
            (9, 15.0),     # Окуп (480 ⭐)
            (10, 10.0),    # Окуп (543 ⭐)
            (11, 5.0),     # Крупный окуп (571 ⭐)
            (12, 3.5),     # Джекпот ~5.5% (500 ⭐)
            (13, 1.8),     # (428 ⭐)
            (14, 1.0),     # (525 ⭐)
            (29, 0.5),     # (525 ⭐)
            (30, 0.2)      # (610 ⭐)
        ]
    },
    "halloween": {
        "name": "Halloween", "price": 299,
        "loot": [
            (61, 42.0),    # Минус (5 ⭐)
            (62, 20.0),    # Минус (10 ⭐)
            (13, 15.0),    # Окуп (428 ⭐)
            (14, 10.0),    # Окуп (525 ⭐)
            (15, 6.0),     # Крупный окуп (420 ⭐)
            (16, 4.0),     # Джекпот ~6% (479 ⭐)
            (17, 1.5),     # (410 ⭐)
            (18, 0.8),     # (399 ⭐)
            (10, 0.4),     # (543 ⭐)
            (36, 0.2),     # (1345 ⭐)
            (39, 0.05),    # (580 ⭐)
            (38, 0.03),    # (571 ⭐)
            (20, 0.02)     # (612 ⭐)
        ]
    },
    "loot": {
        "name": "Loot", "price": 499,
        "loot": [
            (61, 42.0),    # Минус (5 ⭐)
            (62, 20.0),    # Минус (10 ⭐)
            (17, 15.0),    # Окуп (410 ⭐)
            (18, 10.0),    # Окуп (399 ⭐)
            (19, 6.0),     # Крупный окуп (1520 ⭐)
            (59, 4.0),     # Джекпот ~6%
            (20, 1.5),     # (612 ⭐)
            (21, 0.8),     # (555 ⭐)
            (22, 0.4),     # (572 ⭐)
            (26, 0.2),     # (554 ⭐)
            (28, 0.08),    # (715 ⭐)
            (32, 0.02)     # (1343 ⭐)
        ]
    },
    "helmet": {
        "name": "Helmet", "price": 1499,
        "loot": [
            # Минусовые позиции
            (34, 38.0),    # CrystalBall (1321 ⭐)
            (32, 24.0),    # TrappedHeart (1343 ⭐)
            (36, 15.0),    # MadPumpkin (1345 ⭐)
            # Окупаемые позиции и джекпот (суммарно ~23%, где крупный/редкий окуп занимает 5-7%)
            (48, 12.0),    # IonicDryer (1445 ⭐)
            (33, 5.0),     # LovePotion (1447 ⭐)
            (19, 3.5),     # SnoopCigar (1520 ⭐)
            (50, 1.5),     # RareBird (2643 ⭐) — крупный окуп
            (49, 0.6),     # ToyBear (3265 ⭐) — джекпот
            (31, 0.25),    # DiamondRing (3258 ⭐)
            (42, 0.1),     # NekoHelmet (3494 ⭐)
            (45, 0.03),    # SignetRing (3510 ⭐)
            (47, 0.015),   # VintageCigar (3828 ⭐)
            (46, 0.003),   # LowRider (6121 ⭐)
            (44, 0.001),   # SwissWatch (6285 ⭐)
            (52, 0.0005)   # NailBracelet (11259 ⭐)
        ]
    },
    "pepe": {
        "name": "Pepe", "price": 2999,
        "loot": [
            # Минусовые позиции
            (37, 40.0),    # KhabibsPapakha (2363 ⭐)
            (50, 25.0),    # RareBird (2643 ⭐)
            (49, 14.0),    # ToyBear (3265 ⭐)
            # Окупаемые позиции и джекпот (~21% суммарно, 5-7% на крупные)
            (31, 10.0),    # DiamondRing (3258 ⭐)
            (42, 4.5),     # NekoHelmet (3494 ⭐)
            (45, 3.5),     # SignetRing (3510 ⭐)
            (47, 1.5),     # VintageCigar (3828 ⭐) — крупный окуп
            (46, 0.7),     # LowRider (6121 ⭐) — джекпот
            (44, 0.3),     # SwissWatch (6285 ⭐)
            (52, 0.2),     # NailBracelet (11259 ⭐)
            (55, 0.1),     # MightyArm (13869 ⭐)
            (57, 0.05),    # LootBag (14685 ⭐)
            (56, 0.02),    # HeroicHelmet (22836 ⭐)
            (58, 0.01),    # ScaredCat (25039 ⭐)
            (40, 0.005)    # AstralShard (11855 ⭐)
        ]
    }
}
# =====================================================================

ERRORS = {
    "insufficient": "Недостаточно средств",
    "user_not_found": "Пользователь не найден",
    "no_item": "Подарок не найден в инвентаре",
    "failed": "Не удалось открыть кейс",
}


class Base(BaseModel):
    init_data: str


class InfoReq(Base):
    case_id: str


class OpenReq(Base):
    case_id: str
    currency: str = "stars"  # stars | ton
    count: int = 1           # сколько кейсов открыть за раз (1..5)


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


def _case(case_id: str) -> dict:
    cfg = CASES.get(case_id)
    if not cfg:
        raise HTTPException(404, "Кейс не найден")
    return cfg


def _catalog() -> dict:
    data = rpc("get_catalog", {})
    if isinstance(data, dict):
        data = data.get("items") or data.get("catalog") or []
    return {
        x["id"]: {
            "id": x["id"],
            "name": x.get("name"),
            "price_stars": x.get("price_stars"),
            "price_ton": x.get("price_ton") or 0,
        }
        for x in (data or [])
        if isinstance(x, dict) and x.get("id") is not None
    }


def _price_ton(stars: int) -> float:
    return round(stars * TON_PER_STAR, 2)


@router.post("/case/info")
async def case_info(req: InfoReq):
    validate_init_data(req.init_data)
    cfg = _case(req.case_id)
    cat = _catalog()
    items = [cat[nid] for nid, _ in cfg["loot"] if nid in cat]
    return {
        "id": req.case_id,
        "name": cfg["name"],
        "price_stars": cfg["price"],
        "price_ton": _price_ton(cfg["price"]),
        "items": items,
    }


@router.post("/case/open")
async def case_open(req: OpenReq):
    user = validate_init_data(req.init_data)
    cfg = _case(req.case_id)
    if req.currency not in ("stars", "ton"):
        raise HTTPException(400, "Неверная валюта")

    cat = _catalog()
    pool = [(nid, w) for nid, w in cfg["loot"] if nid in cat]
    if not pool:
        raise HTTPException(500, "В кейсе нет призов")

    count = max(1, min(5, int(req.count or 1)))
    price = cfg["price"] if req.currency == "stars" else _price_ton(cfg["price"])
    rng = random.SystemRandom()
    ids = [p[0] for p in pool]
    weights = [p[1] for p in pool]

    nfts, res = [], {}
    for i in range(count):
        nft_id = rng.choices(ids, weights=weights, k=1)[0]
        try:
            res = _check(rpc("open_case", {
                "p_user_id": str(user["id"]),
                "p_nft_id": nft_id,
                "p_currency": req.currency,
                "p_price": price,
            }))
        except HTTPException:
            if i == 0:
                raise
            break
        nfts.append(cat[nft_id])
    return {**res, "nft": nfts[0], "nfts": nfts}

@router.post("/case/sell_ton")
async def case_sell_ton(req: SellReq):
    user = validate_init_data(req.init_data)
    res = _check(rpc("sell_item_ton", {
        "p_user_id": str(user["id"]),
        "p_nft_id": req.nft_id,
    }))
    return res
