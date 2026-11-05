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
            (61, 84.0),    # Слив (Звезда 5 ⭐)
            (17, 4.0),     # Утешительный / легкий плюс
            (15, 3.5),     
            (13, 3.0),     
            (1, 2.5),      
            (4, 1.5),      
            (6, 0.8),      
            (16, 0.4),     
            (9, 0.15),     
            (3, 0.04),     
            (7, 0.01)      # Крупный редкий вин
        ]
    },
    "ring": {
        "name": "Ring", "price": 199,
        "loot": [
            (62, 82.0),    # Слив (Звезда 10 ⭐)
            (18, 4.5),     # Утешительный
            (1, 3.5),      
            (4, 3.0),      
            (7, 2.5),      
            (12, 2.0),     
            (6, 1.2),      
            (13, 0.8),     
            (15, 0.5)      # Редкий вин
        ]
    },
    "lip": {
        "name": "Lip", "price": 249,
        "loot": [
            (61, 55.0),    # Основной слив (5 ⭐)
            (62, 26.0),    # Второй слив (10 ⭐)
            (9, 10.0),     # Мелкий вин
            (10, 4.5),     
            (11, 2.2),     
            (12, 1.2),     
            (13, 0.6),     
            (14, 0.3),     
            (29, 0.15),    
            (30, 0.05)     # Редкий крупный вин
        ]
    },
    "halloween": {
        "name": "Halloween", "price": 299,
        "loot": [
            (61, 48.0),    # Слив (5 ⭐)
            (62, 30.0),    # Слив (10 ⭐)
            (13, 11.0),    # Мелкий возврат
            (14, 6.0),     
            (15, 2.5),     
            (16, 1.2),     
            (17, 0.6),     
            (18, 0.3),     
            (10, 0.18),    
            (36, 0.1),     
            (39, 0.08),    
            (38, 0.03),    
            (20, 0.01)     # Очень редкий крупный вин
        ]
    },
    "loot": {
        "name": "Loot", "price": 499,
        "loot": [
            (61, 48.0),    # Слив (5 ⭐)
            (62, 30.0),    # Слив (10 ⭐)
            (17, 11.0),    # Возврат
            (18, 6.0),     
            (19, 2.5),     
            (59, 1.2),     
            (20, 0.6),     
            (21, 0.3),     
            (22, 0.18),    
            (26, 0.1),     
            (35, 0.08),    
            (32, 0.04)     # Редкий жирный вин
        ]
    },
    "helmet": {
        "name": "Helmet", "price": 1499,
        "loot": [
            # 5 Неокупаемых предметов (мягкий слив)
            (34, 42.0),    
            (32, 28.0),    
            (36, 16.0),    
            (48, 9.0),     
            (33, 4.0),     
            # 10 Окупаемых предметов (вины и редкие крупные победы)
            (19, 0.6),     
            (50, 0.25),    
            (49, 0.1),     
            (31, 0.03),    
            (42, 0.012),   
            (45, 0.005),   
            (47, 0.002),   
            (46, 0.0008),  
            (44, 0.0003),  
            (52, 0.0001)   # Супер-редкий джекпот
        ]
    },
    "pepe": {
        "name": "Pepe", "price": 2999,
        "loot": [
            # 5 Неокупаемых предметов (мягкий слив)
            (37, 45.0),    
            (50, 30.0),    
            (49, 15.0),    
            (31, 7.5),     
            (42, 2.35),    
            # 10 Окупаемых предметов (вины и ультра-редкий топ)
            (45, 0.08),    
            (47, 0.03),    
            (46, 0.01),    
            (44, 0.004),   
            (52, 0.0015),  
            (55, 0.0005),  
            (57, 0.0002),  
            (56, 0.00008), 
            (58, 0.00003), 
            (40, 0.00001)  # Эпический редкий топ
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
