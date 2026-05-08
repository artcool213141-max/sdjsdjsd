import os
import time
import logging
import telebot
from datetime import datetime, timedelta
from flask import Flask, request
from supabase import create_client
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    ReplyKeyboardMarkup, KeyboardButton
)

# ==========================================
# 1. КОНФИГУРАЦИЯ И ЛОГИРОВАНИЕ
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

bot = telebot.TeleBot(TOKEN, threaded=False)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
app = Flask(__name__)

ADMINS = [8431093842] 
SPONSORS = [
    {"name": "Тест 📢", "user": "@tesyruere"},
    {"name": "Buda News 🔥", "user": "@buda_news"} # Пример второго канала
]

REF_REWARD = 10.0
DAILY_BONUS = 5.0

# ==========================================
# 2. ЯДРО СИСТЕМЫ (БАЗА ДАННЫХ)
# ==========================================

def get_u(uid, uname="Unknown"):
    """Безопасное получение юзера с защитой от NoneType."""
    try:
        res = supabase.table("users").select("*").eq("id", uid).execute()
        if res.data and len(res.data) > 0:
            return res.data[0]
        
        # Регистрация нового юзера
        data = {
            "id": uid, 
            "username": uname if uname else "User", 
            "balance": 0.0,
            "tasks_done": 0, 
            "referred_by": None, 
            "is_banned": False,
            "last_bonus": (datetime.now() - timedelta(days=1)).isoformat()
        }
        res = supabase.table("users").insert(data).execute()
        return res.data[0]
    except Exception as e:
        logger.error(f"DB Error (get_u): {e}")
        return None

def update_u(uid, data: dict):
    """Обновление данных юзера."""
    try:
        supabase.table("users").update(data).eq("id", uid).execute()
        return True
    except Exception as e:
        logger.error(f"DB Update Error: {e}")
        return False

def check_subs(user_id):
    """Проверка подписки на обязательные каналы."""
    not_joined = []
    for chan in SPONSORS:
        try:
            status = bot.get_chat_member(chan['user'], user_id).status
            if status in ['left', 'kicked']:
                not_joined.append(chan)
        except Exception as e:
            logger.warning(f"Subs check failed for {chan['user']}: {e}")
            not_joined.append(chan)
    return not_joined

# ==========================================
# 3. ГЕНЕРАЦИЯ ИНТЕРФЕЙСА (UI)
# ==========================================

def main_menu_kb(uid):
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    # Используем Эмодзи для имитации цвета
    btn_tasks = KeyboardButton("🟡 ВЫПОЛНИТЬ ЗАДАНИЕ")
    btn_profile = KeyboardButton("👤 МОЙ ПРОФИЛЬ")
    btn_bonus = KeyboardButton("🎁 БОНУС")
    btn_top = KeyboardButton("🏆 ЛИДЕРЫ")
    btn_withdraw = KeyboardButton("💸 ВЫВОД")
    btn_promo = KeyboardButton("🎫 ПРОМОКОД")
    
    kb.add(btn_tasks, btn_profile)
    kb.add(btn_bonus, btn_top)
    kb.add(btn_withdraw, btn_promo)
    
    if uid in ADMINS:
        kb.add(KeyboardButton("👑 АДМИН-ПАНЕЛЬ"))
    return kb

def sub_kb(nj_list):
    kb = InlineKeyboardMarkup(row_width=1)
    for c in nj_list:
        url = f"https://t.me/{c['user'].replace('@', '')}"
        kb.add(InlineKeyboardButton(text=f"🔵 ПОДПИСАТЬСЯ: {c['name']}", url=url))
    kb.add(InlineKeyboardButton(text="✅ ПРОВЕРИТЬ ПОДПИСКУ", callback_data="check_start"))
    return kb

# ==========================================
# 4. ЛОГИКА ОБРАБОТКИ СООБЩЕНИЙ
# ==========================================

