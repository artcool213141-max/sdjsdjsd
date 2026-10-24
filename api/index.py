import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client

try:
    from ref import router as ref_router
    from tasks import router as tasks_router
    from bot import router as bot_router
    from event_api import router as event_router
except ImportError:
    from api.ref import router as ref_router
    from api.tasks import router as tasks_router
    from api.bot import router as bot_router
    from api.event_api import router as event_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ref_router)
app.include_router(tasks_router)
app.include_router(bot_router)
app.include_router(event_router)

_url = os.environ.get("SUPABASE_URL")
_key = os.environ.get("SUPABASE_SERVICE_KEY")
supabase: Client = create_client(_url, _key) if _url and _key else None


@app.get("/")
def read_root():
    return {"status": "ok", "message": "FixStars API is Running"}


@app.get("/get_balance/{user_id}")
async def get_balance(user_id: str, username: str = "Unknown"):
    if supabase is None:
        return {"error": "supabase env missing", "balance": 0}
    try:
        result = supabase.table("users").select("balance").eq("id", user_id).execute()
        if result.data:
            return {"balance": result.data[0]["balance"] or 0}

        supabase.table("users").upsert(
            {"id": user_id, "balance": 0, "username": username},
            on_conflict="id",
            ignore_duplicates=True,
        ).execute()
        return {"balance": 0}
    except Exception as e:
        print("get_balance error:", repr(e))
        return {"error": str(e), "balance": 0}


@app.get("/get_completed_tasks/{user_id}")
async def get_completed_tasks(user_id: str):
    if supabase is None:
        return {"completed": []}
    try:
        res = supabase.table("completed_tasks").select("task_id").eq("user_id", user_id).execute()
        return {"completed": [r["task_id"] for r in (res.data or [])]}
    except Exception as e:
        print("get_completed_tasks error:", repr(e))
        return {"completed": []}


@app.post("/complete_task")
async def complete_task(req: dict):
    # ОТКЛЮЧЕНО: раньше писало в completed_tasks без всякой проверки, и любой мог
    # накрутить себе задания. Теперь задания засчитывает только api/tasks.py
    # (проверка подписки / BotoHub на сервере). Роут оставлен, чтобы старые
    # страницы не получали 404.
    return {"success": True}
