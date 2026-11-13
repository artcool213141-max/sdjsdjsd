"""
api/rolls.py — общая рулетка-джекпот «Rolls» PandaGift.

Как работает:
  • Раунды общие для всех и живут в таблице roll_rounds (время считает сервер).
  • Игроки ставят Stars / TON / подарок из инвентаря. Вес ставки в колесе — её
    стоимость в звёздах (1 TON = 140 ⭐, подарок = price_stars из nft_catalog).
  • Когда окно ставок закрылось, сервер один раз выбирает победителя
    (secrets.SystemRandom) и угол остановки. Клиент заранее ничего не знает.
  • Победитель забирает весь банк: звёзды, TON и сами подарки (точные предметы
    из ставок). Если за столом меньше двух разных игроков — все ставки возвращаются.
  • Баланс/инвентарь меняет только сервер: crash_adjust (RPC) и users.inventory.

Всё общее (проверка Telegram, баланс, инвентарь, каталог, превью) берётся из
api/crash.py, чтобы была одна система.

Подключение: в api/index.py добавь "rolls" в кортеж имён роутеров.
"""
import hashlib
import json
import os
import secrets
import time
from typing import Optional, Union

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

try:
    import crash as C
except ImportError:  # запуск как пакет api.*
    from api import crash as C

_core = APIRouter()

# ---------------- настройки ----------------
BET_WINDOW_MS = 20_000       # окно ставок
SPIN_MS = 7_000              # длительность вращения колеса
SHOW_MS = 4_000              # сколько показывать победителя
BET_CUTOFF_MS = 1_000        # за сколько мс до старта ставки уже не принимаем
DRAW_GRACE_MS = 1_500        # через сколько после старта вращения выбираем победителя
STARS_PER_TON = 140.0        # курс: 140 ⭐ = 1 TON
MIN_PLAYERS = 2              # минимум разных игроков, иначе возврат
MAX_BETS_USER = 5            # ставок одного игрока в раунде
MAX_BETS_ROUND = 40          # ставок в раунде всего
MIN_BET = {"stars": 1.0, "ton": 0.01}
HISTORY_LEN = 8
# Комиссия казино с банка (только Stars/TON, подарки не трогаем). По умолчанию 0%.
FEE_PCT = min(20.0, max(0.0, float(os.environ.get("ROLLS_FEE_PCT") or 0)))


# ---------------- утилиты ----------------
def _db():
    return C._db()


def uk(user_id) -> str:
    """Короткий стабильный ключ игрока для клиента (настоящий user_id наружу не отдаём)."""
    return hashlib.sha1(C.SECRET + b"|rolls|" + str(user_id).encode()).hexdigest()[:8]


def _clean_avatar(url) -> Optional[str]:
    url = (url or "").strip()
    return url[:300] if url.startswith("https://") else None


def value_of(currency: str, amount: float) -> float:
    return round(amount * STARS_PER_TON, 2) if currency == "ton" else round(amount, 2)


# ---------------- раунды ----------------
def build_round(round_id: int, bet_start: int) -> dict:
    spin_at = bet_start + BET_WINDOW_MS
    return {
        "round_id": round_id,
        "bet_start": bet_start,
        "spin_at": spin_at,
        "end_at": spin_at + SPIN_MS + SHOW_MS,
        "status": "open",
    }


_cache = {"row": None}
_row_cache = {}
_hist_cache = {"t": 0.0, "v": []}
_bets_cache = {}


def _latest(db):
    res = db.table("roll_rounds").select("*").order("round_id", desc=True).limit(1).execute()
    return res.data[0] if res.data else None


def _round_row(rid: int, max_age: float = 0.3):
    c = _row_cache.get(rid)
    if c and time.time() - c[0] < max_age:
        return c[1]
    res = _db().table("roll_rounds").select("*").eq("round_id", rid).limit(1).execute()
    row = res.data[0] if res.data else None
    if len(_row_cache) > 30:
        _row_cache.clear()
    _row_cache[rid] = (time.time(), row)
    return row


def _set_round(rid: int, fields: dict):
    _db().table("roll_rounds").update(fields).eq("round_id", rid).execute()
    _row_cache.pop(rid, None)


def current_round() -> dict:
    n = C.now_ms()
    row = _cache["row"]
    if row and row["end_at"] > n:
        return row
    db = _db()
    last = _latest(db)
    for _ in range(8):
        if last is not None and last["end_at"] > n:
            break
        if last is None:
            nr = build_round(1, n)
        else:
            try:
                finalize(last)
            except Exception as e:
                print("rolls finalize error:", repr(e))
            start = last["end_at"] if n - last["end_at"] < 60_000 else n
            nr = build_round(int(last["round_id"]) + 1, start)
        db.table("roll_rounds").upsert(nr, on_conflict="round_id", ignore_duplicates=True).execute()
        last = _latest(db)
    _cache["row"] = last
    return last


