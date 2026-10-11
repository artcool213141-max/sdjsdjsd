import os, uuid
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

try:
    from ref import validate_init_data, rpc, supabase, BOT_TOKEN
except ImportError:
    from api.ref import validate_init_data, rpc, supabase, BOT_TOKEN

router = APIRouter()

BOTOHUB_TOKEN = os.environ.get("BOTOHUB_TOKEN", "")
BH_REWARD = float(os.environ.get("BH_REWARD", "0.3"))

FLOWLY_API_KEY = os.environ.get("FLOWLY_API_KEY", "")
FLOWLY_URL = os.environ.get("FLOWLY_URL", "https://flowly.guru/api/v1").rstrip("/")
FL_REWARD = float(os.environ.get("FL_REWARD", "0.3"))
FL_TYPES = [t.strip() for t in os.environ.get("FL_TYPES", "channel").split(",") if t.strip()]


class InitReq(BaseModel):
    init_data: str


class CheckReq(BaseModel):
    init_data: str
    task_id: int


class SrcReq(BaseModel):
    init_data: str
    action: Optional[str] = "get"   # get | check | skip


def db():
    if supabase is None:
        raise HTTPException(500, "supabase env missing")
    return supabase


def ensure_user(user: dict):
    db().table("users").upsert(
        {"id": str(user["id"]),
         "username": user.get("username") or user.get("first_name"),
         "balance": 0},
        on_conflict="id", ignore_duplicates=True,
    ).execute()


async def is_member(channel: str, user_id: int):
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember",
                params={"chat_id": channel, "user_id": user_id},
            )
        data = r.json()
        if not data.get("ok"):
            print("getChatMember error:", channel, data.get("description"))
            return None
        res = data["result"]
        st = res.get("status")
        if st in ("member", "administrator", "creator"):
            return True
        if st == "restricted" and res.get("is_member"):
            return True
        return False
    except Exception as e:
        print("is_member exception:", repr(e))
        return None


@router.get("/tasks/env_check")
async def tasks_env_check():
    return {
        "BOTOHUB_TOKEN": bool(BOTOHUB_TOKEN),
        "FLOWLY_API_KEY": bool(FLOWLY_API_KEY),
        "BH_REWARD": BH_REWARD,
        "FL_REWARD": FL_REWARD,
        "FL_TYPES": FL_TYPES,
    }


@router.post("/tasks/list")
async def tasks_list(req: InitReq):
    user = validate_init_data(req.init_data)
    uid = str(user["id"])
    ensure_user(user)

    done = db().table("completed_tasks").select("task_id").eq("user_id", uid).execute().data or []
    done_ids = {r["task_id"] for r in done}

    tasks = (db().table("tasks_list")
             .select("id,title,desc_text,reward,link")
             .eq("active", True).order("id").execute().data or [])
    own = [t for t in tasks if f"own:{t['id']}" not in done_ids]

    bal = db().table("users").select("balance").eq("id", uid).execute().data
    balance = float(bal[0]["balance"] or 0) if bal else 0.0

    return {"balance": balance, "own": own, "done": len(done_ids),
            "bh_reward": BH_REWARD, "fl_reward": FL_REWARD}


@router.post("/tasks/check_own")
async def check_own(req: CheckReq):
    user = validate_init_data(req.init_data)
    uid = str(user["id"])

    rows = db().table("tasks_list").select("*").eq("id", req.task_id).eq("active", True).execute().data
    if not rows:
        return {"success": False, "reason": "not_found"}
    task = rows[0]

    member = await is_member(task["channel"], int(user["id"]))
    if member is None:
        return {"success": False, "reason": "bot_error"}
    if not member:
        return {"success": False, "reason": "not_subscribed"}

    return rpc("complete_task", {
        "p_user_id": uid,
        "p_task_id": f"own:{task['id']}",
        "p_reward": float(task["reward"]),
    })


async def bh_request(chat_id: int, skip: bool):
    if not BOTOHUB_TOKEN:
        print("BOTOHUB_TOKEN missing")
        return {"ok": False, "reason": "token_missing"}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                "https://botohub.me/get-tasks",
                headers={"Auth": BOTOHUB_TOKEN, "Content-Type": "application/json"},
                json={"chat_id": chat_id, "is_task": True, "skip": skip},
            )
        
        # Пытаемся распарсить JSON в любом случае, чтобы увидеть причину от админки/апи
        try:
            data = r.json()
        except Exception:
            data = {"ok": False, "raw_text": r.text}

        if r.status_code != 200:
            print("botohub status:", r.status_code, data)
            # Возвращаем структуру с ошибкой, но сохраняем текст/причину ответа апи
            if isinstance(data, dict):
                data["ok"] = False
                return data
            return {"ok": False, "reason": f"http_{r.status_code}", "raw": r.text}

        return data
    except Exception as e:
        print("botohub exception:", repr(e))
        return {"ok": False, "reason": "exception", "details": repr(e)}


