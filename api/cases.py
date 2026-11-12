import math
import os
import random
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

try:
    from ref import validate_init_data, rpc
except ImportError:
    from api.ref import validate_init_data, rpc

try:
    from supabase import create_client
except Exception:  # supabase не установлен
    create_client = None

router = APIRouter()

# 100 Stars = 1.3 TON
TON_PER_STAR = 1.3 / 100

# Бамбук за каждый открытый платный кейс (включительно).
BAMBOO_MIN = 1
BAMBOO_MAX = 10

# ---------------- РУССКАЯ РУЛЕТКА ----------------
# Обычные гифты (доступны при шансе от 35% до 95%)
RR_ITEMS_STANDARD = [
    61,  # Star
    17,  # WhipCupcake
    9,   # LolPop
    12,  # MoodPack
    48,  # IonicDryer
    19,  # SnoopCigar
    35,  # TopHat
]

# Дорогие / эксклюзивные гифты (можно крутить ТОЛЬКО при шансе >= 80%)
RR_ITEMS_PREMIUM = [
    34,  # CrystalBall
    50,  # RareBird
    49,  # ToyBear
    47,  # VintageCigar
    46,  # LowRider
    44,  # SwissWatch
    41,  # PerfumeBottle
    52,  # NailBracelet
    40,  # AstralShard
]

# Полный список для каталога в рулетке
RR_ITEMS = RR_ITEMS_STANDARD + RR_ITEMS_PREMIUM

RR_MIN_CHANCE = 10
RR_STANDARD_MIN_CHANCE = 35  # Минимальный шанс для обычных гифтов
RR_PREMIUM_MIN_CHANCE = 80   # Минимальный шанс для дорогих гифтов
RR_MAX_CHANCE = 95

RR_FEE_THRESHOLD = 50
RR_FEE_HIGH_CHANCE = 5    # %
RR_FEE_LOW_CHANCE = 15    # %