# ---------------- ставки: чтение ----------------
def _bets_all(rid: int) -> list:
    res = _db().table("roll_bets").select("*").eq("round_id", rid).order("id").execute()
    return res.data or []


PUB_COLS = "id,user_id,username,avatar_url,currency,amount,value_stars,gift_name,gift_image"


def _bets_public(rid: int) -> list:
    c = _bets_cache.get(rid)
    if c and time.time() - c[0] < 0.3:
        return c[1]
    res = _db().table("roll_bets").select(PUB_COLS).eq("round_id", rid).order("id").execute()
    rows = res.data or []
    if len(_bets_cache) > 30:
        _bets_cache.clear()
    _bets_cache[rid] = (time.time(), rows)
    return rows


def _pot(bets: list) -> dict:
    return {
        "pot_value": round(sum(float(b["value_stars"]) for b in bets), 2),
        "pot_stars": round(sum(float(b["amount"]) for b in bets if b["currency"] == "stars"), 2),
        "pot_ton": round(sum(float(b["amount"]) for b in bets if b["currency"] == "ton"), 2),
        "pot_gifts": sum(1 for b in bets if b["currency"] == "gift"),
    }


# ---------------- выплаты ----------------
def _settle_ids(ids: list) -> list:
    """Атомарно помечает ставки выплаченными. Возвращает только те, что мы реально «захватили»."""
    if not ids:
        return []
    res = _db().table("roll_bets").update({"settled": True}).in_("id", ids).eq("settled", False).execute()
    return res.data or []


def _unsettle_ids(ids: list):
    if ids:
        _db().table("roll_bets").update({"settled": False}).in_("id", ids).execute()


def _credit(uid: str, rows: list, fee_pct: float):
    """Начисляет игроку содержимое ставок: звёзды, TON и сами подарки."""
    stars = ton = 0.0
    gifts = []
    for r in rows:
        if r["currency"] == "stars":
            stars += float(r["amount"])
        elif r["currency"] == "ton":
            ton += float(r["amount"])
        elif r.get("gift_item"):
            try:
                gifts.append(json.loads(r["gift_item"]))
            except Exception:
                print("rolls: битый gift_item у ставки", r["id"])
    keep = 1 - fee_pct / 100.0
    if stars > 0 or ton > 0:
        if C.adjust(uid, C.r2(stars * keep), C.r2(ton * keep)) is None:
            raise RuntimeError("adjust failed")
    if gifts:
        try:
            ok = C.inv_mutate(uid, lambda items, mode: (items + gifts, True))
        except Exception as e:
            ok = None
            print("rolls: gift credit exception:", repr(e))
        if not ok:
            # звёзды уже начислены — повторно не начисляем, просто громко логируем
            print(f"rolls: НЕ УДАЛОСЬ выдать подарки user={uid}: {gifts}")


def pay_winner(rid: int, winner_uid: str):
    rows = [b for b in _bets_all(rid) if not b.get("settled")]
    claimed = _settle_ids([b["id"] for b in rows])
    if not claimed:
        return
    try:
        _credit(winner_uid, claimed, FEE_PCT)
    except Exception:
        _unsettle_ids([b["id"] for b in claimed])
        raise


def refund_all(rid: int):
    rows = [b for b in _bets_all(rid) if not b.get("settled")]
    claimed = _settle_ids([b["id"] for b in rows])
    by_user = {}
    for b in claimed:
        by_user.setdefault(str(b["user_id"]), []).append(b)
    for uid, items in by_user.items():
        try:
            _credit(uid, items, 0.0)
        except Exception as e:
            print("rolls refund error:", uid, repr(e))
            _unsettle_ids([b["id"] for b in items])


# ---------------- розыгрыш ----------------
def draw_round(row: dict):
    rid = int(row["round_id"])
    claim = _db().table("roll_rounds").update({"status": "drawing"}) \
        .eq("round_id", rid).eq("status", "open").execute()
    _row_cache.pop(rid, None)
    if not claim.data:
        return  # кто-то уже розыгрывает

    bets = _bets_all(rid)
    if not bets:
        _set_round(rid, {"status": "empty"})
        return

    pot = _pot(bets)
    users = {str(b["user_id"]) for b in bets}
    if len(users) < MIN_PLAYERS:
        _set_round(rid, {"status": "refund", **pot})
        refund_all(rid)
        return

    rng = secrets.SystemRandom()
    total = sum(float(b["value_stars"]) for b in bets)
    pick = rng.random() * total
    acc = 0.0
    win, a0, a1 = bets[-1], 0.0, 360.0
    for i, b in enumerate(bets):
        start = acc / total * 360.0
        acc += float(b["value_stars"])
        end = acc / total * 360.0
        if pick < acc or i == len(bets) - 1:
            win, a0, a1 = b, start, end
            break
    angle = a0 + (a1 - a0) * (0.12 + 0.76 * rng.random())   # не на самом краю сектора

    _set_round(rid, {
        "status": "drawn",
        "winner_bet_id": win["id"],
        "winner_user_id": str(win["user_id"]),
        "winner_username": win.get("username"),
        "winner_avatar": win.get("avatar_url"),
        "result_angle": round(angle, 3),
        **pot,
    })
    try:
        pay_winner(rid, str(win["user_id"]))
        _set_round(rid, {"status": "done"})
    except Exception as e:
        # статус остаётся 'drawn' — выплата будет дожата в finalize()
        print("rolls payout error:", repr(e))


