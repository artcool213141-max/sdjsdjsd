import os
from fastapi import APIRouter, HTTPException

try:
    from ref import supabase
except ImportError:
    from api.ref import supabase

# Оставляем пустой роутер, чтобы index.py не падал при загрузке
router = APIRouter()

async def create_withdraw_request(user: dict, res: dict, nft_id: int, fee: float):
    # База данных уже сама записала заявку в таблицу withdraw_requests.
    # Нам больше ничего делать не нужно, просто возвращаем успех.
    return {"success": True, "request_id": res.get("request_id")}

async def set_status(req_id: int, status: str, cq: dict):
    # Если в будущем захочешь менять статус, логика будет тут
    return {"ok": True}