@router.post("/tasks/bh")
async def bh_task(req: SrcReq):
    user = validate_init_data(req.init_data)
    uid = str(user["id"])
    ensure_user(user)

    skip = req.action == "skip"
    data = await bh_request(int(user["id"]), skip)
    
    # Если пришел ответ с ошибкой или без списка задач — логгируем, чтобы видеть причину в консоли сервера
    if not data or data.get("ok") is False:
        print("BotoHub error/reason response:", data)
        return {
            "ok": False, 
            "reason": data.get("reason") or data.get("message") or "bh_unavailable",
            "details": data
        }

    state = db().table("bh_state").select("link").eq("user_id", uid).execute().data
    prev_link = state[0]["link"] if state else None

    rewarded = 0.0
    new_balance = None
    if data.get("prev_success") and not skip:
        res = rpc("complete_task", {
            "p_user_id": uid,
            "p_task_id": "bh:" + (prev_link or uuid.uuid4().hex),
            "p_reward": BH_REWARD,
        })
        if res.get("success"):
            rewarded = BH_REWARD
            new_balance = res.get("new_balance")

    tasks = data.get("tasks") or []
    link = tasks[0] if tasks else None

    db().table("bh_state").upsert(
        {"user_id": uid, "link": link}, on_conflict="user_id"
    ).execute()

    return {
        "ok": True,
        "link": link,
        "rewarded": rewarded,
        "new_balance": float(new_balance) if new_balance is not None else None,
        "completed": bool(data.get("completed")),
    }


# ===================== Flowly =====================
async def fl_request(path: str, payload: dict):
    """dict с ответом, либо None при любой ошибке (в т.ч. 503 — тогда ничего не засчитываем)."""
    if not FLOWLY_API_KEY:
        print("FLOWLY_API_KEY missing")
        return None
    try:
        async with httpx.AsyncClient(timeout=12) as c:
            r = await c.post(
                FLOWLY_URL + path,
                headers={"Auth": FLOWLY_API_KEY, "Content-Type": "application/json"},
                json=payload,
            )
        if r.status_code != 200:
            print("flowly status:", path, r.status_code, r.text[:200])
            return None
        return r.json()
    except Exception as e:
        print("flowly exception:", repr(e))
        return None


def fl_done_ids(uid: str):
    rows = (db().table("completed_tasks").select("task_id")
            .eq("user_id", uid).like("task_id", "fl:%").execute().data or [])
    return {r["task_id"][3:] for r in rows}


def fl_get_state(uid: str):
    st = db().table("fl_state").select("offer_id,link,skipped").eq("user_id", uid).execute().data
    return st[0] if st else {"offer_id": None, "link": None, "skipped": ""}


def fl_save(uid: str, offer, skipped: str):
    db().table("fl_state").upsert(
        {"user_id": uid,
         "offer_id": str(offer["offer_id"]) if offer else None,
         "link": offer.get("link") if offer else None,
         "skipped": skipped},
        on_conflict="user_id",
    ).execute()


async def fl_pick(user: dict, uid: str, skipped: str, prefer_id: Optional[str] = None):
    """Возвращает offer (dict), {} если заданий нет, None при ошибке."""
    data = await fl_request("/offers", {
        "tg_user_id": int(user["id"]),
        "lang": user.get("language_code") or "ru",
        "is_premium": bool(user.get("is_premium")),
        "offers_limit": 5,
    })
    if data is None:
        return None
    if data.get("status") != "not_ok":
        return {}

    done = fl_done_ids(uid)
    skip_set = {x for x in skipped.split(",") if x}
    good = []
    for o in data.get("offers") or []:
        oid = str(o.get("offer_id"))
        if o.get("subscribed") or oid in done or oid in skip_set:
            continue
        if o.get("type") not in FL_TYPES or not o.get("link"):
            continue
        good.append(o)

    if prefer_id:
        for o in good:
            if str(o["offer_id"]) == prefer_id:
                return o
    return good[0] if good else {}


@router.post("/tasks/fl")
async def fl_task(req: SrcReq):
    user = validate_init_data(req.init_data)
    uid = str(user["id"])
    ensure_user(user)

    st = fl_get_state(uid)
    cur_id, skipped = st.get("offer_id"), st.get("skipped") or ""

    if req.action == "skip":
        if cur_id:
            skipped = (skipped + "," + cur_id).strip(",")
        offer = await fl_pick(user, uid, skipped)
        if offer is None:
            return {"ok": False, "reason": "fl_unavailable"}
        fl_save(uid, offer or None, skipped)
        return {"ok": True, "link": (offer or {}).get("link"), "rewarded": 0, "new_balance": None}

    if req.action == "check":
        if not cur_id:
            offer = await fl_pick(user, uid, skipped)
            if offer is None:
                return {"ok": False, "reason": "fl_unavailable"}
            fl_save(uid, offer or None, skipped)
            return {"ok": True, "link": (offer or {}).get("link"), "rewarded": 0, "new_balance": None}

        chk = await fl_request("/check", {"tg_user_id": int(user["id"]), "offer_id": int(cur_id)})
        if chk is None:
            return {"ok": False, "reason": "fl_unavailable"}

        status = chk.get("status")
        if status == "not_subscribed":
            return {"ok": True, "link": st.get("link"), "rewarded": 0, "new_balance": None}

        rewarded, new_balance = 0.0, None
        if status == "subscribed":
            res = rpc("complete_task", {
                "p_user_id": uid,
                "p_task_id": f"fl:{cur_id}",
                "p_reward": FL_REWARD,
            })
            if res.get("success"):
                rewarded = FL_REWARD
                new_balance = res.get("new_balance")
        # subscribed или offer_unavailable: идём к следующему заданию
        offer = await fl_pick(user, uid, skipped)
        if offer is None:
            offer = {}
        fl_save(uid, offer or None, skipped)
        return {
            "ok": True,
            "link": (offer or {}).get("link"),
            "rewarded": rewarded,
            "new_balance": float(new_balance) if new_balance is not None else None,
        }

    # get
    offer = await fl_pick(user, uid, skipped, prefer_id=cur_id)
    if offer is None:
        return {"ok": False, "reason": "fl_unavailable"}
    fl_save(uid, offer or None, skipped)
    return {"ok": True, "link": (offer or {}).get("link"), "rewarded": 0, "new_balance": None}