def finalize(row: dict):
    """Доводит закончившийся раунд до конца (в т.ч. после сбоев)."""
    rid = int(row["round_id"])
    fresh = _round_row(rid, 0.0) or row
    st = fresh.get("status")
    if st == "open":
        draw_round(fresh)
    elif st == "drawing":      # упали посреди розыгрыша, победитель не выбран — возвращаем всё
        _set_round(rid, {"status": "refund"})
        refund_all(rid)
    elif st == "drawn":
        pay_winner(rid, str(fresh["winner_user_id"]))
        _set_round(rid, {"status": "done"})
    elif st == "refund":
        refund_all(rid)


def history_of() -> list:
    if time.time() - _hist_cache["t"] < 3.0:
        return _hist_cache["v"]
    res = _db().table("roll_rounds").select("round_id,winner_username,pot_value,status") \
        .in_("status", ["drawn", "done"]).order("round_id", desc=True).limit(HISTORY_LEN).execute()
    out = [{"round_id": r["round_id"], "username": r.get("winner_username") or "Игрок",
            "pot_value": float(r.get("pot_value") or 0)} for r in (res.data or [])]
    _hist_cache.update(t=time.time(), v=out)
    return out


# ---------------- API ----------------
@_core.get("/state")
def state(user_id: Optional[str] = None):
    row = current_round()
    n = C.now_ms()
    rid = int(row["round_id"])

    if n >= row["spin_at"] + DRAW_GRACE_MS:
        fresh = _round_row(rid)
        if fresh and fresh.get("status") == "open":
            try:
                draw_round(fresh)
            except Exception as e:
                print("rolls draw error:", repr(e))
            fresh = _round_row(rid, 0.0)
        row = fresh or row

    bets = _bets_public(rid)
    pub = []
    for b in bets:
        pub.append({
            "id": b["id"], "uk": uk(b["user_id"]),
            "username": b.get("username") or "Игрок", "avatar_url": b.get("avatar_url"),
            "currency": b["currency"], "amount": float(b["amount"]),
            "value_stars": float(b["value_stars"]),
            "gift_name": b.get("gift_name"), "gift_image": b.get("gift_image"),
            "mine": bool(user_id) and str(b["user_id"]) == str(user_id),
        })

    out = {
        "round_id": rid,
        "bet_start": row["bet_start"],
        "spin_at": row["spin_at"],
        "spin_end": row["spin_at"] + SPIN_MS,
        "end_at": row["end_at"],
        "bet_window_ms": BET_WINDOW_MS,
        "spin_ms": SPIN_MS,
        "stars_per_ton": STARS_PER_TON,
        "fee_pct": FEE_PCT,
        "min_bet": MIN_BET,
        "bets": pub,
        "history": history_of(),
        "result": None,
    }
    st = row.get("status")
    if st in ("drawn", "done"):
        out["result"] = {
            "status": "win",
            "angle": float(row["result_angle"]),
            "winner_bet_id": row["winner_bet_id"],
            "winner_uk": uk(row["winner_user_id"]),
            "winner_username": row.get("winner_username") or "Игрок",
            "winner_avatar": row.get("winner_avatar"),
            "winner_mine": bool(user_id) and str(row["winner_user_id"]) == str(user_id),
            "pot": {
                "pot_value": float(row.get("pot_value") or 0),
                "pot_stars": float(row.get("pot_stars") or 0),
                "pot_ton": float(row.get("pot_ton") or 0),
                "pot_gifts": int(row.get("pot_gifts") or 0),
            },
        }
    elif st == "refund":
        out["result"] = {"status": "refund"}
    elif st == "empty":
        out["result"] = {"status": "empty"}
    out["server_time"] = C.now_ms()
    return out


@_core.get("/me/{user_id}")
def me(user_id: str, x_init_data: Optional[str] = Header(None)):
    # баланс + инвентарь (подарки без Star, с превью) — ровно как в краше
    return C.me(user_id, x_init_data)


