import os, random, secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

try:
    from ref import validate_init_data, supabase
except ImportError:
    from api.ref import validate_init_data, supabase

router = APIRouter()

STARS_PER_TON = float(os.environ.get("STARS_PER_TON", "80"))
MAX_BET_STARS = float(os.environ.get("PLINKO_MAX_STARS", "100000"))
MAX_BET_TON = float(os.environ.get("PLINKO_MAX_TON", "1000"))

# ---- Шансы (в процентах, сумма = 100). RTP ≈ 95% ----
OUTCOMES = [(10, 1), (5, 5), (2, 10), (1.5, 6), (1, 10), (0.5, 25), (0.2, 43)]
# Визуальные ячейки слева направо и в какие ячейки может упасть множитель
BUCKETS = [10, 2, 1, 0.5, 0.2, 0.5, 1.5, 2, 5]
SLOTS = {10: [0], 5: [8], 2: [1, 7], 1.5: [6], 1: [2], 0.5: [3, 5], 0.2: [4]}

# ---- ТАБЛИЦЫ ПОДАРКОВ (ADAPT: поправьте под свою базу, если названия другие) ----
CATALOG_TABLE = "gifts"          # key, name, image_url, price_stars
INVENTORY_TABLE = "inventory"    # id, user_id, gift_key


class DropReq(BaseModel):
    currency: str                      # stars | ton | gift
    amount: Optional[float] = 0
    gift_key: Optional[str] = None


def db():
    if supabase is None:
        raise HTTPException(500, "supabase env missing")
    return supabase


def me_id(x_init_data: str) -> str:
    return str(validate_init_data(x_init_data)["id"])


def pick_multiplier() -> float:
    r = secrets.SystemRandom().uniform(0, 100)
    acc = 0.0
    for m, p in OUTCOMES:
        acc += p
        if r < acc:
            return m
    return OUTCOMES[-1][0]


def get_row(uid: str):
    rows = db().table("users").select("balance,ton_balance").eq("id", uid).execute().data
    if not rows:
        raise HTTPException(404, "Пользователь не найден")
    return rows[0]


def _eq(q, col, val):
    return q.is_(col, "null") if val is None else q.eq(col, val)


def cas_balance(uid: str, row: dict, new_b: float, new_t: float) -> bool:
    q = db().table("users").update({"balance": new_b, "ton_balance": new_t}).eq("id", uid)
    q = _eq(_eq(q, "balance", row.get("balance")), "ton_balance", row.get("ton_balance"))
    return bool(q.execute().data)


def catalog():
    return (db().table(CATALOG_TABLE).select("key,name,image_url,price_stars")
            .gt("price_stars", 0).execute().data or [])


def take_gift(uid: str, key: str):
    """Забирает один подарок из инвентаря (ставка). Возвращает запись каталога."""
    item = next((g for g in catalog() if g["key"] == key), None)
    if not item:
        raise HTTPException(400, "Подарок не найден")
    rows = (db().table(INVENTORY_TABLE).select("id").eq("user_id", uid)
            .eq("gift_key", key).limit(1).execute().data)
    if not rows:
        raise HTTPException(400, "У вас нет этого подарка")
    done = db().table(INVENTORY_TABLE).delete().eq("id", rows[0]["id"]).execute().data
    if not done:
        raise HTTPException(409, "Подарок уже использован")
    return item


def give_gift(uid: str, key: str):
    db().table(INVENTORY_TABLE).insert({"user_id": uid, "gift_key": key}).execute()


@router.get("/plinko/me/{user_id}")
async def plinko_me(user_id: str, x_init_data: str = Header("")):
    uid = me_id(x_init_data)
    if uid != str(user_id):
        raise HTTPException(403, "forbidden")
    row = get_row(uid)
    cat = {g["key"]: g for g in catalog()}
    own = db().table(INVENTORY_TABLE).select("gift_key").eq("user_id", uid).execute().data or []
    counts = {}
    for r in own:
        counts[r["gift_key"]] = counts.get(r["gift_key"], 0) + 1
    gifts = [{**cat[k], "count": c} for k, c in counts.items() if k in cat]
    return {
        "balance": float(row.get("balance") or 0),
        "ton_balance": float(row.get("ton_balance") or 0),
        "stars_per_ton": STARS_PER_TON,
        "buckets": BUCKETS,
        "gifts": gifts,
    }


@router.post("/plinko/drop")
async def plinko_drop(req: DropReq, x_init_data: str = Header("")):
    uid = me_id(x_init_data)
    cur = req.currency
    if cur not in ("stars", "ton", "gift"):
        raise HTTPException(400, "Неверная валюта")

    amt = round(float(req.amount or 0), 4)
    if cur == "stars" and not (1 <= amt <= MAX_BET_STARS):
        raise HTTPException(400, f"Ставка в звёздах: от 1 до {int(MAX_BET_STARS)}")
    if cur == "ton" and not (0.01 <= amt <= MAX_BET_TON):
        raise HTTPException(400, f"Ставка в TON: от 0.01 до {MAX_BET_TON}")

    mult = pick_multiplier()
    slot = random.choice(SLOTS[mult])

    win_s = win_t = 0.0
    win_gift = None
    bet_gift = None

    if cur == "gift":
        bet_gift = take_gift(uid, req.gift_key or "")
        value = float(bet_gift["price_stars"]) * mult
        # самый дорогой подарок из каталога, который не дороже выигрыша; остаток звёздами
        cands = [g for g in catalog() if float(g["price_stars"]) <= value]
        win_gift = max(cands, key=lambda g: float(g["price_stars"])) if cands else None
        win_s = round(value - float(win_gift["price_stars"]), 2) if win_gift else round(value, 2)
        try:
            if win_gift:
                give_gift(uid, win_gift["key"])
        except Exception:
            give_gift(uid, bet_gift["key"])   # вернуть ставку, если выдача не удалась
            raise HTTPException(500, "Не удалось выдать выигрыш")
        if win_s > 0:
            for _ in range(5):
                row = get_row(uid)
                if cas_balance(uid, row, float(row.get("balance") or 0) + win_s,
                               float(row.get("ton_balance") or 0)):
                    break
    else:
        for _ in range(5):
            row = get_row(uid)
            b, t = float(row.get("balance") or 0), float(row.get("ton_balance") or 0)
            if cur == "stars":
                if b < amt:
                    raise HTTPException(400, "Недостаточно звёзд")
                win_s = round(amt * mult, 2)
                nb, nt = b - amt + win_s, t
            else:
                if t < amt:
                    raise HTTPException(400, "Недостаточно TON")
                win_t = round(amt * mult, 4)
                nb, nt = b, t - amt + win_t
            if cas_balance(uid, row, nb, nt):
                break
        else:
            raise HTTPException(409, "Баланс изменился, попробуйте ещё раз")

    row = get_row(uid)
    return {
        "slot": slot,
        "mult": mult,
        "win": {"stars": win_s, "ton": win_t, "gift": win_gift},
        "bet_gift": bet_gift,
        "balance": float(row.get("balance") or 0),
        "ton_balance": float(row.get("ton_balance") or 0),
    }
