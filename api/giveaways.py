from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

try:
    from ref import validate_init_data, rpc
except ImportError:
    from api.ref import validate_init_data, rpc

router = APIRouter()

ERRORS = {
    "closed": "Розыгрыш уже завершён или не найден",
    "insufficient": "Недостаточно бамбука",
    "user_not_found": "Пользователь не найден",
    "bad_amount": "Некорректное количество билетов",
    "limit": "Превышен лимит билетов на одного участника",
}


class ListReq(BaseModel):
    init_data: str
    status: Optional[str] = "active"


class JoinReq(BaseModel):
    init_data: str
    giveaway_id: int
    tickets: int


@router.post("/giveaways/list")
async def giveaways_list(req: ListReq):
    user = validate_init_data(req.init_data)
    # итоги подводим «лениво»: при каждом открытии страницы (cron не нужен)
    try:
        rpc("finish_due_giveaways", {})
    except HTTPException as e:
        print("finish_due_giveaways failed:", e.detail)

    status = "finished" if req.status == "finished" else "active"
    items = rpc("get_giveaways", {"p_user_id": str(user["id"]), "p_status": status}) or []
    return {"items": items}


@router.post("/giveaways/join")
async def giveaways_join(req: JoinReq):
    user = validate_init_data(req.init_data)
    if req.tickets < 1 or req.tickets > 1000000:
        raise HTTPException(400, ERRORS["bad_amount"])

    res = rpc("join_giveaway", {
        "p_user_id": str(user["id"]),
        "p_giveaway_id": req.giveaway_id,
        "p_tickets": req.tickets,
    })
    if isinstance(res, list):
        res = res[0] if res else {}
    res = res or {}

    if not res.get("success"):
        raise HTTPException(400, ERRORS.get(res.get("error"), "Не удалось участвовать"))
    return res