class BetReq(BaseModel):
    user_id: Union[str, int]
    username: Optional[str] = "Игрок"
    avatar_url: Optional[str] = None
    currency: str                       # stars | ton | gift
    amount: float = 0
    gift_key: Optional[str] = None


def _refund_own(uid: str, cur: str, amount: float, gift_item):
    if cur == "gift":
        C.give_back_item(uid, gift_item)
    else:
        C.adjust(uid, amount if cur == "stars" else 0, amount if cur == "ton" else 0)


@_core.post("/bet")
def place_bet(req: BetReq, x_init_data: Optional[str] = Header(None)):
    uid = str(req.user_id)
    C.verify_user(uid, x_init_data)
    db = _db()
    row = current_round()
    if C.now_ms() >= row["spin_at"] - BET_CUTOFF_MS:
        raise HTTPException(status_code=400, detail="Приём ставок закрыт, дождитесь следующего раунда")
    rid = int(row["round_id"])
    cur = req.currency
    if cur not in ("stars", "ton", "gift"):
        raise HTTPException(status_code=400, detail="Неверная валюта")

    existing = db.table("roll_bets").select("id,user_id").eq("round_id", rid).execute().data or []
    if len(existing) >= MAX_BETS_ROUND:
        raise HTTPException(status_code=400, detail="В раунде максимум ставок, дождитесь следующего")
    if sum(1 for b in existing if str(b["user_id"]) == uid) >= MAX_BETS_USER:
        raise HTTPException(status_code=400, detail=f"Не больше {MAX_BETS_USER} ставок за раунд")

    username = (req.username or "Игрок").strip()[:24] or "Игрок"
    gift_item = None
    gift_info = None
    if cur == "gift":
        if not req.gift_key:
            raise HTTPException(status_code=400, detail="Не выбран подарок")

        def take(items, mode):
            for i, it in enumerate(items):
                if C.item_key(it) == req.gift_key:
                    return items[:i] + items[i + 1:], it
            return None

        got = C.inv_mutate(uid, take)
        if not got:
            raise HTTPException(status_code=400, detail="Подарок не найден в инвентаре")
        gift_item = got[1]
        gift_info = C.resolve_item(gift_item)
        if C.is_star(gift_info["name"]):
            C.give_back_item(uid, gift_item)
            raise HTTPException(status_code=400, detail="Этот подарок нельзя поставить")
        if gift_info["price_stars"] <= 0:
            C.give_back_item(uid, gift_item)
            raise HTTPException(status_code=400, detail="У этого подарка нет цены в каталоге")
        amount = C.r2(gift_info["price_stars"])
    else:
        amount = C.r2(req.amount)
        if amount < MIN_BET[cur]:
            raise HTTPException(status_code=400, detail=f"Минимальная ставка — {MIN_BET[cur]}")
        bal = C.adjust(uid, -amount if cur == "stars" else 0, -amount if cur == "ton" else 0)
        if bal is None:
            raise HTTPException(status_code=400, detail="Недостаточно средств")

    record = {
        "round_id": rid, "user_id": uid, "username": username,
        "avatar_url": _clean_avatar(req.avatar_url),
        "currency": cur, "amount": amount, "value_stars": value_of(cur, amount),
    }
    if gift_info:
        record["gift_name"] = gift_info["name"]
        record["gift_image"] = gift_info["image_url"]
        record["gift_item"] = json.dumps(gift_item, ensure_ascii=False)

    try:
        ins = db.table("roll_bets").insert(record).execute()
        new_id = ins.data[0]["id"]
    except Exception as e:
        print("rolls bet insert error:", repr(e))
        _refund_own(uid, cur, amount, gift_item)
        raise HTTPException(status_code=400, detail="Не удалось принять ставку")

    # Если пока писали ставку, окно уже закрылось — откатываем (розыгрыш идёт позже spin_at + DRAW_GRACE_MS).
    if C.now_ms() >= row["spin_at"]:
        try:
            db.table("roll_bets").delete().eq("id", new_id).execute()
            _refund_own(uid, cur, amount, gift_item)
        except Exception as e:
            print("rolls late rollback error:", repr(e))
        raise HTTPException(status_code=400, detail="Приём ставок закрыт, дождитесь следующего раунда")

    _bets_cache.pop(rid, None)
    return {"ok": True, "round_id": rid, "bet_id": new_id, "amount": amount, "currency": cur,
            "value_stars": record["value_stars"], **C.get_balances(uid)}


# Доступен и по /rolls/..., и по /api/rolls/... (как crash)
router = APIRouter(tags=["rolls"])
router.include_router(_core, prefix="/rolls")
router.include_router(_core, prefix="/api/rolls")
