from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
from pydantic import BaseModel

app = FastAPI()

# Разрешаем Mini App делать запросы к этому API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ТВОИ ДАННЫЕ
SUPABASE_URL = "https://bajufluzgcguxconhxuj.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJhanVmbHV6Z2NndXhjb25oeHVqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzgxODI4NTUsImV4cCI6MjA5Mzc1ODg1NX0.LtqDCLlMlaWluUdnx0uc3Wt-w8jimw9gyLYMwW9eAxs"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Модели для валидации запросов
class UpdateBalanceRequest(BaseModel):
    user_id: str
    amount: int

class PromoRequest(BaseModel):
    user_id: str
    code: str


# 1. ПОЛУЧЕНИЕ БАЛАНСА + АВТО-РЕФЕРАЛКА С НАГРАДОЙ 3 ЗВЕЗДЫ
@app.get("/get_balance/{user_id}")
async def get_balance(user_id: str, username: str = "Unknown", start_param: str = None):
    try:
        # Проверяем, есть ли юзер в базе
        result = supabase.table("users").select("balance").eq("id", user_id).execute()
        
        # Если юзер УЖЕ СУЩЕСТВУЕТ, просто отдаем его баланс
        if result.data and len(result.data) > 0:
            return {"balance": result.data[0]["balance"]}
        
        # ЕСЛИ ЮЗЕР НОВЫЙ (Первый раз открыл Mini App)
        else:
            # Создаем нового пользователя с базовыми реферными счетчиками на 0
            new_user = {
                "id": user_id, 
                "balance": 0, 
                "username": username,
                "ref_count": 0,
                "ref_earned": 0
            }
            supabase.table("users").insert(new_user).execute()
            
            # Если он пришел по чьей-то реф-ссылке (start_param — это ID пригласившего)
            if start_param and start_param != user_id:
                try:
                    # Ищем того, кто пригласил
                    referrer = supabase.table("users").select("balance", "ref_count", "ref_earned").eq("id", start_param).execute()
                    
                    if referrer.data and len(referrer.data) > 0:
                        ref_user = referrer.data[0]
                        
                        # ТВОЯ НАСТРОЙКА: 3 звезды за одного реферала
                        bonus = 3  
                        
                        # Высчитываем новые значения для пригласившего
                        current_balance = ref_user.get("balance", 0)
                        current_ref_count = ref_user.get("ref_count", 0) if ref_user.get("ref_count") is not None else 0
                        current_ref_earned = ref_user.get("ref_earned", 0) if ref_user.get("ref_earned") is not None else 0
                        
                        # Начисляем 3 звезды и прибавляем +1 к счетчику рефералов в базе
                        supabase.table("users").update({
                            "balance": current_balance + bonus,
                            "ref_count": current_ref_count + 1,
                            "ref_earned": current_ref_earned + bonus
                        }).eq("id", start_param).execute()
                        
                except Exception as ref_err:
                    print(f"Ошибка начисления реф-бонуса: {ref_err}")
            
            return {"balance": 0}
            
    except Exception as e:
        return {"error": str(e), "balance": 0}


# 2. СПИСАНИЕ ПРИ ПОКУПКЕ ПОДАРКА
@app.post("/update_balance")
async def update_balance(request: UpdateBalanceRequest):
    try:
        result = supabase.table("users").select("balance").eq("id", request.user_id).execute()
        
        if not result.data:
            return {"error": "User not found"}

        current_balance = result.data[0]["balance"]
        new_balance = current_balance + request.amount

        if new_balance < 0:
            return {"error": "Insufficient funds"}

        supabase.table("users").update({"balance": new_balance}).eq("id", request.user_id).execute()
        return {"status": "success", "new_balance": new_balance}

    except Exception as e:
        return {"error": str(e)}


# 3. СТАТИСТИКА РЕФЕРАЛОВ ДЛЯ ПРОФИЛЯ
@app.get("/get_ref_stats/{user_id}")
async def get_ref_stats(user_id: str):
    try:
        result = supabase.table("users").select("ref_count", "ref_earned").eq("id", user_id).execute()
        if result.data and len(result.data) > 0:
            return {
                "ref_count": result.data[0].get("ref_count", 0),
                "ref_earned": result.data[0].get("ref_earned", 0)
            }
        return {"ref_count": 0, "ref_earned": 0}
    except Exception as e:
        return {"error": str(e), "ref_count": 0, "ref_earned": 0}


# 4. АКТИВАЦИЯ ПРОМОКОДОВ
@app.post("/activate_promo")
async def activate_promo(req: PromoRequest):
    try:
        user_id = req.user_id
        code = req.code.strip().upper()

        promo_res = supabase.table("promo_codes").select("*").eq("code", code).execute()
        if not promo_res.data:
            raise HTTPException(status_code=400, detail="Промокод не найден")
        
        promo = promo_res.data[0]
        
        if promo.get("current_uses", 0) >= promo.get("max_uses", 100):
            raise HTTPException(status_code=400, detail="Срок действия промокода истек")

        check_act = supabase.table("promo_activations").select("id").eq("user_id", user_id).eq("code", code).execute()
        if check_act.data:
            raise HTTPException(status_code=400, detail="Вы уже активировали этот промокод")

        supabase.table("promo_activations").insert({"user_id": user_id, "code": code}).execute()

        user_res = supabase.table("users").select("balance").eq("id", user_id).execute()
        current_balance = user_res.data[0]["balance"] if user_res.data else 0
        new_balance = current_balance + float(promo["reward"])
        
        supabase.table("users").update({"balance": new_balance}).eq("id", user_id).execute()
        supabase.table("promo_codes").update({"current_uses": promo.get("current_uses", 0) + 1}).eq("code", code).execute()

        return {"success": True, "reward": promo["reward"], "new_balance": new_balance}

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка сервера: {str(e)}")
