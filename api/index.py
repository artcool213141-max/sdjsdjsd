from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
from pydantic import BaseModel

app = FastAPI()

# Разрешаем Mini App делать запросы к этому API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ТВОИ ДАННЫЕ
SUPABASE_URL = "https://bajufluzgcguxconhxuj.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJhanVmbHV6Z2NndXhjb25oeHVqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzgxODI4NTUsImV4cCI6MjA5Mzc1ODg1NX0.LtqDCLlMlaWluUdnx0uc3Wt-w8jimw9gyLYMwW9eAxs"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Модель для приема данных при покупке
class UpdateBalanceRequest(BaseModel):
    user_id: int
    amount: int

@app.get("/get_balance/{user_id}")
async def get_balance(user_id: int, username: str = "Unknown"):
    try:
        result = supabase.table("users").select("balance").eq("id", user_id).execute()
        
        if result.data and len(result.data) > 0:
            return {"balance": result.data[0]["balance"]}
        else:
            new_user = {
                "id": user_id, 
                "balance": 0, 
                "username": username 
            }
            supabase.table("users").insert(new_user).execute()
            return {"balance": 0}
            
    except Exception as e:
        return {"error": str(e), "balance": 0}

# НОВАЯ ФУНКЦИЯ ДЛЯ СПИСАНИЯ ПРИ ПОКУПКЕ ПОДАРКА
@app.post("/update_balance")
async def update_balance(request: UpdateBalanceRequest):
    try:
        # 1. Получаем текущий баланс
        result = supabase.table("users").select("balance").eq("id", request.user_id).execute()
        
        if not result.data:
            return {"error": "User not found"}

        current_balance = result.data[0]["balance"]
        
        # 2. Считаем новый баланс (amount будет отрицательным, например -15)
        new_balance = current_balance + request.amount

        if new_balance < 0:
            return {"error": "Insufficient funds"}

        # 3. Сохраняем в базу
        supabase.table("users").update({"balance": new_balance}).eq("id", request.user_id).execute()

        return {"status": "success", "new_balance": new_balance}

    except Exception as e:
        return {"error": str(e)}
