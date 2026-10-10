import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
from ref import router as ref_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ref_router)

supabase: Client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


@app.get("/")
def read_root():
    return {"status": "ok", "message": "FixStars API is Running"}


@app.get("/get_balance/{user_id}")
async def get_balance(user_id: str, username: str = "Unknown"):
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
    try:
        res = supabase.table("completed_tasks").select("task_id").eq("user_id", user_id).execute()
        return {"completed": [r["task_id"] for r in (res.data or [])]}
    except Exception as e:
        print("get_completed_tasks error:", repr(e))
        return {"completed": []}


@app.post("/complete_task")
async def complete_task(req: dict):
    # ВРЕМЕННО: пока не вижу tasks.html. Просто записываем факт выполнения.
    user_id = str(req.get("user_id", ""))
    task_id = str(req.get("task_id", ""))
    if user_id and task_id:
        try:
            supabase.table("completed_tasks").upsert(
                {"user_id": user_id, "task_id": task_id},
                on_conflict="user_id,task_id",
            ).execute()
        except Exception as e:
            print("complete_task error:", repr(e))
    return {"success": True}
