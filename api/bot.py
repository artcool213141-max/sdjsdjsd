import os
import telebot

from supabase import create_client

from flask import Flask, request
from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup
)

# =========================
# ENV
# =========================

TOKEN = os.environ["BOT_TOKEN"]

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

# =========================
# SUPABASE
# =========================

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)

# =========================
# BOT
# =========================

bot = telebot.TeleBot(TOKEN)

app = Flask(__name__)

# =========================
# FUNCTIONS
# =========================

def get_sponsor_channels():
    response = supabase.table(
        "sponsor_channels"
    ).select("*").eq(
        "active",
        True
    ).execute()

    return response.data


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

        except:
            return False

    return True

# =========================
# START
# =========================

@bot.message_handler(commands=["start"])
def start(message):
    channels = get_sponsor_channels()

    keyboard = InlineKeyboardMarkup()

    for channel in channels:
        keyboard.add(
            InlineKeyboardButton(
                f"📢 {channel['title']}",
                url=channel["url"]
            )
        )

    keyboard.add(
        InlineKeyboardButton(
            "✅ Проверить подписку",
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
    func=lambda c: c.data == "check_subs"
)
def check(callback):

    ok = check_subscriptions(
        callback.from_user.id
    )

    if not ok:
        bot.answer_callback_query(
            callback.id,
            "❌ Подпишитесь на все каналы",
            show_alert=True
        )
        return

    menu = ReplyKeyboardMarkup(
        resize_keyboard=True
    )

    menu.row("🎯 Задания", "👤 Профиль")
    menu.row("💸 Вывести", "🏆 Топ")

    bot.send_message(
        callback.message.chat.id,
        "✅ Подписки подтверждены!",
        reply_markup=menu
    )

# =========================
# WEBHOOK
# =========================

@app.route("/", methods=["POST"])
def webhook():
    json_str = request.get_data().decode("utf-8")

    update = telebot.types.Update.de_json(
        json_str
    )

    bot.process_new_updates([update])

    return "ok", 200


@app.route("/", methods=["GET"])
def index():
    return "Bot is running"
