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
        
        # Если юзер уже есть
        if result.data and len(result.data) > 0:
            return {"balance": result.data[0]["balance"]}
        
        # ЕСЛИ ЮЗЕР НОВЫЙ
        new_user = {"id": user_id, "balance": 0, "username": username, "ref_count": 0, "ref_earned": 0}
        supabase.table("users").insert(new_user).execute()
        
        # Реферальная логика
        if start_param and start_param != user_id:
            check_ref = supabase.table("referrals").select("invited_id").eq("invited_id", user_id).execute()
            
            if not check_ref.data: 
                try:
                    # Записываем факт приглашения
                    supabase.table("referrals").insert({"invited_id": user_id, "referrer_id": start_param}).execute()
                    
                    # Начисляем бонус пригласившему
                    referrer = supabase.table("users").select("balance", "ref_count", "ref_earned").eq("id", start_param).execute()
                    if referrer.data:
                        ref_user = referrer.data[0]
                        bonus = 3
                        supabase.table("users").update({
                            "balance": ref_user["balance"] + bonus,
                            "ref_count": ref_user.get("ref_count", 0) + 1,
                            "ref_earned": ref_user.get("ref_earned", 0) + bonus
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

# 5. ПОЛУЧЕНИЕ СПИСКА ВЫПОЛНЕННЫХ ЗАДАНИЙ
@app.get("/get_completed_tasks/{user_id}")
async def get_completed_tasks(user_id: str):
    try:
        result = supabase.table("tasks_completed") \
            .select("task_id") \
            .eq("user_id", user_id) \
            .execute()
        # Возвращаем массив ID выполненных заданий
        return {"completed": [row["task_id"] for row in result.data]}
    except Exception as e:
        return {"completed": []}


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

# Модель для задания
class TaskRequest(BaseModel):
    user_id: str
    task_id: str
    reward: int

@app.post("/complete_task")
async def complete_task(req: TaskRequest):
    try:
        # 1. Проверяем, не выполнял ли юзер это задание раньше
        check = supabase.table("tasks_completed")\
            .select("id")\
            .eq("user_id", req.user_id)\
            .eq("task_id", req.task_id)\
            .execute()
        
        if check.data:
            raise HTTPException(status_code=400, detail="Задание уже выполнено!")

        # 2. Если не выполнял — записываем в базу выполненных
        supabase.table("tasks_completed").insert({
            "user_id": req.user_id, 
            "task_id": req.task_id
        }).execute()

        # 3. Начисляем баллы
        user = supabase.table("users").select("balance").eq("id", req.user_id).execute()
        new_balance = user.data[0]["balance"] + req.reward
        
        supabase.table("users").update({"balance": new_balance}).eq("id", req.user_id).execute()

        return {"success": True, "new_balance": new_balance}

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
