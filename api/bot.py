import os
import time
import telebot
import logging
from flask import Flask, request
from supabase import create_client
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    ReplyKeyboardMarkup, KeyboardButton
)

# ==========================================
# 1. КОНФИГУРАЦИЯ
# ==========================================
TOKEN = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

bot = telebot.TeleBot(TOKEN, threaded=False)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
app = Flask(__name__)

# Список админов (ID)
ADMINS = [8431093842] 

# Настройка каналов (До 10 штук)
SPONSORS = [
    {"name": "Buda News 📢", "user": "@channel1"},
    {"name": "Crypto Farm 💎", "user": "@channel2"},
    {"name": "Buda Community 👥", "user": "@channel3"},
    {"name": "Partner Channel ✨", "user": "@channel4"},
]

# Настройки наград
REF_REWARD = 10.0  # Звезд за друга
MIN_WITHDRAW = 50.0 # Мин. вывод

# ==========================================
# 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (HELPERS)
# ==========================================

def is_admin(user_id):
    return user_id in ADMINS

def check_subs(user_id):
    """Проверяет подписки и возвращает список недостающих."""
    not_joined = []
    for chan in SPONSORS:
        try:
            status = bot.get_chat_member(chan['user'], user_id).status
            if status in ['left', 'kicked']:
                not_joined.append(chan)
        except:
            not_joined.append(chan)
    return not_joined

# ==========================================
# 3. РАБОТА С БАЗОЙ ДАННЫХ (SUPABASE)
# ==========================================

def get_u(uid, uname="Unknown"):
    """Получить или создать юзера."""
    try:
        res = supabase.table("users").select("*").eq("id", uid).execute()
        if not res.data:
            data = {
                "id": uid, "username": uname, "balance": 0.0,
                "tasks_done": 0, "referred_by": None, "is_banned": False
            }
            res = supabase.table("users").insert(data).execute()
        return res.data[0]
    except Exception as e:
        logging.error(f"DB Error: {e}")
        return None

def add_balance(uid, amount):
    user = get_u(uid)
    if not user: return False
    new_bal = round(float(user['balance']) + float(amount), 2)
    supabase.table("users").update({"balance": new_bal}).eq("id", uid).execute()
    return True

# ==========================================
# 4. КЛАВИАТУРЫ (UI)
# ==========================================

def main_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(KeyboardButton("🟡 ВЫПОЛНИТЬ ЗАДАНИЕ"), KeyboardButton("👤 МОЙ ПРОФИЛЬ"))
    kb.add(KeyboardButton("🏆 ЛИДЕРЫ"), KeyboardButton("💸 ВЫВОД"))
    kb.add(KeyboardButton("❓ ПОМОЩЬ"))
    return kb

def sub_kb(not_joined):
    kb = InlineKeyboardMarkup()
    for c in not_joined:
        url = f"https://t.me/{c['user'].replace('@', '')}"
        kb.add(InlineKeyboardButton(text=f"🔗 ПОДПИСАТЬСЯ: {c['name']}", url=url))
    kb.add(InlineKeyboardButton(text="✅ Я ПОДПИСАЛСЯ (ПРОВЕРИТЬ)", callback_data="check_start"))
    return kb

def task_kb(tid, url):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🔗 ПЕРЕЙТИ К ЗАДАНИЮ", url=url))
    kb.add(InlineKeyboardButton("💎 ПРОВЕРИТЬ ВЫПОЛНЕНИЕ", callback_data=f"v_{tid}"))
    kb.add(InlineKeyboardButton("⏭ СЛЕДУЮЩЕЕ", callback_data="skip"))
    return kb

# ==========================================
# 5. ОБРАБОТЧИКИ (HANDLERS)
# ==========================================

@bot.message_handler(commands=['start'])
def start(m):
    uid, uname = m.from_user.id, m.from_user.username
    user = get_u(uid, uname)
    
    # Реферальная система
    args = m.text.split()
    if len(args) > 1 and args[1].startswith("ref_") and not user['referred_by']:
        ref_id = int(args[1].replace("ref_", ""))
        if ref_id != uid:
            supabase.table("users").update({"referred_by": ref_id}).eq("id", uid).execute()
            try: bot.send_message(ref_id, f"🔔 *У вас новый реферал!* @{uname}\nБонус будет начислен после проверки.")
            except: pass

    # Проверка подписки
    nj = check_subs(uid)
    if nj:
        bot.send_message(uid, "🛑 *ДОСТУП ОГРАНИЧЕН!*\n\nПодпишись на каналы ниже, чтобы разблокировать функции бота:", 
                         parse_mode="Markdown", reply_markup=sub_kb(nj))
    else:
        bot.send_message(uid, "🚀 *BudaTasks запущен!*\n\nНажимай на кнопки внизу, чтобы начать зарабатывать.", 
                         parse_mode="Markdown", reply_markup=main_kb())

@bot.callback_query_handler(func=lambda c: c.data == "check_start")
def check_start_btn(c):
    uid = c.from_user.id
    nj = check_subs(uid)
    if not nj:
        bot.delete_message(c.message.chat.id, c.message.message_id)
        bot.send_message(uid, "✅ *Проверка пройдена!* Добро пожаловать.", parse_mode="Markdown", reply_markup=main_kb())
    else:
        bot.answer_callback_query(c.id, f"❌ Вы подписались не на все каналы! ({len(nj)} осталось)", show_alert=True)

# --- ЛОГИКА ЗАДАНИЙ ---

