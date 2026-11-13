"""
api/crash.py — общий «прямой эфир» краш-игры PandaGift.

Одна ракета на весь бот: раунды хранятся в таблице crash_rounds, время и точка
краха считает сервер. Клиент точку краха заранее не знает (она отдаётся только
после падения). Баланс/инвентарь меняет только сервер через service-ключ.

Подключение: в api/index.py добавь "crash" в кортеж имён роутеров.
"""
import hashlib
import hmac
import json
import math
import os
import re
import time
from typing import Optional, Union
from urllib.parse import parse_qsl

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from supabase import create_client, Client

_core = APIRouter()

_url = os.environ.get("SUPABASE_URL")
_key = os.environ.get("SUPABASE_SERVICE_KEY")
sb: Optional[Client] = create_client(_url, _key) if _url and _key else None

# ---------------- настройки ----------------
BET_WINDOW_MS = 5_000       # окно ставок между полётами
CRASH_SHOW_MS = 3_000       # сколько показывать крах
BET_CUTOFF_MS = 300         # за сколько мс до взлёта ставки уже не принимаем
GROWTH = 0.25               # m(t) = e^(GROWTH * t_sec)
MIN_BET = {"stars": 1.0, "ton": 0.01}
HISTORY_LEN = 14
SECRET = (os.environ.get("CRASH_SECRET") or _key or "dev-secret").encode()


# ---------------- утилиты ----------------
def now_ms() -> int:
    return int(time.time() * 1000)


def r2(x: float) -> float:
    return round(float(x) + 1e-9, 2)


def _db() -> Client:
    if sb is None:
        raise HTTPException(status_code=500, detail="supabase env missing")
    return sb


def verify_user(user_id: str, init_data: Optional[str]) -> None:
    """Проверяет подпись Telegram initData. Если BOT_TOKEN не задан — проверка пропускается."""
    token = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return
    if not init_data:
        raise HTTPException(status_code=401, detail="Откройте игру из Telegram")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    got_hash = pairs.pop("hash", None)
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not got_hash or not hmac.compare_digest(calc, got_hash):
        raise HTTPException(status_code=401, detail="Неверная подпись Telegram")
    try:
        tg_user = json.loads(pairs.get("user", "{}"))
    except Exception:
        tg_user = {}
    if str(tg_user.get("id")) != str(user_id):
        raise HTTPException(status_code=401, detail="user_id не совпадает")
    try:
        if time.time() - int(pairs.get("auth_date", "0")) > 86400:
            raise HTTPException(status_code=401, detail="Сессия устарела, перезапустите приложение")
    except ValueError:
        raise HTTPException(status_code=401, detail="Плохая auth_date")


# ---------------- раунды ----------------
def crash_point(round_id: int) -> float:
    h = hmac.new(SECRET, str(round_id).encode(), hashlib.sha256).digest()
    a = int.from_bytes(h[:8], "big") / 2 ** 64
    b = int.from_bytes(h[8:16], "big") / 2 ** 64
    v = (2 + b * 10) if a > 0.93 else (1.01 + b * 1.5)
    return math.floor(v * 100) / 100


def mult_at(ms_since_fly: float) -> float:
    return math.floor(math.exp(GROWTH * max(0.0, ms_since_fly) / 1000.0) * 100) / 100


def build_round(round_id: int, bet_start: int) -> dict:
    cp = crash_point(round_id)
    fly_start = bet_start + BET_WINDOW_MS
    crash_at = fly_start + int(math.log(cp) / GROWTH * 1000)
    return {
        "round_id": round_id,
        "bet_start": bet_start,
        "fly_start": fly_start,
        "crash_at": crash_at,
        "end_at": crash_at + CRASH_SHOW_MS,
        "crash_point": cp,
    }


_round_cache = {"row": None}
_settled = set()


