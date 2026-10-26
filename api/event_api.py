"""
Роутер ивента «Кровавая Жатва».

Подключение в основном приложении FastAPI:
    from event_api import router as event_router
    app.include_router(event_router)

Переменные окружения (Vercel → Settings → Environment Variables):
    BOT_TOKEN             токен бота (для проверки initData)
    SUPABASE_URL          https://xxxx.supabase.co
    SUPABASE_SERVICE_KEY  service_role ключ (ТОЛЬКО на бэкенде, на фронт не отдавать!)

requirements.txt:  supabase
"""
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from supabase import create_client

BOT_TOKEN = os.environ["BOT_TOKEN"]
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

router = APIRouter(prefix="/event", tags=["event"])

# Только для показа в интерфейсе. Реальные значения живут в SQL-функциях —
# если меняешь экономику, меняй и там, и тут.
CFG = {
    "harvest_cooldown": 3600,
    "harvest_min": 5,
    "harvest_max": 10,
    "shield_cost": 50,
    "shield_hours": 12,
    "shield_max_hours": 24,
    "shield_block": 50,
    "soul_per_star": 1000,
    "daily_star_cap": 3,
    "min_souls_battle": 10,
    "round_seconds": 12,
    "steal_percent": 15,
}


# ---------- авторизация через Telegram initData ----------
def auth(init_data: str):
    if not init_data:
        raise HTTPException(401, "no init_data")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", None)
    if not received:
        raise HTTPException(401, "no hash")

    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        raise HTTPException(401, "bad signature")
    if time.time() - int(pairs.get("auth_date", "0")) > 86400:
        raise HTTPException(401, "init_data expired")

    user = json.loads(pairs["user"])
    name = ("@" + user["username"]) if user.get("username") else user.get("first_name", "Жнец")
    return int(user["id"]), name


def rpc(name: str, params: dict):
    try:
        return sb.rpc(name, params).execute().data
    except Exception as e:  # noqa: BLE001
        print(f"[event] rpc {name} failed: {e}")
        raise HTTPException(500, "rpc_failed")


def secs_until(ts, plus: int = 0) -> int:
    if not ts:
        return 0
    t = datetime.fromisoformat(ts.replace("Z", "+00:00")) + timedelta(seconds=plus)
    return max(0, int((t - datetime.now(timezone.utc)).total_seconds()))


# ---------- модели запросов ----------
class Base(BaseModel):
    init_data: str


class BattleIn(Base):
    battle_id: str


class MoveIn(BattleIn):
    spell: str


class ConvertIn(Base):
    stars: int


class RatingIn(Base):
    kind: str = "stolen"


# ---------- эндпоинты ----------
@router.post("/profile")
def profile(body: Base):
    uid, name = auth(body.init_data)
    p = rpc("event_ensure_profile", {"p_user": uid, "p_username": name})

    stars_balance, converted_today = 0.0, 0.0

    # Баланс звёзд: в таблице users ключ — колонка id (как в /get_balance)
    try:
        row = sb.table("users").select("balance").eq("id", str(uid)).execute().data
        if row:
            stars_balance = float(row[0].get("balance") or 0)
    except Exception as e:  # noqa: BLE001
        print(f"[event] profile stars_balance failed: {e}")

    # Сколько звёзд уже сконвертировано за последние 24 часа
    try:
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        conv = (
            sb.table("event_conversions")
            .select("stars")
            .eq("user_id", uid)
            .gte("created_at", since)
            .execute()
            .data
        )
        converted_today = float(sum(float(c["stars"]) for c in conv))
    except Exception as e:  # noqa: BLE001
        print(f"[event] profile converted_today failed: {e}")

    return {
        "souls": p["souls_balance"],
        "collected": p["souls_collected_total"],
        "stolen": p["souls_stolen_total"],
        "lost": p["souls_lost_total"],
        "wins": p["wins"],
        "losses": p["losses"],
        "draws": p["draws"],
        "shield_left": secs_until(p.get("shield_until")),
        "harvest_wait": secs_until(p.get("last_harvest_at"), CFG["harvest_cooldown"]),
        "stars_balance": stars_balance,
        "converted_today": converted_today,
        "cfg": CFG,
    }


@router.post("/harvest")
def harvest(body: Base):
    uid, _ = auth(body.init_data)
    return rpc("event_harvest", {"p_user": uid})


@router.post("/shield")
def shield(body: Base):
    uid, _ = auth(body.init_data)
    return rpc("event_buy_shield", {"p_user": uid})


@router.post("/battle/search")
def battle_search(body: Base):
    uid, name = auth(body.init_data)
    return rpc("event_find_match", {"p_user": uid, "p_username": name})


@router.post("/battle/cancel")
def battle_cancel(body: Base):
    uid, _ = auth(body.init_data)
    rpc("event_cancel_search", {"p_user": uid})
    return {"ok": True}


@router.post("/battle/move")
def battle_move(body: MoveIn):
    uid, _ = auth(body.init_data)
    return rpc("event_submit_move", {"p_user": uid, "p_battle": body.battle_id, "p_spell": body.spell})


@router.post("/battle/state")
def battle_state(body: BattleIn):
    uid, _ = auth(body.init_data)
    return rpc("event_battle_state", {"p_user": uid, "p_battle": body.battle_id})


@router.post("/convert")
def convert(body: ConvertIn):
    uid, _ = auth(body.init_data)
    return rpc("event_convert", {"p_user": uid, "p_stars": body.stars})


@router.post("/rating")
def rating(body: RatingIn):
    uid, _ = auth(body.init_data)
    col = "souls_balance" if body.kind == "souls" else "souls_stolen_total"
    try:
        rows = (
            sb.table("event_profiles")
            .select("user_id,username,souls_balance,souls_stolen_total,wins")
            .order(col, desc=True)
            .limit(50)
            .execute()
            .data
        )
    except Exception as e:  # noqa: BLE001
        print(f"[event] rating failed: {e}")
        raise HTTPException(500, "rating_failed")

    return {
        "items": [
            {
                "rank": i + 1,
                "name": r.get("username") or "Жнец",
                "souls": r["souls_balance"],
                "stolen": r["souls_stolen_total"],
                "wins": r["wins"],
                "is_me": r["user_id"] == uid,
            }
            for i, r in enumerate(rows)
        ]
    }
