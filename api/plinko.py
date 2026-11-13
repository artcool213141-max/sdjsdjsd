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
    currency: str                      # stars | ton
    amount: Optional[float] = 0


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


def settle(uid: str, bet_s: float, bet_t: float, win_s: float, win_t: float):
    """Атомарно: списать ставку и начислить выигрыш. Возвращает (звёзды, TON) после операции.
    Основной путь — SQL-функция plinko_settle (см. plinko.sql). Если её ещё нет, работает запасной путь."""
    res = None
    try:
        res = rpc("plinko_settle", {"p_user_id": uid, "p_bet_s": bet_s, "p_bet_t": bet_t,
                                    "p_win_s": win_s, "p_win_t": win_t})
    except Exception as e:
        print("plinko_settle rpc error:", repr(e))
    if isinstance(res, list):
        res = res[0] if res else None
    if isinstance(res, dict) and "success" in res:
        if not res["success"]:
            raise HTTPException(400, "Недостаточно средств")
        return float(res.get("balance") or 0), float(res.get("ton_balance") or 0)

    print("plinko_settle недоступна — запасной путь (выполните plinko.sql в Supabase)")
    row = get_row(uid)
    b, t = float(row.get("balance") or 0), float(row.get("ton_balance") or 0)
    if b < bet_s:
        raise HTTPException(400, "Недостаточно звёзд")
    if t < bet_t:
        raise HTTPException(400, "Недостаточно TON")
    nb, nt = round(b - bet_s + win_s, 4), round(t - bet_t + win_t, 6)
    db().table("users").update({"balance": nb, "ton_balance": nt}).eq("id", uid).execute()
    return nb, nt


@router.get("/plinko/me/{user_id}")
async def plinko_me(user_id: str, x_init_data: str = Header("")):
    uid = me_id(x_init_data)
    if uid != str(user_id):
        raise HTTPException(403, "forbidden")
    row = get_row(uid)
    return {
        "balance": float(row.get("balance") or 0),
        "ton_balance": float(row.get("ton_balance") or 0),
        "stars_per_ton": STARS_PER_TON,
        "buckets": BUCKETS,
    }


@router.post("/plinko/drop")
async def plinko_drop(req: DropReq, x_init_data: str = Header("")):
    uid = me_id(x_init_data)
    cur = req.currency
    amt = round(float(req.amount or 0), 4)
    if cur == "stars":
        if not (1 <= amt <= MAX_BET_STARS):
            raise HTTPException(400, f"Ставка в звёздах: от 1 до {int(MAX_BET_STARS)}")
    elif cur == "ton":
        if not (0.01 <= amt <= MAX_BET_TON):
            raise HTTPException(400, f"Ставка в TON: от 0.01 до {MAX_BET_TON}")
    else:
        raise HTTPException(400, "Неверная валюта")

    mult = pick_multiplier()
    slot = random.choice(SLOTS[mult])
    win_s = round(amt * mult, 2) if cur == "stars" else 0.0
    win_t = round(amt * mult, 4) if cur == "ton" else 0.0
    bet_s = amt if cur == "stars" else 0.0
    bet_t = amt if cur == "ton" else 0.0

    balance, ton_balance = settle(uid, bet_s, bet_t, win_s, win_t)
    return {
        "slot": slot,
        "mult": mult,
        "win": {"stars": win_s, "ton": win_t},
        "balance": balance,
        "ton_balance": ton_balance,
    }
