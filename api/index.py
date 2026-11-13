import os
import sys
import importlib
import traceback
from typing import Union

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
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

for _name in ("ref", "tasks", "bot", "event_api", "topup", "inventory", "withdraw", "giveaways", "roulette", "upgrade", "cases", "crash", "rolls"):
    _router = load_router(_name)
    if _router is not None:
        app.include_router(_router)

_url = os.environ.get("SUPABASE_URL")
_key = os.environ.get("SUPABASE_SERVICE_KEY")
supabase: Client = create_client(_url, _key) if _url and _key else None


class BalanceUpdateRequest(BaseModel):
    # Telegram ID приходит с фронта числом (user.id), поэтому принимаем и int, и str
    user_id: Union[str, int]
    stars_change: float = 0
    ton_change: float = 0


@app.post("/update_balance")
async def update_balance(req: BalanceUpdateRequest):
    if supabase is None:
        raise HTTPException(status_code=400, detail="supabase env missing")
    try:
        # Получаем текущие балансы пользователя
        res = supabase.table("users").select("balance, ton_balance").eq("id", str(req.user_id)).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="User not found")

        row = res.data[0]
        current_stars = float(row.get("balance") or 0)
        current_ton = float(row.get("ton_balance") or 0)

        new_stars = current_stars + req.stars_change
        new_ton = current_ton + req.ton_change

        if new_stars < 0 or new_ton < 0:
            raise HTTPException(status_code=400, detail="Insufficient funds")

        # Обновляем в таблице users
        supabase.table("users").update({
            "balance": new_stars,
            "ton_balance": new_ton
        }).eq("id", str(req.user_id)).execute()

        return {
            "success": True,
            "balance": new_stars,
            "ton_balance": new_ton
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        print("update_balance error:", repr(e))
        raise HTTPException(status_code=500, detail=str(e))


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
            result = supabase.table("users").select("balance, ton_balance, bamboo_balance").eq("id", user_id).execute()
        except Exception:
            # колонок ещё нет (не выполнен SQL)
            result = supabase.table("users").select("balance").eq("id", user_id).execute()
        if result.data:
            row = result.data[0]
            return {
                "balance": row.get("balance") or 0,
                "ton_balance": row.get("ton_balance") or 0,
                "bamboo_balance": row.get("bamboo_balance") or 0,
            }

        supabase.table("users").upsert(
            {"id": user_id, "balance": 0, "username": username},
            on_conflict="id",
            ignore_duplicates=True,
        ).execute()
        return {"balance": 0, "ton_balance": 0, "bamboo_balance": 0}
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