# =====================================================================
#  ЛУТ КЕЙСОВ
# =====================================================================
CASES = {
    "bear": {
        "name": "Bear", "price": 149,
        "loot": [
            (61, 71.0), (17, 12.0), (15, 8.0), (13, 5.0), (1, 2.0), (4, 1.2),
            (6, 0.5), (16, 0.2), (9, 0.07), (3, 0.02), (7, 0.01)
        ]
    },
    "ring": {
        "name": "Ring", "price": 199,
        "loot": [
            (62, 55.0), (18, 18.0), (1, 12.0), (4, 8.0), (7, 3.5),
            (12, 2.0), (6, 1.0), (13, 0.3), (15, 0.2)
        ]
    },
    "lip": {
        "name": "Lip", "price": 249,
        "loot": [
            (61, 45.0), (62, 18.0), (9, 15.0), (10, 10.0), (11, 5.0),
            (12, 3.5), (13, 1.8), (14, 1.0), (29, 0.5), (30, 0.2)
        ]
    },
    "halloween": {
        "name": "Halloween", "price": 299,
        "loot": [
            (61, 42.0), (62, 20.0), (13, 15.0), (14, 10.0), (15, 6.0),
            (16, 4.0), (17, 1.5), (18, 0.8), (10, 0.4), (36, 0.2),
            (39, 0.05), (38, 0.03), (20, 0.02)
        ]
    },
    "loot": {
        "name": "Loot", "price": 499,
        "loot": [
            (61, 42.0), (62, 20.0), (17, 15.0), (18, 10.0), (19, 6.0),
            (59, 4.0), (20, 1.5), (21, 0.8), (22, 0.4), (26, 0.2),
            (28, 0.08), (32, 0.02)
        ]
    },
    "helmet": {
        "name": "Helmet", "price": 1499,
        "loot": [
            (34, 38.0), (32, 24.0), (36, 15.0), (48, 12.0), (33, 5.0),
            (19, 3.5), (50, 1.5), (49, 0.6), (31, 0.25), (42, 0.1),
            (45, 0.03), (47, 0.015), (46, 0.003), (44, 0.001), (52, 0.0005)
        ]
    },
    "pepe": {
        "name": "Pepe", "price": 2999,
        "loot": [
            (37, 40.0), (50, 25.0), (49, 14.0), (31, 10.0), (42, 4.5),
            (45, 3.5), (47, 1.5), (46, 0.7), (44, 0.3), (52, 0.2),
            (55, 0.1), (57, 0.05), (56, 0.02), (58, 0.01), (40, 0.005)
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

_SB_URL = os.environ.get("SUPABASE_URL")
_SB_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
_sb = create_client(_SB_URL, _SB_KEY) if (create_client and _SB_URL and _SB_KEY) else None


class Base(BaseModel):
    init_data: str


class InfoReq(Base):
    case_id: str


class OpenReq(Base):
    case_id: str
    currency: str = "stars"
    count: int = 1


class SellReq(Base):
    nft_id: int


class RRSpinReq(Base):
    item_id: int
    chance: int
    currency: str = "stars"


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
            "image_url": x.get("image_url"),
            "link": x.get("link"),
        }
        for x in (data or [])
        if isinstance(x, dict) and x.get("id") is not None
    }


def _price_ton(stars: int) -> float:
    return round(stars * TON_PER_STAR, 2)


def _adjust(user_id: str, column: str, delta, min_value=None):
    if _sb is None:
        raise RuntimeError("supabase env missing")
    for attempt in range(10):
        rows = _sb.table("users").select(column).eq("id", user_id).execute().data
        if not rows:
            raise LookupError("user_not_found")
        raw = rows[0].get(column)
        old = raw if raw is not None else 0
        new = old + delta
        if isinstance(new, float):
            new = round(new, 4)
        if min_value is not None and new < min_value:
            raise ValueError("insufficient")
        q = _sb.table("users").update({column: new}).eq("id", user_id)
        q = q.is_(column, "null") if raw is None else q.eq(column, raw)
        upd = q.execute()
        if upd.data:
            val = upd.data[0].get(column)
            return float(val if val is not None else new)
        time.sleep(0.03 * (attempt + 1))
    raise RuntimeError(f"{column} update conflict")


def _add_bamboo(user_id: str, amount: int) -> float:
    return _adjust(user_id, "bamboo_balance", int(amount))


def _spend_stars(user_id: str, cost: int) -> float:
    try:
        return _adjust(user_id, "balance", -int(cost), min_value=0)
    except LookupError:
        raise HTTPException(404, ERRORS["user_not_found"])
    except ValueError:
        raise HTTPException(400, ERRORS["insufficient"])
    except RuntimeError:
        raise HTTPException(409, "Повторите попытку")


def _spend_ton(user_id: str, cost: float) -> float:
    try:
        return _adjust(user_id, "ton_balance", -float(cost), min_value=0)
    except LookupError:
        raise HTTPException(404, ERRORS["user_not_found"])
    except ValueError:
        raise HTTPException(400, ERRORS["insufficient"])
    except RuntimeError:
        raise HTTPException(409, "Повторите попытку")


def _stars_balance(user_id: str):
    if _sb is None:
        return None
    try:
        rows = _sb.table("users").select("balance").eq("id", user_id).execute().data
        return float(rows[0].get("balance") or 0) if rows else None
    except Exception:
        return None


def _ton_balance(user_id: str):
    if _sb is None:
        return None
    try:
        rows = _sb.table("users").select("ton_balance").eq("id", user_id).execute().data
        return float(rows[0].get("ton_balance") or 0) if rows else None
    except Exception:
        return None


def _rr_fee(chance: int) -> int:
    return RR_FEE_HIGH_CHANCE if chance >= RR_FEE_THRESHOLD else RR_FEE_LOW_CHANCE


def _rr_cost(price_stars, chance: int, currency: str = "stars") -> float:
    price = int(round(float(price_stars or 0)))
    base_stars = max(1, -(-(price * chance * (100 + _rr_fee(chance))) // 10000))
    if currency == "ton":
        return round(base_stars * TON_PER_STAR, 2)
    return base_stars


# ---------------- Кейсы ----------------
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
        "bamboo_min": BAMBOO_MIN,
        "bamboo_max": BAMBOO_MAX,
    }


@router.post("/case/open")
async def case_open(req: OpenReq):
    user = validate_init_data(req.init_data)
    uid = str(user["id"])
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
                "p_user_id": uid,
                "p_nft_id": nft_id,
                "p_currency": req.currency,
                "p_price": price,
            }))
        except HTTPException:
            if i == 0:
                raise
            break
        nfts.append(cat[nft_id])

    bamboo_each = [rng.randint(BAMBOO_MIN, BAMBOO_MAX) for _ in nfts]
    bamboo = sum(bamboo_each)
    bamboo_balance = None
    try:
        bamboo_balance = _add_bamboo(uid, bamboo)
    except Exception as e:
        print("bamboo credit error:", repr(e))
        bamboo = 0
        bamboo_each = []

    return {
        **res,
        "nft": nfts[0],
        "nfts": nfts,
        "bamboo": bamboo,
        "bamboo_each": bamboo_each,
        "bamboo_balance": bamboo_balance,
    }


