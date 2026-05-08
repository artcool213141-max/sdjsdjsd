import os
import telebot
from flask import Flask, request
from supabase import create_client
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    ReplyKeyboardMarkup, WebAppInfo
)

# --- Настройки ---
TOKEN = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
WEB_APP_URL = f"https://{os.environ.get('VERCEL_URL')}/" # Ссылка на твой фронт

bot = telebot.TeleBot(TOKEN, threaded=False)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
app = Flask(__name__)

# --- Логика БД ---

def get_user(user_id, username=None):
    res = supabase.table("users").select("*").eq("id", user_id).execute()
    if not res.data:
        # Регистрация нового юзера
        data = {"id": user_id, "username": username}
        res = supabase.table("users").insert(data).execute()
    return res.data[0]

def add_balance(user_id, amount):
    user = get_user(user_id)
    new_balance = float(user['balance']) + float(amount)
    supabase.table("users").update({"balance": new_balance}).eq("id", user_id).execute()

# --- Кнопки (Главное меню) ---

def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("🟡 Задания", "👤 Профиль")
    markup.add("💸 Вывести", "🏆 Топ")
    # Та самая синяя кнопка Mini App
    markup.add(InlineKeyboardButton("🤖 Мини-игры", web_app=WebAppInfo(url=WEB_APP_URL)))
    return markup

# --- Обработчики ---

@bot.message_handler(commands=['start'])
def start_cmd(message):
    user_id = message.from_user.id
    username = message.from_user.username
    
    # Обработка рефералки: /start ref_12345
    ref_parent = None
    if len(message.text.split()) > 1:
        parts = message.text.split()[1]
        if parts.startswith("ref_"):
            ref_parent = parts.replace("ref_", "")

    # Регаем юзера
    user_exists = supabase.table("users").select("id").eq("id", user_id).execute()
    if not user_exists.data:
        new_user = {
            "id": user_id,
            "username": username,
            "referred_by": int(ref_parent) if ref_parent and ref_parent.isdigit() else None
        }
        supabase.table("users").insert(new_user).execute()
        if ref_parent:
            # Начисляем 10 звезд пригласившему (как на скрине)
            add_balance(ref_parent, 10)
            bot.send_message(ref_parent, f"💎 Вам начислено 10 ⭐ за нового реферала @{username}!")

    bot.send_message(
        message.chat.id, 
        f"🚀 *BudaTasks* — лучший бот для фарма!", 
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

@bot.message_handler(func=lambda m: m.text == "👤 Профиль")
def profile(message):
    u = get_user(message.from_user.id)
    ref_link = f"https://t.me/{bot.get_me().username}?start=ref_{u['id']}"
    
    text = (
        f"👤 *Ваш профиль*\n\n"
        f"💰 Баланс: `{u['balance']}` ⭐\n"
        f"✅ Выполнено заданий: `{u['tasks_completed']}`\n\n"
        f"🔗 Твоя ссылка:\n`{ref_link}`\n\n"
        f"🎁 Награда за реферала: *10 ⭐*"
    )
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🍀 Бонус", callback_data="daily_bonus"))
    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🟡 Задания")
# Функция получения заданий "за звезды"
def get_available_star_tasks(user_id):
    # Берем ID уже выполненных заданий
    done_res = supabase.table("star_tasks_done").select("task_id").eq("user_id", user_id).execute()
    done_ids = [item['task_id'] for item in done_res.data]
    
    # Берем активные задания, которые еще не сделаны
    query = supabase.table("star_tasks").select("*").eq("active", True)
    if done_ids:
        query = query.not_.in_("id", done_ids)
    
    res = query.limit(5).execute() # Показываем по 5 штук за раз
    return res.data

# Обработчик кнопки "🟡 Задания"
@bot.message_handler(func=lambda m: m.text == "🟡 Задания")
def show_star_tasks(message):
    tasks = get_available_star_tasks(message.from_user.id)
    
    if not tasks:
        bot.send_message(message.chat.id, "📭 Пока нет новых заданий за звезды. Загляни позже!")
        return

    for t in tasks:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔗 Выполнить", url=t['url']))
        # Кнопка подтверждения выполнения
        markup.add(InlineKeyboardButton(f"💰 Получить {t['reward']} ⭐", callback_data=f"done_star_{t['id']}"))
        
        msg_text = (
            f"🌟 *Задание:* {t['title']}\n"
            f"📝 *Что нужно сделать:* {t['description'] if t['description'] else 'Перейти по ссылке'}\n"
            f"💵 *Награда:* `{t['reward']}` ⭐"
        )
        bot.send_message(message.chat.id, msg_text, parse_mode="Markdown", reply_markup=markup)

# Обработчик нажатия на кнопку "Получить награду"
@bot.callback_query_handler(func=lambda call: call.data.startswith("done_star_"))
def claim_star_reward(call):
    task_id = int(call.data.split("_")[2])
    user_id = call.from_user.id
    
    # 1. Проверяем, не нажимал ли он уже (защита от дабл-клика)
    check = supabase.table("star_tasks_done").select("*").eq("user_id", user_id).eq("task_id", task_id).execute()
    if check.data:
        bot.answer_callback_query(call.id, "⚠️ Вы уже получили награду!", show_alert=True)
        return

    # 2. Берем данные о задании
    task = supabase.table("star_tasks").select("*").eq("id", task_id).execute().data[0]
    
    # 3. Начисляем баланс и записываем выполнение
    add_balance(user_id, task['reward'])
    supabase.table("star_tasks_done").insert({"user_id": user_id, "task_id": task_id}).execute()
    
    # 4. Обновляем сообщение
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✅ Задание «{task['title']}» выполнено! \n💰 +{task['reward']} ⭐ начислено на баланс."
    )
    bot.answer_callback_query(call.id, "Успешно!")

@bot.message_handler(func=lambda m: m.text == "💸 Вывести")
def withdraw(message):
    u = get_user(message.from_user.id)
    bot.send_message(message.chat.id, f"💳 Доступно к выводу: `{u['balance']}` ⭐\n\nМинималка: 50 ⭐")

# --- Flask Роуты ---

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
    return "Bot is active", 200