def _latest_round(db: Client):
    res = db.table("crash_rounds").select("*").order("round_id", desc=True).limit(1).execute()
    return res.data[0] if res.data else None


def current_round() -> dict:
    n = now_ms()
    row = _round_cache["row"]
    if row and row["end_at"] > n:
        return row
    db = _db()
    last = _latest_round(db)
    for _ in range(8):
        if last is not None and last["end_at"] > n:
            break
        if last is None:
            nr = build_round(1, n)
        else:
            try:
                settle_round(last)
            except Exception as e:
                print("settle_round error:", repr(e))
            start = last["end_at"] if n - last["end_at"] < 60_000 else n
            nr = build_round(int(last["round_id"]) + 1, start)
        db.table("crash_rounds").upsert(nr, on_conflict="round_id", ignore_duplicates=True).execute()
        last = _latest_round(db)
    _round_cache["row"] = last
    return last


# ---------------- баланс ----------------
def adjust(user_id: str, stars: float = 0.0, ton: float = 0.0):
    """Атомарное изменение баланса через SQL-функцию crash_adjust. None — не хватает средств / нет юзера."""
    res = _db().rpc("crash_adjust", {"p_user": str(user_id), "p_stars": r2(stars), "p_ton": r2(ton)}).execute()
    data = res.data
    if isinstance(data, list):
        return data[0] if data else None
    return data or None


def get_balances(user_id: str) -> dict:
    res = _db().table("users").select("balance, ton_balance").eq("id", str(user_id)).limit(1).execute()
    row = res.data[0] if res.data else {}
    return {"balance": float(row.get("balance") or 0), "ton_balance": float(row.get("ton_balance") or 0)}


def ensure_user(user_id: str, username: str = "Unknown"):
    _db().table("users").upsert(
        {"id": str(user_id), "balance": 0, "username": username}, on_conflict="id", ignore_duplicates=True
    ).execute()


# ---------------- каталог и инвентарь ----------------
_catalog_cache = {"t": 0.0, "rows": []}


def get_catalog() -> list:
    if time.time() - _catalog_cache["t"] > 60 or not _catalog_cache["rows"]:
        try:
            res = _db().table("nft_catalog").select("id,name,link,price_stars,price_ton,image_url,active").execute()
            _catalog_cache["rows"] = [c for c in (res.data or []) if c.get("active", True)]
            _catalog_cache["t"] = time.time()
        except Exception as e:
            print("catalog error:", repr(e))
    return _catalog_cache["rows"]


def _slug(link) -> Optional[str]:
    m = re.search(r"t\.me/nft/([A-Za-z0-9_-]+)", str(link or ""))
    return m.group(1).lower() if m else None


PREVIEW_BASE = (os.environ.get("PREVIEW_BASE") or "https://sdjsdjsd.vercel.app").rstrip("/")


def preview_url(cat_id) -> str:
    """Превью подарка — тот же эндпоинт, что использует система кейсов (/inventory/preview/<id>)."""
    return f"{PREVIEW_BASE}/inventory/preview/{cat_id}" if cat_id is not None else ""


def is_star(name) -> bool:
    """Подарки с именем Star — это просто звёзды, как подарок их не показываем и не ставим."""
    return str(name or "").strip().lower() == "star"


def inv_parse(raw):
    """users.inventory (text) -> (items, mode). Поддерживает JSON-массив и список через запятую."""
    if raw is None:
        return [], "json"
    if isinstance(raw, list):
        return list(raw), "json"
    s = str(raw).strip()
    if not s:
        return [], "json"
    try:
        p = json.loads(s)
        if isinstance(p, list):
            return p, "json"
        if isinstance(p, dict):
            return [p], "json"
    except Exception:
        pass
    return [x.strip() for x in re.split(r"[,\n;]+", s) if x.strip()], "csv"


def inv_dump(items: list, mode: str) -> str:
    if mode == "json":
        return json.dumps(items, ensure_ascii=False)
    return ",".join(str(i) for i in items)


