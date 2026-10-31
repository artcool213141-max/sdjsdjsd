import os
import re

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

try:
    from ref import validate_init_data, rpc, supabase, BOT_TOKEN, ADMIN_ID
except ImportError:
    from api.ref import validate_init_data, rpc, supabase, BOT_TOKEN, ADMIN_ID

router = APIRouter()

# Комиссия за вывод NFT в Stars (списывается с баланса). 0 = бесплатно.
WITHDRAW_FEE = float(os.environ.get("WITHDRAW_FEE", "30"))

ERRORS = {
    "not_found": "Предмет не найден или уже продан",
    "insufficient": "Недостаточно Stars для вывода",
    "user_not_found": "Пользователь не найден",
}


NFT_LINK_RE = re.compile(r"^https://t\.me/nft/[A-Za-z0-9_-]+$")
OG_IMAGE_RES = (
    re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.I),
)


def extract_og_image(html: str):
    for rx in OG_IMAGE_RES:
        m = rx.search(html)
        if m and m.group(1).startswith("https://"):
            return m.group(1).replace("&amp;", "&")
    return None


class InitReq(BaseModel):
    init_data: str


class ItemReq(BaseModel):
    init_data: str
    nft_id: int   # id NFT из таблицы nft_catalog


def grant_item(user_id, nft_id: int):
    """Выдать NFT пользователю. Вызывай из роута кейсов, когда предмет выпал:
        from inventory import grant_item
        grant_item(user["id"], 3)   # 3 = id строки в таблице nft_catalog
    """
    res = rpc("grant_item", {"p_user_id": str(user_id), "p_nft_id": int(nft_id)})
    if isinstance(res, list):
        res = res[0] if res else {}
    return res or {}


@router.get("/inventory/preview/{nft_id}")
async def inventory_preview(nft_id: int):
    """Картинка NFT: берёт ссылку t.me/nft/... из каталога, достаёт og:image
    со страницы и перенаправляет на неё. Результат кэшируется на сутки.
    Использование на странице: <img src="BACKEND_URL/inventory/preview/ID">"""
    if supabase is None:
        raise HTTPException(404, "no db")
    rows = supabase.table("nft_catalog").select("link").eq("id", nft_id).limit(1).execute().data
    link = rows[0]["link"] if rows else ""
    if not NFT_LINK_RE.match(link or ""):
        raise HTTPException(404, "not found")
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as c:
            r = await c.get(link, headers={"User-Agent": "Mozilla/5.0"})
        img = extract_og_image(r.text) if r.status_code == 200 else None
    except Exception as e:
        print("preview fetch error:", repr(e))
        img = None
    if not img:
        raise HTTPException(404, "no image")
    return RedirectResponse(img, status_code=302, headers={
        "Cache-Control": "public, max-age=3600, s-maxage=86400",
    })


@router.get("/inventory/catalog")
async def inventory_catalog():
    """Все активные NFT (id, name, link, price_stars, price_ton): для показа в кейсах.
    Картинка: BACKEND_URL + '/inventory/preview/' + id"""
    return {"items": rpc("get_catalog", {}) or []}


@router.post("/inventory/list")
async def inventory_list(req: InitReq):
    user = validate_init_data(req.init_data)
    items = rpc("get_inventory", {"p_user_id": str(user["id"])}) or []
    return {"items": items, "withdraw_fee": WITHDRAW_FEE}


@router.post("/inventory/sell")
async def inventory_sell(req: ItemReq):
    user = validate_init_data(req.init_data)
    res = rpc("sell_item", {"p_user_id": str(user["id"]), "p_nft_id": req.nft_id}) or {}
    if not res.get("success"):
        raise HTTPException(400, ERRORS.get(res.get("error"), "Не удалось продать предмет"))
    return res


@router.post("/inventory/withdraw")
async def inventory_withdraw(req: ItemReq):
    user = validate_init_data(req.init_data)
    res = rpc("withdraw_item", {
        "p_user_id": str(user["id"]),
        "p_nft_id": req.nft_id,
        "p_fee": WITHDRAW_FEE,
    }) or {}
    if not res.get("success"):
        raise HTTPException(400, ERRORS.get(res.get("error"), "Не удалось создать заявку"))

    text = (
        "🎁 НОВАЯ ЗАЯВКА НА ВЫВОД NFT\n\n"
        f"👤 Пользователь: {user.get('first_name', 'Без имени')}\n"
        f"🆔 Telegram ID: {user['id']}\n"
        f"🔗 Username: @{user.get('username') or 'отсутствует'}\n"
        f"🏷 Предмет: {res.get('name')}\n"
        f"🔢 NFT ID: {req.nft_id}\n"
        f"📋 Заявка №{res.get('request_id')}"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": ADMIN_ID, "text": text},
            )
    except Exception as e:
        print("notify admin error:", repr(e))

    return res
