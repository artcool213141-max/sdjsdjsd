import os
import telebot

from flask import Flask, request
from supabase import create_client

from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup
)

# =========================
# ENV
# =========================

TOKEN = os.environ.get("BOT_TOKEN")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

print("ENV LOADED")

# =========================
# BOT
# =========================

bot = telebot.TeleBot(TOKEN)

print("BOT CREATED")

app = Flask(__name__)

# =========================
# SUPABASE
# =========================

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)

print("SUPABASE CONNECTED")

# =========================
# FUNCTIONS
# =========================

def get_sponsor_channels():

    try:

        response = supabase.table(
            "sponsor_channels"
        ).select("*").eq(
            "active",
            True
        ).execute()

        print(response.data)

        return response.data

    except Exception as e:

        print(e)

        return []

def check_subscriptions(user_id):

    channels = get_sponsor_channels()

    for channel in channels:

        try:

            member = bot.get_chat_member(
                channel["username"],
                user_id
            )

            if member.status not in [
                "member",
                "administrator",
                "creator"
            ]:
                return False

        except Exception as e:

            print(e)

            return False

    return True

# =========================
# START
# =========================

@bot.message_handler(commands=["start"])
def start(message):

    print("START WORKED")

    channels = get_sponsor_channels()

    print(channels)

    keyboard = InlineKeyboardMarkup()

    for channel in channels:

        keyboard.add(
            InlineKeyboardButton(
                text=f"📢 {channel['title']}",
                url=channel["url"]
            )
        )

    keyboard.add(
        InlineKeyboardButton(
            text="✅ Проверить подписку",
            callback_data="check_subs"
        )
    )

    bot.send_message(
        message.chat.id,
        "Подпишитесь на все каналы:",
        reply_markup=keyboard
    )

# =========================
# CHECK SUBS
# =========================

@bot.callback_query_handler(
    func=lambda call: call.data == "check_subs"
)
def check_subs(call):

    ok = check_subscriptions(
        call.from_user.id
    )

    if not ok:

        bot.answer_callback_query(
            call.id,
            "❌ Подпишитесь на все каналы",
            show_alert=True
        )

        return

    menu = ReplyKeyboardMarkup(
        resize_keyboard=True
    )

    menu.row(
        "🎯 Задания",
        "👤 Профиль"
    )

    menu.row(
        "🏆 Топ"
    )

    bot.send_message(
        call.message.chat.id,
        "✅ Подписки подтверждены!",
        reply_markup=menu
    )

# =========================
# ROUTES
# =========================

# =========================
# ROUTES
# =========================

@app.route("/", methods=["POST"])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return 'OK', 200
    else:
        return "Error", 403

@app.route("/", methods=["GET"])
def index():
    return "Bot is running", 200