@router.post("/case/sell_ton")
async def case_sell_ton(req: SellReq):
    user = validate_init_data(req.init_data)
    res = _check(rpc("sell_item_ton", {
        "p_user_id": str(user["id"]),
        "p_nft_id": req.nft_id,
    }))
    return res


# ---------------- Русская рулетка ----------------
@router.post("/case/rr/info")
async def rr_info(req: Base):
    validate_init_data(req.init_data)
    cat = _catalog()
    items = [cat[i] for i in RR_ITEMS if i in cat]
    return {
        "items": items,
        "min_chance": RR_MIN_CHANCE,
        "standard_min_chance": RR_STANDARD_MIN_CHANCE,
        "premium_min_chance": RR_PREMIUM_MIN_CHANCE,
        "max_chance": RR_MAX_CHANCE,
        "fee_threshold": RR_FEE_THRESHOLD,
        "fee_high_chance": RR_FEE_HIGH_CHANCE,
        "fee_low_chance": RR_FEE_LOW_CHANCE,
    }


@router.post("/case/rr/spin")
async def rr_spin(req: RRSpinReq):
    user = validate_init_data(req.init_data)
    uid = str(user["id"])

    if req.item_id not in RR_ITEMS:
        raise HTTPException(404, "Подарок не найден")
    
    # Проверка для обычных гифтов (теперь минимум 35%)
    if req.item_id in RR_ITEMS_STANDARD and req.chance < RR_STANDARD_MIN_CHANCE:
        raise HTTPException(400, f"Этот приз можно крутить только при шансе от {RR_STANDARD_MIN_CHANCE}%!")

    # Проверка для премиальных гифтов (остается от 80%)
    if req.item_id in RR_ITEMS_PREMIUM and req.chance < RR_PREMIUM_MIN_CHANCE:
        raise HTTPException(400, f"Этот приз можно крутить только при шансе от {RR_PREMIUM_MIN_CHANCE}%!")

    if not (RR_MIN_CHANCE <= req.chance <= RR_MAX_CHANCE):
        raise HTTPException(400, "Неверный шанс")
    if req.currency not in ("stars", "ton"):
        raise HTTPException(400, "Неверная валюта")

    cat = _catalog()
    item = cat.get(req.item_id)
    if not item:
        raise HTTPException(404, "Подарок не найден")

    cost = _rr_cost(item["price_stars"], req.chance, currency=req.currency)
    win = random.SystemRandom().random() * 100 < req.chance

    if win:
        _check(rpc("open_case", {
            "p_user_id": uid,
            "p_nft_id": req.item_id,
            "p_currency": req.currency,
            "p_price": cost,
        }))
    else:
        if req.currency == "ton":
            _spend_ton(uid, cost)
        else:
            _spend_stars(uid, cost)

    return {
        "win": win,
        "cost": cost,
        "chance": req.chance,
        "fee": _rr_fee(req.chance),
        "item": item,
        "balance": _stars_balance(uid),
        "ton_balance": _ton_balance(uid),
    }
