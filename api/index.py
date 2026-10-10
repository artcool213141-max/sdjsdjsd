from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
from pydantic import BaseModel

# Импортируем роутер рефералов из файла ref.py
from ref import router as ref_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Подключаем реферальные роуты (теперь /ref_ping, /register_referral и др. будут доступны)
app.include_router(ref_router)

SUPABASE_URL = "https://xbklrdpguqsuvqndcujb.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inhia2xyZHBndXFzdXZxbmRjdWpiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTE2MzQ0NzUsImV4cCI6MjEwNzIxMDQ3NX0.IeK3NGOTu_J1jDOd_XjHQUOySnkbFh4uThGCPeu-Wu4"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

@app.get("/")
def read_root():
    return {"status": "ok", "message": "FixStars API is Running"}

class UpdateBalanceRequest(BaseModel):
    user_id: str
    amount: int

class PromoRequest(BaseModel):
    user_id: str
    code: str

@app.get("/get_balance/{user_id}")
async def get_balance(user_id: str, username: str = "Unknown", start_param: str = None):
    try:
        result = supabase.table("users").select("balance").eq("id", user_id).execute()
        if result.data and len(result.data) > 0:
            return {"balance": result.data[0]["balance"]}
        
        new_user = {"id": user_id, "balance": 0, "username": username, "ref_count": 0, "ref_earned": 0}
        supabase.table("users").insert(new_user).execute()
        return {"balance": 0}
    except Exception as e:
        return {"error": str(e), "balance": 0}

@app.post("/complete_task")
async def complete_task(req: dict):
    return {"success": True}