@bot.message_handler(func=lambda m: m.text == "🟡 ВЫПОЛНИТЬ ЗАДАНИЕ")
def show_task(m):
    uid = m.from_user.id
    # Проверка подписки перед выдачей
    if check_subs(uid):
        bot.send_message(uid, "⚠️ Сначала подпишитесь на спонсоров в /start")
        return

    # Получаем список выполненных
    done = [x['task_id'] for x in supabase.table("star_tasks_done").select("task_id").eq("user_id", uid).execute().data]
    
    q = supabase.table("star_tasks").select("*").eq("active", True)
    if done: q = q.not_.in_("id", done)
    
    res = q.limit(1).execute().data
    if not res:
        bot.send_message(uid, "😴 *Задания закончились!*\nЗаходи позже, мы постоянно добавляем новые.", parse_mode="Markdown")
        return

    t = res[0]
    msg = (f"🎯 *НОВОЕ ЗАДАНИЕ*\n\n"
           f"📝 *Название:* {t['title']}\n"
           f"💰 *Награда:* {t['reward']} ⭐\n\n"
           f"ℹ️ {t['description']}")
    bot.send_message(uid, msg, parse_mode="Markdown", reply_markup=task_kb(t['id'], t['url']))

@bot.callback_query_handler(func=lambda c: c.data.startswith("v_"))
def verify_task(c):
    tid = int(c.data.split("_")[1])
    uid = c.from_user.id
    
    # 1. Проверка на повтор
    check = supabase.table("star_tasks_done").select("*").eq("user_id", uid).eq("task_id", tid).execute().data
    if check:
        bot.answer_callback_query(c.id, "🛑 Уже выполнено!", show_alert=True)
        return

    # 2. Начисление
    task = supabase.table("star_tasks").select("*").eq("id", tid).execute().data[0]
    if add_balance(uid, task['reward']):
        supabase.table("star_tasks_done").insert({"user_id": uid, "task_id": tid}).execute()
        
        # Обновляем счетчик заданий юзера
        u = get_u(uid)
        supabase.table("users").update({"tasks_done": (u['tasks_done'] or 0) + 1}).eq("id", uid).execute()
        
        bot.edit_message_text(f"✅ *Успешно!* +{task['reward']} ⭐ начислено.", 
                             c.message.chat.id, c.message.message_id, parse_mode="Markdown")
        time.sleep(1)
        show_task(c.message) # Даем следующее
    else:
        bot.answer_callback_query(c.id, "❌ Ошибка БД", show_alert=True)

# --- ПРОФИЛЬ И ТОП ---

@bot.message_handler(func=lambda m: m.text == "👤 МОЙ ПРОФИЛЬ")
def profile(m):
    u = get_u(m.from_user.id, m.from_user.username)
    refs = supabase.table("users").select("id", count="exact").eq("referred_by", m.from_user.id).execute().count
    ref_link = f"https://t.me/{bot.get_me().username}?start=ref_{m.from_user.id}"
    
    p_text = (
        f"👤 *ВАШ АККАУНТ*\n"
        f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        f"🆔 ID: `{m.from_user.id}`\n"
        f"💰 Баланс: *{u['balance']}* ⭐\n"
        f"✅ Выполнено: *{u['tasks_done']}*\n"
        f"👥 Рефералы: *{refs}*\n"
        f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        f"🔗 *Ваша ссылка для друзей:*\n{ref_link}\n\n"
        f"🎁 Приглашай друзей и получай по {REF_REWARD} ⭐ за каждого!"
    )
    bot.send_message(m.chat.id, p_text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "🏆 ЛИДЕРЫ")
def top(m):
    res = supabase.table("users").select("username, balance").order("balance", desc=True).limit(10).execute()
    text = "🏆 *ТОП-10 ПОЛЬЗОВАТЕЛЕЙ*\n\n"
    for i, user in enumerate(res.data, 1):
        name = user['username'] or "Аноним"
        text += f"{i}. {name} — *{user['balance']}* ⭐\n"
    bot.send_message(m.chat.id, text, parse_mode="Markdown")

# --- АДМИН-ПАНЕЛЬ (ДЛЯ ТЕБЯ) ---

@bot.message_handler(commands=['admin'])
def admin_panel(m):
    if not is_admin(m.from_user.id): return
    bot.send_message(m.chat.id, "👑 *АДМИН-МЕНЮ*\n\n/send — Рассылка всем\n/give ID СУММА — Выдать баланс", parse_mode="Markdown")

@bot.message_handler(commands=['send'])
def broadcast(m):
    if not is_admin(m.from_user.id): return
    msg = bot.send_message(m.chat.id, "Введите текст для рассылки всем юзерам:")
    bot.register_next_step_handler(msg, broadcast_process)

def broadcast_process(m):
    users = supabase.table("users").select("id").execute().data
    count = 0
    for u in users:
        try:
            bot.send_message(u['id'], m.text)
            count += 1
            time.sleep(0.05) # Защита от спам-фильтра
        except: pass
    bot.send_message(m.chat.id, f"✅ Рассылка завершена. Получили {count} юзеров.")

# ==========================================
# 6. ЗАПУСК (FLASK / WEBHOOK)
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
    return "<h1>Engine is running...</h1>", 200

if __name__ == "__main__":
    # Если запускаешь на ПК — раскомментируй нижние строки и закомментируй app.run
    # bot.remove_webhook()
    # bot.infinity_polling()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