@bot.message_handler(commands=['start'])
def start_cmd(m):
    uid, uname = m.from_user.id, m.from_user.username
    user = get_u(uid, uname)
    
    if not user:
        bot.send_message(uid, "❌ Ошибка инициализации. Попробуйте позже.")
        return

    if user.get('is_banned'):
        bot.send_message(uid, "🚫 Ваш аккаунт заблокирован.")
        return

    # Рефералка
    args = m.text.split()
    if len(args) > 1 and args[1].startswith("ref_") and not user.get('referred_by'):
        ref_id = args[1].replace("ref_", "")
        if ref_id.isdigit() and int(ref_id) != uid:
            update_u(uid, {"referred_by": int(ref_id)})
            try: bot.send_message(int(ref_id), f"🔔 *У вас новый реферал!* @{uname}")
            except: pass

    # Проверка подписки
    nj = check_subs(uid)
    if nj:
        msg = "🛑 *ДОСТУП ЗАКРЫТ!*\n\nЧтобы пользоваться ботом и зарабатывать ⭐ Stars, подпишитесь на наших спонсоров:"
        bot.send_message(uid, msg, parse_mode="Markdown", reply_markup=sub_kb(nj))
    else:
        bot.send_message(uid, f"🚀 *Добро пожаловать, {uname}!*", reply_markup=main_menu_kb(uid), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "🎁 БОНУС")
def daily_bonus_handler(m):
    uid = m.from_user.id
    user = get_u(uid)
    
    last_bonus_str = user.get('last_bonus')
    last_bonus = datetime.fromisoformat(last_bonus_str)
    
    if datetime.now() - last_bonus > timedelta(days=1):
        new_bal = float(user['balance']) + DAILY_BONUS
        update_u(uid, {
            "balance": new_bal,
            "last_bonus": datetime.now().isoformat()
        })
        bot.send_message(uid, f"✅ Вы получили ежедневный бонус: *{DAILY_BONUS}* ⭐!", parse_mode="Markdown")
    else:
        next_bonus = last_bonus + timedelta(days=1)
        wait_time = next_bonus - datetime.now()
        hours = wait_time.seconds // 3600
        bot.send_message(uid, f"⏳ Бонус уже получен! Приходите через *{hours} ч.*", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "👤 МОЙ ПРОФИЛЬ")
def profile_handler(m):
    uid = m.from_user.id
    user = get_u(uid, m.from_user.username)
    
    # Считаем рефералов
    refs_count = supabase.table("users").select("id", count="exact").eq("referred_by", uid).execute().count
    
    ref_link = f"https://t.me/{bot.get_me().username}?start=ref_{uid}"
    
    text = (
        f"👤 *ВАШ КАБИНЕТ*\n"
        f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        f"🆔 Ваш ID: `{uid}`\n"
        f"💰 Баланс: *{user['balance']}* ⭐\n"
        f"✅ Заданий: *{user['tasks_done']}*\n"
        f"👥 Рефералов: *{refs_count}*\n"
        f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        f"🔗 *Реферальная ссылка:* \n{ref_link}"
    )
    bot.send_message(uid, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "🎫 ПРОМОКОД")
def promo_handler(m):
    msg = bot.send_message(m.chat.id, "⌨️ Введите промокод:")
    bot.register_next_step_handler(msg, process_promo)

def process_promo(m):
    code = m.text.strip()
    uid = m.from_user.id
    
    # В реальности тут должен быть запрос к таблице promos в Supabase
    if code == "BUDA2026":
        user = get_u(uid)
        update_u(uid, {"balance": user['balance'] + 50})
        bot.send_message(uid, "🎉 Код активирован! +50 ⭐")
    else:
        bot.send_message(uid, "❌ Неверный или истекший промокод.")

# --- АДМИНКА (РАСШИРЕННАЯ) ---

@bot.message_handler(func=lambda m: m.text == "👑 АДМИН-ПАНЕЛЬ")
def admin_menu(m):
    if m.from_user.id not in ADMINS: return
    
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📊 Статистика", callback_data="adm_stats"))
    kb.add(InlineKeyboardButton("📢 Рассылка", callback_data="adm_broadcast"))
    kb.add(InlineKeyboardButton("💳 Выдать баланс", callback_data="adm_give"))
    
    bot.send_message(m.chat.id, "⚙️ *Управление ботом:*", parse_mode="Markdown", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "adm_stats")
def adm_stats_callback(c):
    if c.from_user.id not in ADMINS: return
    
    total = supabase.table("users").select("id", count="exact").execute().count
    bot.answer_callback_query(c.id)
    bot.send_message(c.message.chat.id, f"📈 *Всего пользователей:* {total}")

# ==========================================
# 5. СЛУЖЕБНЫЕ ЧАСТИ (WEBHOOK / FLASK)
# ==========================================

@app.route("/", methods=["POST"])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return 'OK', 200
    return "Error", 403

@app.route("/", methods=["GET"])
def index():
    return "<h1>Bot is online</h1>", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
