from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Убери /rest/v1/ в конце, если он есть!
SUPABASE_URL = "https://bajufluzgcguxconhxuj.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJhanVmbHV6Z2NndXhjb25oeHVqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzgxODI4NTUsImV4cCI6MjA5Mzc1ODg1NX0.LtqDCLlMlaWluUdnx0uc3Wt-w8jimw9gyLYMwW9eAxs" # Твой ключ

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

@app.get("/get_balance/{user_id}")
async def get_balance(user_id: int):
    try:
        # Тянем данные из твоей таблицы public.users
        result = supabase.table("users").select("balance").eq("id", user_id).execute()
        if result.data:
            return {"balance": result.data[0]["balance"]}
        return {"balance": 0}
    except Exception as e:
        return {"error": str(e)}