def item_key(item) -> str:
    return hashlib.md5(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def resolve_item(item) -> dict:
    cat = get_catalog()
    found = None
    extra = {}
    if isinstance(item, dict):
        extra = item
        for k in ("nft_id", "catalog_id", "gift_id", "id"):
            if item.get(k) is not None:
                found = next((c for c in cat if str(c["id"]) == str(item[k])), None)
                if found:
                    break
        if not found and item.get("link"):
            sl = _slug(item["link"])
            found = next((c for c in cat if _slug(c.get("link")) == sl), None)
        nm = item.get("name") or item.get("title")
        if not found and nm:
            found = next((c for c in cat if str(c["name"]).lower() == str(nm).lower()), None)
    else:
        s = str(item).strip()
        if s.isdigit():
            found = next((c for c in cat if str(c["id"]) == s), None)
        if not found and _slug(s):
            found = next((c for c in cat if _slug(c.get("link")) == _slug(s)), None)
        if not found:
            found = next((c for c in cat if str(c["name"]).lower() == s.lower()), None)
        extra = {"name": s}
    price = found["price_stars"] if found else (extra.get("price_stars") or extra.get("price") or 0)
    img = preview_url(found["id"]) if found else ""
    if not img:
        img = extra.get("image_url") or extra.get("img") or ""
    return {
        "key": item_key(item),
        "id": found["id"] if found else None,
        "name": (found["name"] if found else (extra.get("name") or extra.get("title"))) or "Подарок",
        "price_stars": float(price or 0),
        "image_url": img,
    }


def new_item_like(cat: dict, items: list, mode: str):
    """Формирует запись нового подарка в том же стиле, как уже лежат в инвентаре."""
    img = preview_url(cat["id"])
    sample = items[0] if items else None
    if isinstance(sample, dict):
        out = {}
        for k in sample:
            lk = k.lower()
            if lk in ("id", "nft_id", "catalog_id", "gift_id"):
                out[k] = cat["id"]
            elif lk in ("name", "title"):
                out[k] = cat["name"]
            elif lk == "link":
                out[k] = cat.get("link")
            elif lk in ("price_stars", "price"):
                out[k] = cat["price_stars"]
            elif lk == "price_ton":
                out[k] = cat.get("price_ton")
            elif lk in ("image_url", "img", "image"):
                out[k] = img
        return out
    if sample is not None:
        if isinstance(sample, int) or str(sample).isdigit():
            return cat["id"] if isinstance(sample, int) else str(cat["id"])
        return cat["name"]
    return {
        "id": cat["id"], "name": cat["name"], "link": cat.get("link"),
        "price_stars": cat["price_stars"], "image_url": img,
    }


def inv_mutate(user_id: str, fn, retries: int = 5):
    """Читает инвентарь, применяет fn(items, mode) -> (new_items, result) | None, пишет с проверкой версии."""
    db = _db()
    for _ in range(retries):
        r = db.table("users").select("inventory").eq("id", str(user_id)).limit(1).execute()
        if not r.data:
            return None
        old = r.data[0].get("inventory")
        items, mode = inv_parse(old)
        out = fn(items, mode)
        if out is None:
            return None
        new_items, result = out
        q = db.table("users").update({"inventory": inv_dump(new_items, mode)}).eq("id", str(user_id))
        q = q.is_("inventory", "null") if old is None else q.eq("inventory", old)
        if q.execute().data:
            return (True, result)
    return None


def add_gift(user_id: str, cat: dict) -> bool:
    def fn(items, mode):
        return items + [new_item_like(cat, items, mode)], True
    return inv_mutate(user_id, fn) is not None


def give_back_item(user_id: str, item) -> bool:
    return inv_mutate(user_id, lambda items, mode: (items + [item], True)) is not None


def pick_prize(value: float, orig_name: Optional[str]):
    """Самый дорогой подарок, цена которого <= выигрыша. Остаток выдаём звёздами.
    Подарки Star не выдаём — выигрыш в этом случае придёт обычными звёздами."""
    cands = [c for c in get_catalog() if 0 < float(c["price_stars"]) <= value and not is_star(c["name"])]
    if not cands:
        return None
    return max(cands, key=lambda c: (float(c["price_stars"]), str(c["name"]) == str(orig_name)))


# ---------------- выплаты ----------------
def payout_bet(bet: dict, m: float):
    db = _db()
    m = math.floor(m * 100) / 100
    claim = db.table("crash_bets").update({"cashed_out": True, "multiplier": m}) \
        .eq("id", bet["id"]).eq("cashed_out", False).execute()
    if not claim.data:
        return None  # уже выплачено (двойной клик / авто-вывод)
    uid = str(bet["user_id"])
    amount = float(bet["amount"])
    value = r2(amount * m)
    cur = bet["currency"]
    res = {"multiplier": m, "value": value, "currency": cur, "gift": None, "stars": 0.0}
    upd = {"profit": value}
    if cur == "ton":
        adjust(uid, 0, value)
    elif cur == "stars":
        adjust(uid, value, 0)
    else:
        rest = value
        g = pick_prize(value, bet.get("gift_name"))
        if g and add_gift(uid, g):
            gimg = preview_url(g["id"])
            rest = r2(value - float(g["price_stars"]))
            res["gift"] = {"name": g["name"], "image_url": gimg, "price_stars": float(g["price_stars"])}
            upd["payout_gift_name"] = g["name"]
            upd["payout_gift_image"] = gimg
        if rest > 0:
            adjust(uid, rest, 0)
        res["stars"] = rest
        upd["payout_stars"] = rest
    db.table("crash_bets").update(upd).eq("id", bet["id"]).execute()
    _bets_cache.pop(int(bet["round_id"]), None)
    return res


def settle_round(row: dict):
    """Авто-вывод: платим тем, чей целевой множитель <= точки краха."""
    rid = int(row["round_id"])
    if rid in _settled:
        return
    cp = float(row["crash_point"])
    res = _db().table("crash_bets").select("*").eq("round_id", rid).eq("cashed_out", False) \
        .gt("auto_cashout", 1).lte("auto_cashout", cp).execute()
    for bet in res.data or []:
        try:
            payout_bet(bet, float(bet["auto_cashout"]))
        except Exception as e:
            print("auto payout error:", repr(e))
    _settled.add(rid)
    if len(_settled) > 200:
        _settled.clear()


# ---------------- ставки / история ----------------
_bets_cache = {}
_hist_cache = {}

BET_COLS = ("id,user_id,username,currency,amount,gift_name,gift_image,cashed_out,multiplier,profit,"
            "payout_gift_name,payout_gift_image,payout_stars,auto_cashout")


def _public_bet(b: dict, user_id: Optional[str], round_id: int) -> dict:
    return {
        "id": b["id"], "round_id": round_id,
        "username": b.get("username") or "Игрок",
        "currency": b["currency"], "amount": float(b["amount"]),
        "gift_name": b.get("gift_name"), "gift_image": b.get("gift_image"),
        "cashed_out": bool(b.get("cashed_out")),
        "multiplier": float(b["multiplier"]) if b.get("multiplier") is not None else None,
        "profit": float(b["profit"]) if b.get("profit") is not None else None,
        "payout_gift_name": b.get("payout_gift_name"), "payout_gift_image": b.get("payout_gift_image"),
        "payout_stars": float(b["payout_stars"]) if b.get("payout_stars") is not None else None,
        "mine": bool(user_id) and str(b["user_id"]) == str(user_id),
    }


def bets_of(round_id: int) -> list:
    c = _bets_cache.get(round_id)
    if c and time.time() - c[0] < 0.35:
        return c[1]
    res = _db().table("crash_bets").select(BET_COLS).eq("round_id", round_id) \
        .order("id", desc=True).limit(40).execute()
    rows = res.data or []
    _bets_cache.clear() if len(_bets_cache) > 50 else None
    _bets_cache[round_id] = (time.time(), rows)
    return rows


def history_of(cur_id: int, include_current: bool) -> list:
    key = (cur_id, include_current)
    if key in _hist_cache:
        return _hist_cache[key]
    q = _db().table("crash_rounds").select("crash_point,round_id")
    q = q.lte("round_id", cur_id) if include_current else q.lt("round_id", cur_id)
    res = q.order("round_id", desc=True).limit(HISTORY_LEN).execute()
    out = [float(r["crash_point"]) for r in (res.data or [])]
    if len(_hist_cache) > 20:
        _hist_cache.clear()
    _hist_cache[key] = out
    return out


# ---------------- API ----------------
@_core.get("/state")
def state(user_id: Optional[str] = None):
    row = current_round()
    n = now_ms()
    finished = n >= row["crash_at"]
    rid = int(row["round_id"])
    if finished:
        try:
            settle_round(row)
        except Exception as e:
            print("settle error:", repr(e))
    out = {
        "round_id": rid,
        "bet_start": row["bet_start"],
        "fly_start": row["fly_start"],
        "finished": finished,
        "bet_window_ms": BET_WINDOW_MS,
        "growth": GROWTH,
        "history": history_of(rid, finished),
    }
    if finished:  # точка краха раскрывается только после падения
        out["crash_point"] = float(row["crash_point"])
        out["crash_at"] = row["crash_at"]
        out["end_at"] = row["end_at"]
    rows = bets_of(rid)
    out["bets"] = [_public_bet(b, user_id, rid) for b in rows]
    my = next((b for b in out["bets"] if b["mine"]), None)
    if my is None and user_id and len(rows) >= 40:
        r = _db().table("crash_bets").select(BET_COLS).eq("round_id", rid).eq("user_id", str(user_id)).limit(1).execute()
        if r.data:
            my = _public_bet(r.data[0], user_id, rid)
    out["my_bet"] = my
    out["server_time"] = now_ms()
    return out


@_core.get("/me/{user_id}")
def me(user_id: str, x_init_data: Optional[str] = Header(None)):
    verify_user(user_id, x_init_data)
    ensure_user(user_id)
    res = _db().table("users").select("balance, ton_balance, inventory").eq("id", user_id).limit(1).execute()
    row = res.data[0] if res.data else {}
    items, _ = inv_parse(row.get("inventory"))
    gifts = []
    for it in items:
        g = resolve_item(it)
        if is_star(g["name"]):
            continue  # Star — не подарок для ставок
        gifts.append(g)
    return {
        "balance": float(row.get("balance") or 0),
        "ton_balance": float(row.get("ton_balance") or 0),
        "gifts": gifts,
    }


class BetReq(BaseModel):
    user_id: Union[str, int]
    username: Optional[str] = "Игрок"
    currency: str                       # stars | ton | gift
    amount: float = 0
    gift_key: Optional[str] = None
    auto_cashout: Optional[float] = None


@_core.post("/bet")
def place_bet(req: BetReq, x_init_data: Optional[str] = Header(None)):
    uid = str(req.user_id)
    verify_user(uid, x_init_data)
    db = _db()
    row = current_round()
    if now_ms() >= row["fly_start"] - BET_CUTOFF_MS:
        raise HTTPException(status_code=400, detail="Приём ставок закрыт, дождитесь следующего раунда")
    rid = int(row["round_id"])
    cur = req.currency
    if cur not in ("stars", "ton", "gift"):
        raise HTTPException(status_code=400, detail="Неверная валюта")
    dup = db.table("crash_bets").select("id").eq("round_id", rid).eq("user_id", uid).limit(1).execute()
    if dup.data:
        raise HTTPException(status_code=400, detail="Вы уже сделали ставку в этом раунде")
    auto = req.auto_cashout if (req.auto_cashout and req.auto_cashout >= 1.01) else None
    if auto:
        auto = min(float(auto), 1000.0)
    username = (req.username or "Игрок").strip()[:24] or "Игрок"

    gift_item = None
    gift_info = None
    if cur == "gift":
        if not req.gift_key:
            raise HTTPException(status_code=400, detail="Не выбран подарок")

        def take(items, mode):
            for i, it in enumerate(items):
                if item_key(it) == req.gift_key:
                    return items[:i] + items[i + 1:], it
            return None

        got = inv_mutate(uid, take)
        if not got:
            raise HTTPException(status_code=400, detail="Подарок не найден в инвентаре")
        gift_item = got[1]
        gift_info = resolve_item(gift_item)
        if is_star(gift_info["name"]):
            give_back_item(uid, gift_item)
            raise HTTPException(status_code=400, detail="Этот подарок нельзя поставить")
        if gift_info["price_stars"] <= 0:
            give_back_item(uid, gift_item)
            raise HTTPException(status_code=400, detail="У этого подарка нет цены в каталоге")
        amount = r2(gift_info["price_stars"])
    else:
        amount = r2(req.amount)
        if amount < MIN_BET[cur]:
            raise HTTPException(status_code=400, detail=f"Минимальная ставка — {MIN_BET[cur]}")
        bal = adjust(uid, -amount if cur == "stars" else 0, -amount if cur == "ton" else 0)
        if bal is None:
            raise HTTPException(status_code=400, detail="Недостаточно средств")

    record = {
        "round_id": rid, "user_id": uid, "username": username, "currency": cur, "amount": amount,
        "cashed_out": False, "auto_cashout": auto,
    }
    if gift_info:
        record["gift_name"] = gift_info["name"]
        record["gift_image"] = gift_info["image_url"]
        record["gift_price"] = amount
    try:
        db.table("crash_bets").insert(record).execute()
    except Exception as e:
        print("bet insert error:", repr(e))
        if cur == "gift":
            give_back_item(uid, gift_item)
        else:
            adjust(uid, amount if cur == "stars" else 0, amount if cur == "ton" else 0)
        raise HTTPException(status_code=400, detail="Не удалось принять ставку")
    _bets_cache.pop(rid, None)
    return {"ok": True, "round_id": rid, "amount": amount, "currency": cur, **get_balances(uid)}


class CashReq(BaseModel):
    user_id: Union[str, int]


@_core.post("/cashout")
def cashout(req: CashReq, x_init_data: Optional[str] = Header(None)):
    uid = str(req.user_id)
    verify_user(uid, x_init_data)
    row = current_round()
    n = now_ms()
    if n < row["fly_start"]:
        raise HTTPException(status_code=400, detail="Раунд ещё не стартовал")
    if n >= row["crash_at"]:
        raise HTTPException(status_code=400, detail="Поздно — ракета уже упала")
    res = _db().table("crash_bets").select("*").eq("round_id", int(row["round_id"])) \
        .eq("user_id", uid).eq("cashed_out", False).limit(1).execute()
    if not res.data:
        raise HTTPException(status_code=400, detail="Нет активной ставки")
    m = min(mult_at(n - row["fly_start"]), float(row["crash_point"]))
    out = payout_bet(res.data[0], m)
    if out is None:
        raise HTTPException(status_code=400, detail="Ставка уже выплачена")
    return {"ok": True, **out, **get_balances(uid)}


# Роутер для api/index.py. Доступен и по /crash/..., и по /api/crash/...
# (на случай, если vercel.json пропускает к FastAPI только часть путей).
router = APIRouter(tags=["crash"])
router.include_router(_core, prefix="/crash")
router.include_router(_core, prefix="/api/crash")
