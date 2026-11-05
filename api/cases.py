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
#  ЛУТ КЕЙСОВ — ПРОПИСЫВАТЬ ЗДЕСЬ
#
#  ключ     — id кейса (тот же, что в CASES на case.html)
#  price    — цена в Stars (TON считается сам по курсу)
#  name     — название, которое увидит игрок
#  loot     — список (nft_id, вес). nft_id берётся из твоей таблицы каталога
#             (та же, из которой get_catalog отдаёт подарки).
#             Вес — относительный шанс: чем больше число, тем чаще выпадает.
#             Пример: (1, 70) и (2, 30) — это 70% и 30%.
#
#  Следи, чтобы средняя стоимость выпадения была НИЖЕ цены кейса,
#  иначе кейсы будут приносить убыток.
# =====================================================================
CASES = {
    "bear": {
        "name": "Bear", "price": 149,
        "loot": [
            (61, 80),      # Звезда (5 ⭐)
            (17, 3.333),   # WhipCupcake
            (15, 3.333),   # XmasStocking
            (13, 3.333),   # SnakeBox
            (1, 3.333),    # ChillFlame
            (4, 3.333),    # InstantRamen
            (6, 1.333),    # MirageLamp
            (16, 1.333),   # CandyCane
            (9, 0.533),    # LolPop
            (3, 0.067),    # ViceCream
            (7, 0.067)     # PoolFloat
        ]
    },
    "ring": {
        "name": "Ring", "price": 199,
        "loot": [
            (62, 75),      # Звезда (10 ⭐)
            (18, 3.125),   # LunarSnake
            (1, 3.125),    # ChillFlame
            (4, 3.125),    # InstantRamen
            (7, 3.125),    # PoolFloat
            (12, 3.125),   # MoodPack
            (6, 3.125),    # MirageLamp
            (13, 3.125),   # SnakeBox
            (15, 3.125)    # XmasStocking
        ]
    },
    "lip": {
        "name": "Lip", "price": 249,
        "loot": [
            (61, 45),      # Звезда (5 ⭐)
            (62, 25),      # Звезда (10 ⭐)
            (9, 15),       # LolPop
            (10, 8),       # HappyBrownie
            (11, 4),       # SwagBag
            (12, 1.8),     # MoodPack
            (13, 0.8),     # SnakeBox
            (14, 0.3),     # CloverPin
            (29, 0.08),    # TimelessBook
            (30, 0.02)     # InputKey
        ]
    },
    "halloween": {
        "name": "Halloween", "price": 299,
        "loot": [
            (61, 40),      # Звезда (5 ⭐)
            (62, 30),      # Звезда (10 ⭐)
            (13, 13.5),    # SnakeBox
            (14, 8),       # CloverPin
            (15, 4),       # XmasStocking
            (16, 2),       # CandyCane
            (17, 1),       # WhipCupcake
            (18, 0.8),     # LunarSnake
            (36, 0.4),     # MadPumpkin
            (39, 0.2),     # HexPot
            (38, 0.08),    # EternalCandle
            (20, 0.02)     # LightSword
        ]
    },
    "loot": {
        "name": "Loot", "price": 499,
        "loot": [
            (61, 40),      # Звезда (5 ⭐)
            (62, 30),      # Звезда (10 ⭐)
            (17, 13.5),    # WhipCupcake
            (18, 8),       # LunarSnake
            (19, 4),       # SnoopCigar
            (59, 2),       
            (20, 1),       # LightSword
            (21, 0.8),     # CookieHeart
            (22, 0.4),     # FaithAmulet
            (26, 0.2),     # StellarRocket
            (35, 0.08),    # TopHat
            (32, 0.02)     # TrappedHeart
        ]
    },
    "helmet": {
        "name": "Helmet", "price": 1499,
        "loot": [
            (61, 35),      # Звезда (5 ⭐)
            (62, 35),      # Звезда (10 ⭐)
            (1, 14),       # ChillFlame
            (2, 8),        # LibertyFigure
            (3, 4),        # ViceCream
            (4, 2.5),      # InstantRamen
            (25, 1),       # EvilEye
            (27, 0.3),     # JellyBunny
            (42, 0.1),     # NekoHelmet
            (44, 0.08),    # SwissWatch
            (45, 0.02)     # SignetRing
        ]
    },
    "pepe": {
        "name": "Pepe", "price": 2999,
        "loot": [
            (62, 50),      # Звезда (10 ⭐)
            (5, 25),       # SnoopDogg
            (6, 12),       # MirageLamp
            (7, 7),        # PoolFloat
            (8, 3.5),      # IceCream
            (37, 1.5),     # KhabibsPapakha
            (46, 0.6),     # LowRider
            (49, 0.3),     # ToyBear
            (50, 0.07),    # RareBird
            (55, 0.02)     # MightyArm
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
        # приз выбирает сервер, браузеру доверять нельзя
        nft_id = rng.choices(ids, weights=weights, k=1)[0]
        # SQL-функция должна В ОДНОЙ ТРАНЗАКЦИИ: проверить баланс, списать цену
        # и добавить подарок в инвентарь. Возвращает {success, balance, ton_balance}.
        # Каждый кейс списывается отдельно: если денег хватило не на все, откроются первые.
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
    # SQL-функция sell_item_ton в ОДНОЙ ТРАНЗАКЦИИ: найти подарок в инвентаре пользователя,
    # удалить его и начислить nft_catalog.price_ton на ton_balance.
    # Возвращает {success, balance, ton_balance}.
    res = _check(rpc("sell_item_ton", {
        "p_user_id": str(user["id"]),
        "p_nft_id": req.nft_id,
    }))
    return res
