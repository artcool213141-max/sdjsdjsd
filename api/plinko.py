import os, random, secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

try:
    from ref import validate_init_data, rpc, supabase
except ImportError:
    from api.ref import validate_init_data, rpc, supabase

router = APIRouter()

STARS_PER_TON = float(os.environ.get("STARS_PER_TON", "80"))
MAX_BET_STARS = float(os.environ.get("PLINKO_MAX_STARS", "100000"))
MAX_BET_TON = float(os.environ.get("PLINKO_MAX_TON", "1000"))

# ---- Шансы (в процентах, сумма = 100). RTP ≈ 95% ----
OUTCOMES = [(10, 1), (5, 5), (2, 10), (1.5, 6), (1, 10), (0.5, 25), (0.2, 43)]
BUCKETS = [10, 2, 1, 0.5, 0.2, 0.5, 1.5, 2, 5]
SLOTS = {10: [0], 5: [8], 2: [1, 7], 1.5: [6], 1: [2], 0.5: [3, 5], 0.2: [4]}


class DropReq(BaseModel):
    currency: str                      # stars | ton | gift
    amount: Optional[float] = 0
    gift_key: Optional[str] = None     # id NFT из nft_catalog


def db():
    if supabase is None:
        raise HTTPException(500, "supabase env missing")
    return supabase


def me_id(x_init_data: str) -> str:
    return str(validate_init_data(x_init_data)["id"])


def r1(res):
    """rpc может вернуть список или словарь."""
    if isinstance(res, list):
        return res[0] if res else {}
    return res or {}


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


def add_stars(uid: str, amount: float):
    for _ in range(5):
        row = get_row(uid)
        if cas_balance(uid, row, float(row.get("balance") or 0) + amount,
                       float(row.get("ton_balance") or 0)):
            return True
    return False


# ---------- подарки (nft_catalog / инвентарь через ваши RPC из inventory.py) ----------
def _item(c):
    nid = c.get("id")
    return {"key": str(nid), "id": nid, "name": c.get("name") or f"NFT #{nid}",
            "image_url": f"/inventory/preview/{nid}",
            "price_stars": float(c.get("price_stars") or 0)}


def catalog():
    try:
        rows = rpc("get_catalog", {}) or []
    except Exception as e:
        print("plinko catalog error:", repr(e))
        return []
    return [_item(c) for c in rows if float(c.get("price_stars") or 0) > 0]


def my_inventory(uid: str):
    """[{id, count}] из rpc get_inventory (разные имена полей учтены)."""
    try:
        rows = rpc("get_inventory", {"p_user_id": uid}) or []
    except Exception as e:
        print("plinko inventory error:", repr(e))
        return []
    out = {}
    for it in rows:
        nid = it.get("nft_id", it.get("id"))
        if nid is None:
            continue
        cnt = it.get("count", it.get("qty", it.get("quantity", 1))) or 1
        out[int(nid)] = out.get(int(nid), 0) + int(cnt)
    return [{"id": k, "count": v} for k, v in out.items()]


def take_gift(uid: str, nft_id: int):
    """Ставка подарком: подарок убирается из инвентаря через sell_item (атомарно в БД),
    а полученные за него звёзды сразу идут в ставку. Возвращает (предмет, сумма ставки в звёздах)."""
    item = next((g for g in catalog() if g["id"] == nft_id), None)
    if not item:
        raise HTTPException(400, "Подарок недоступен для ставки")
    if not any(i["id"] == nft_id and i["count"] > 0 for i in my_inventory(uid)):
        raise HTTPException(400, "У вас нет этого подарка")
    before = get_row(uid)
    res = r1(rpc("sell_item", {"p_user_id": uid, "p_nft_id": nft_id}))
    if not res.get("success"):
        raise HTTPException(400, "Подарок уже использован или продан")
    after = get_row(uid)
    credited = float(after.get("balance") or 0) - float(before.get("balance") or 0)
    if credited <= 0:
        credited = item["price_stars"]
    return item, round(credited, 2)


def give_gift(uid: str, nft_id: int) -> bool:
    try:
        res = r1(rpc("grant_item", {"p_user_id": uid, "p_nft_id": int(nft_id)}))
        return res.get("success", True) is not False
    except Exception as e:
        print("plinko grant error:", repr(e))
        return False


@router.get("/plinko/me/{user_id}")
async def plinko_me(user_id: str, x_init_data: str = Header("")):
    uid = me_id(x_init_data)
    if uid != str(user_id):
        raise HTTPException(403, "forbidden")
    row = get_row(uid)
    cat = {g["id"]: g for g in catalog()}
    gifts = [{**cat[i["id"]], "count": i["count"]} for i in my_inventory(uid) if i["id"] in cat]
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

    bet_gift, bet_s, bet_t = None, 0.0, 0.0
    if cur == "gift":
        try:
            nft_id = int(req.gift_key)
        except (TypeError, ValueError):
            raise HTTPException(400, "Неверный подарок")
        bet_gift, bet_s = take_gift(uid, nft_id)   # подарок уже списан
    elif cur == "stars":
        bet_s = amt
    else:
        bet_t = amt

    mult = pick_multiplier()
    slot = random.choice(SLOTS[mult])

    win_s = win_t = 0.0
    win_gift = None
    if cur == "ton":
        win_t = round(bet_t * mult, 4)
    else:
        value = bet_s * mult
        if cur == "gift":
            # самый дорогой подарок, который не дороже выигрыша; остаток — звёздами
            cands = [g for g in catalog() if g["price_stars"] <= value]
            win_gift = max(cands, key=lambda g: g["price_stars"]) if cands else None
            win_s = round(value - win_gift["price_stars"], 2) if win_gift else round(value, 2)
        else:
            win_s = round(value, 2)

    for _ in range(5):
        row = get_row(uid)
        b, t = float(row.get("balance") or 0), float(row.get("ton_balance") or 0)
        if b < bet_s or t < bet_t:
            raise HTTPException(400, "Недостаточно средств")
        if cas_balance(uid, row, b - bet_s + win_s, t - bet_t + win_t):
            break
    else:
        raise HTTPException(409, "Баланс изменился, попробуйте ещё раз")

    if win_gift and not give_gift(uid, win_gift["id"]):
        add_stars(uid, win_gift["price_stars"])   # не удалось выдать — компенсируем звёздами
        win_s = round(win_s + win_gift["price_stars"], 2)
        win_gift = None

    row = get_row(uid)
    strip = lambda g: None if not g else {k: v for k, v in g.items() if k != "id"}
    return {
        "slot": slot,
        "mult": mult,
        "win": {"stars": win_s, "ton": win_t, "gift": strip(win_gift)},
        "bet_gift": strip(bet_gift),
        "balance": float(row.get("balance") or 0),
        "ton_balance": float(row.get("ton_balance") or 0),
    }
