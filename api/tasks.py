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

SUBGRAM_TOKEN = os.environ.get("SUBGRAM_TOKEN", "")
SUBGRAM_URL = os.environ.get("SUBGRAM_URL", "https://api.subgram.org").rstrip("/")
SG_REWARD = float(os.environ.get("SG_REWARD", "0.1"))


class InitReq(BaseModel):
    init_data: str


class CheckReq(BaseModel):
    init_data: str
    task_id: int


class BhReq(BaseModel):
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
        "SUBGRAM_TOKEN": bool(SUBGRAM_TOKEN),
        "BH_REWARD": BH_REWARD,
        "SG_REWARD": SG_REWARD,
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
            "bh_reward": BH_REWARD, "sg_reward": SG_REWARD}


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


# ===================== BotoHub =====================
async def bh_request(chat_id: int, skip: bool):
    if not BOTOHUB_TOKEN:
        print("BOTOHUB_TOKEN missing")
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                "https://botohub.me/get-tasks",
                headers={"Auth": BOTOHUB_TOKEN, "Content-Type": "application/json"},
                json={"chat_id": chat_id, "is_task": True, "skip": skip},
            )
        if r.status_code != 200:
            print("botohub status:", r.status_code, r.text[:200])
            return None
        return r.json()
    except Exception as e:
        print("botohub exception:", repr(e))
        return None


@router.post("/tasks/bh")
async def bh_task(req: BhReq):
    user = validate_init_data(req.init_data)
    uid = str(user["id"])
    ensure_user(user)

    skip = req.action == "skip"
    data = await bh_request(int(user["id"]), skip)
    if data is None:
        return {"ok": False, "reason": "bh_unavailable"}

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


# ===================== Subgram =====================
async def sg_request(path: str, payload: dict):
    if not SUBGRAM_TOKEN:
        print("SUBGRAM_TOKEN missing")
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                SUBGRAM_URL + path,
                headers={"Auth": SUBGRAM_TOKEN, "Content-Type": "application/json"},
                json=payload,
            )
        try:
            data = r.json()
        except Exception:
            print("subgram bad json:", r.status_code, r.text[:200])
            return None
        if r.status_code >= 500 or data.get("status") == "error":
            print("subgram error:", r.status_code, str(data)[:300])
            return None
        return data
    except Exception as e:
        print("subgram exception:", repr(e))
        return None


def sg_done_resources(uid: str):
    rows = (db().table("completed_tasks").select("task_id")
            .eq("user_id", uid).like("task_id", "sg:%").execute().data or [])
    return [r["task_id"][3:] for r in rows]


async def sg_fetch(user: dict, uid: str, extra_exclude=None):
    """Возвращает dict спонсора, {} если спонсоров нет, None при ошибке."""
    exclude = sg_done_resources(uid) + list(extra_exclude or [])
    payload = {
        "user_id": int(user["id"]),
        "chat_id": int(user["id"]),
        "action": "newtask",
        "max_sponsors": 1,
        "get_links": 1,
        "is_premium": 1 if user.get("is_premium") else 0,
        "first_name": user.get("first_name") or "",
        "username": user.get("username") or "",
        "language_code": user.get("language_code") or "ru",
        "exclude_resource_ids": [str(x) for x in exclude[:500]],
    }
    data = await sg_request("/get-sponsors", payload)
    if data is None:
        return None
    if data.get("status") != "warning":
        return {}
    for s in (data.get("additional") or {}).get("sponsors") or []:
        if s.get("status") == "unsubscribed" and s.get("available_now") and s.get("link"):
            return s
    return {}


def sg_save(uid: str, sponsor: dict):
    db().table("sg_state").upsert(
        {"user_id": uid,
         "link": sponsor.get("link") if sponsor else None,
         "resource_id": str(sponsor.get("resource_id")) if sponsor else None},
        on_conflict="user_id",
    ).execute()


@router.post("/tasks/sg")
async def sg_task(req: BhReq):
    user = validate_init_data(req.init_data)
    uid = str(user["id"])
    ensure_user(user)

    st = db().table("sg_state").select("link,resource_id").eq("user_id", uid).execute().data
    cur = st[0] if st else {}
    cur_link, cur_rid = cur.get("link"), cur.get("resource_id")

    rewarded = 0.0
    new_balance = None

    if req.action == "get" and cur_link:
        return {"ok": True, "link": cur_link, "rewarded": 0, "new_balance": None}

    if req.action == "check":
        if not cur_link:
            sp = await sg_fetch(user, uid)
            if sp is None:
                return {"ok": False, "reason": "sg_unavailable"}
            sg_save(uid, sp)
            return {"ok": True, "link": sp.get("link"), "rewarded": 0, "new_balance": None}

        data = await sg_request("/get-user-subscriptions",
                                {"user_id": int(user["id"]), "links": [cur_link]})
        if data is None:
            return {"ok": False, "reason": "sg_unavailable"}

        sponsors = (data.get("additional") or {}).get("sponsors") or []
        subscribed = any(s.get("status") == "subscribed" for s in sponsors)
        if not subscribed:
            return {"ok": True, "link": cur_link, "rewarded": 0, "new_balance": None}

        res = rpc("complete_task", {
            "p_user_id": uid,
            "p_task_id": "sg:" + (cur_rid or uuid.uuid4().hex),
            "p_reward": SG_REWARD,
        })
        if res.get("success"):
            rewarded = SG_REWARD
            new_balance = res.get("new_balance")

        sp = await sg_fetch(user, uid)
        sg_save(uid, sp or None)
        return {
            "ok": True,
            "link": (sp or {}).get("link"),
            "rewarded": rewarded,
            "new_balance": float(new_balance) if new_balance is not None else None,
        }

    # get без сохранённой ссылки, либо skip
    extra = [cur_rid] if (req.action == "skip" and cur_rid) else []
    sp = await sg_fetch(user, uid, extra)
    if sp is None:
        return {"ok": False, "reason": "sg_unavailable"}
    sg_save(uid, sp or None)
    return {"ok": True, "link": (sp or {}).get("link"), "rewarded": 0, "new_balance": None}
