import os
import sys
import importlib
import traceback

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client

# Папка api/ и корень проекта должны быть в путях поиска модулей,
# иначе `from ref import ...` на Vercel не находит соседние файлы.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (BASE_DIR, os.path.dirname(BASE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def load_router(name: str):
    """Импортирует router из api/<name>.py (пробует `name`, затем `api.name`).
    Если не получилось, пишет причину в логи Vercel и возвращает None,
    чтобы остальные роуты продолжали работать."""
    errors = []
    for full in (name, f"api.{name}"):
        try:
            return importlib.import_module(full).router
        except Exception:
            errors.append((full, traceback.format_exc()))
    print(f"[index] НЕ УДАЛОСЬ подключить модуль '{name}':")
    for full, tb in errors:
        print(f"--- {full} ---\n{tb}")
    return None


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

for _name in ("ref", "tasks", "bot", "event_api", "topup", "inventory"):
    _router = load_router(_name)
    if _router is not None:
        app.include_router(_router)

_url = os.environ.get("SUPABASE_URL")
_key = os.environ.get("SUPABASE_SERVICE_KEY")
supabase: Client = create_client(_url, _key) if _url and _key else None


@app.get("/")
def read_root():
    return {"status": "ok", "message": "FixStars API is Running"}


@app.get("/tonconnect-manifest.json")
def tonconnect_manifest():
    # Манифест для TonConnect. url и иконка указывают на мини-апку (WEBAPP_URL).
    webapp = os.environ.get("WEBAPP_URL", "https://sdjsdjsd.vercel.app").rstrip("/")
    # TON_APP_URL — адрес, который кошелёк покажет пользователю (например свой домен).
    # Если не задан, показывается адрес мини-апки.
    shown = os.environ.get("TON_APP_URL", webapp).rstrip("/")
    return {
        "url": shown,
        "name": os.environ.get("TON_APP_NAME", "GiftPanda"),
        "iconUrl": f"{webapp}/img/star.png",   # иконка остаётся с фронта, где она реально лежит
    }


@app.get("/get_balance/{user_id}")
async def get_balance(user_id: str, username: str = "Unknown"):
    if supabase is None:
        return {"error": "supabase env missing", "balance": 0}
    try:
        try:
            result = supabase.table("users").select("balance, ton_balance").eq("id", user_id).execute()
        except Exception:
            # колонки ton_balance ещё нет (не выполнен topup_supabase.sql)
            result = supabase.table("users").select("balance").eq("id", user_id).execute()
        if result.data:
            row = result.data[0]
            return {"balance": row.get("balance") or 0, "ton_balance": row.get("ton_balance") or 0}

        supabase.table("users").upsert(
            {"id": user_id, "balance": 0, "username": username},
            on_conflict="id",
            ignore_duplicates=True,
        ).execute()
        return {"balance": 0, "ton_balance": 0}
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
