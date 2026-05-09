from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client

app = FastAPI()

# Разрешаем Mini App делать запросы к этому API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ТВОИ ДАННЫЕ (проверь их еще раз в Settings -> API в Supabase)
SUPABASE_URL = "https://bajufluzgcguxconhxuj.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJhanVmbHV6Z2NndXhjb25oeHVqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzgxODI4NTUsImV4cCI6MjA5Mzc1ODg1NX0.LtqDCLlMlaWluUdnx0uc3Wt-w8jimw9gyLYMwW9eAxs" # Твой ANON ключ

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

@app.get("/get_balance/{user_id}")
async def get_balance(user_id: int):
    try:
        # 1. Пробуем найти юзера
        result = supabase.table("users").select("balance").eq("id", user_id).execute()
        
        if result.data and len(result.data) > 0:
            # Юзер есть, отдаем баланс
            return {"balance": result.data[0]["balance"]}
        else:
            # 2. Юзера НЕТ. Создаем новую строку!
            # Здесь мы добавляем ID и ставим начальный баланс 0
            new_user = {
                "id": user_id, 
                "balance": 0, 
                "username": "new_player" # можно будет потом передавать реальный ник
            }
            supabase.table("users").insert(new_user).execute()
            
            print(f"Создан новый пользователь: {user_id}")
            return {"balance": 0}
            
    except Exception as e:
        print(f"Ошибка: {e}")
        return {"error": str(e), "balance": 0}
