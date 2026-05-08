import os
import time
import telebot
from flask import Flask, request
from supabase import create_client
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    ReplyKeyboardMarkup, KeyboardButton
)

# ==========================================
# КОНФИГУРАЦИЯ И ИНИЦИАЛИЗАЦИЯ
# ==========================================

TOKEN = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# Проверка критических переменных
if not all([TOKEN, SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("Критические переменные окружения не установлены!")

bot = telebot.TeleBot(TOKEN, threaded=False)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
app = Flask(__name__)

# Списки администраторов (впиши свои ID через запятую)
ADMINS = [8431093842] 

# ==========================================
# СИСТЕМНЫЕ ФУНКЦИИ БАЗЫ ДАННЫХ
# ==========================================

def get_user_data(user_id, username="Unknown"):
    """Получает или создает данные пользователя с глубокой инициализацией."""
    try:
        res = supabase.table("users").select("*").eq("id", user_id).execute()
        if not res.data:
            new_user = {
                "id": user_id,
                "username": username,
                "balance": 0.0,
                "tasks_completed": 0,
                "referred_by": None,
                "is_banned": False,
                "last_active": "now()"
            }
            res = supabase.table("users").insert(new_user).execute()
        return res.data[0]
    except Exception as e:
        print(f"DB Error (get_user): {e}")
        return None

def update_balance(user_id, amount, reason="task"):
    """Безопасное обновление баланса с логированием (если нужно)."""
    user = get_user_data(user_id)
    if not user: return False
    
    new_balance = round(float(user['balance']) + float(amount), 2)
    try:
        supabase.table("users").update({
            "balance": new_balance,
            "last_active": "now()"
        }).eq("id", user_id).execute()
        return True
    except Exception as e:
        print(f"DB Error (update_balance): {e}")
        return False

# ==========================================
# КЛАВИАТУРЫ И ИНТЕРФЕЙС
# ==========================================

def get_main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("🟡 Задания"), 
        KeyboardButton("👤 Профиль")
    )
    markup.add(
        KeyboardButton("🏆 Топ"), 
        KeyboardButton("💸 Вывести")
    )
    return markup

def get_task_keyboard(task_id, url):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🤖 Открыть задание", url=url))
    markup.row(
        InlineKeyboardButton("✅ Проверить", callback_data=f"verify_{task_id}"),
        InlineKeyboardButton("🔄 Пропустить", callback_data="skip_task")
    )
    return markup

# ==========================================
# ОБРАБОТЧИКИ СООБЩЕНИЙ (COMMANDS)
# ==========================================

@bot.message_handler(commands=['start'])
def handle_start(message):
    uid = message.from_user.id
    uname = message.from_user.username
    
    # Обработка реферального кода
    args = message.text.split()
    referrer = None
    if len(args) > 1 and args[1].startswith("ref_"):
        ref_id = args[1].replace("ref_", "")
        if ref_id.isdigit() and int(ref_id) != uid:
            referrer = int(ref_id)

    # Инициализация пользователя
    user = get_user_data(uid, uname)
    
    if referrer and not user.get('referred_by'):
        # Если юзер новый и пришел по ссылке — записываем родителя
        # (Логика начисления бонуса родителю может быть тут или при первом задании)
        supabase.table("users").update({"referred_by": referrer}).eq("id", uid).execute()
        try:
            bot.send_message(referrer, f"💎 У вас новый реферал: @{uname}! Вы получите бонус после его первого задания.")
        except: pass

    welcome_text = (
        f"🚀 *Добро пожаловать в BudaTasks, @{uname}!*\n\n"
        "Выполняй простые задания, приглашай друзей и зарабатывай реальные звезды (⭐).\n\n"
        "👇 Используй меню ниже, чтобы начать:"
    )
    bot.send_message(uid, welcome_text, parse_mode="Markdown", reply_markup=get_main_menu())

# ==========================================
# ФУНКЦИОНАЛ: ПРОФИЛЬ
# ==========================================

@bot.message_handler(func=lambda m: m.text == "👤 Профиль")
def handle_profile(message):
    uid = message.from_user.id
    u = get_user_data(uid)
    
    if not u:
        bot.send_message(message.chat.id, "❌ Ошибка загрузки профиля.")
        return

    # Считаем количество рефералов
    refs_count = supabase.table("users").select("id", count="exact").eq("referred_by", uid).execute().count
    
    ref_link = f"https://t.me/{bot.get_me().username}?start=ref_{uid}"
    
    profile_text = (
        f"👤 *ЛИЧНЫЙ КАБИНЕТ*\n"
        f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        f"🆔 Ваш ID: `{uid}`\n"
        f"💰 Баланс: *{u['balance']}* ⭐\n"
        f"✅ Заданий выполнено: *{u.get('tasks_completed', 0)}*\n"
        f"👥 Приглашено друзей: *{refs_count}*\n"
        f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        f"🔗 *Реферальная ссылка:*\n{ref_link}\n\n"
        f"🎁 _Получай 10 ⭐ за каждого активного друга!_"
    )
    
    bot.send_message(message.chat.id, profile_text, parse_mode="Markdown")

# ==========================================
# ФУНКЦИОНАЛ: ТОП ИГРОКОВ
# ==========================================

@bot.message_handler(func=lambda m: m.text == "🏆 Топ")
def handle_top(message):
    try:
        # Сортировка по балансу
        res = supabase.table("users").select("username, balance").order("balance", desc=True).limit(10).execute()
        
        leaderboard = "🏆 *ТОП-10 МАЙНЕРОВ СЕЗОНА*\n\n"
        for i, user in enumerate(res.data, 1):
            name = user['username'] if user['username'] else f"User_{i}"
            # Экранирование для Markdown
            name = name.replace("_", "\\_").replace("*", "\\*")
            leaderboard += f"{i}. {name} — *{user['balance']}* ⭐\n"
        
        leaderboard += (
            "\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            "🎁 *Награда:* Топ-10 игроков получат эксклюзивные бонусы в конце месяца!"
        )
        bot.send_message(message.chat.id, leaderboard, parse_mode="Markdown")
    except Exception as e:
        bot.send_message(message.chat.id, "🛠 Секция ТОП временно на тех. обслуживании.")

# ==========================================
# ФУНКЦИОНАЛ: ЗАДАНИЯ (ENGINE)
# ==========================================

@bot.message_handler(func=lambda m: m.text == "🟡 Задания")
def handle_tasks_hub(message):
    uid = message.from_user.id
    
    # 1. Получаем выполненные ID
    done_res = supabase.table("star_tasks_done").select("task_id").eq("user_id", uid).execute()
    done_ids = [item['task_id'] for item in done_res.data]
    
    # 2. Ищем доступное задание
    query = supabase.table("star_tasks").select("*").eq("active", True)
    if done_ids:
        query = query.not_.in_("id", done_ids)
    
    tasks = query.limit(1).execute().data
    
    if not tasks:
        bot.send_message(uid, "📭 *Все задания выполнены!*\nНовые появятся совсем скоро. Следите за обновлениями.", parse_mode="Markdown")
        return

    t = tasks[0]
    task_card = (
        f"✨ *НОВОЕ ЗАДАНИЕ*\n\n"
        f"📌 *{t['title']}*\n"
        f"📝 {t['description'] if t['description'] else 'Перейдите по ссылке и выполните условия.'}\n\n"
        f"💰 Награда: *+{t['reward']}* ⭐"
    )
    
    bot.send_message(uid, task_card, parse_mode="Markdown", reply_markup=get_task_keyboard(t['id'], t['url']))

@bot.callback_query_handler(func=lambda call: call.data.startswith("verify_"))
def callback_verify(call):
    task_id = int(call.data.split("_")[1])
    uid = call.from_user.id
    
    # Проверка на повторное выполнение (на всякий случай)
    check = supabase.table("star_tasks_done").select("*").eq("user_id", uid).eq("task_id", task_id).execute()
    if check.data:
        bot.answer_callback_query(call.id, "⚠️ Вы уже получили награду!", show_alert=True)
        return

    # Данные задания
    t_data = supabase.table("star_tasks").select("*").eq("id", task_id).execute().data
    if not t_data: return
    task = t_data[0]

    # Начисление
    if update_balance(uid, task['reward']):
        supabase.table("star_tasks_done").insert({"user_id": uid, "task_id": task_id}).execute()
        
        # Обновляем статистику выполненных
        u = get_user_data(uid)
        supabase.table("users").update({"tasks_completed": (u.get('tasks_completed', 0) or 0) + 1}).eq("id", uid).execute()
        
        bot.answer_callback_query(call.id, f"✅ Успешно! +{task['reward']} ⭐", show_alert=False)
        bot.edit_message_text(
            f"✅ *Задание выполнено!*\nВы получили {task['reward']} ⭐",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown"
        )
        # С задержкой предлагаем следующее задание
        time.sleep(1)
        handle_tasks_hub(call.message)
    else:
        bot.answer_callback_query(call.id, "❌ Ошибка при начислении.", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data == "skip_task")
def callback_skip(call):
    bot.delete_message(call.message.chat.id, call.message.message_id)
    bot.send_message(call.message.chat.id, "🔄 Ищем другое задание...")
    handle_tasks_hub(call.message)

# ==========================================
# СЕКЦИЯ: ВЫВОД (ЗАГЛУШКА)
# ==========================================

@bot.message_handler(func=lambda m: m.text == "💸 Вывести")
def handle_withdraw(message):
    u = get_user_data(message.from_user.id)
    text = (
        f"💸 *ВЫВОД СРЕДСТВ*\n\n"
        f"Доступно: *{u['balance']}* ⭐\n"
        f"Минимальная сумма: *50 ⭐*\n\n"
        f"⚠️ На данный момент вывод временно ограничен до начала финального этапа распределения (Airdrop)."
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

# ==========================================
# FLASK & WEBHOOKS
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
    return "<h1>BudaTasks Engine Active</h1>", 200

if __name__ == "__main__":
    # Для локального запуска (не для Vercel)
    # bot.remove_webhook()
    # bot.infinity_polling()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
