import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from dotenv import load_dotenv
import os

load_dotenv()

bot = telebot.TeleBot(os.getenv("BOT_TOKEN"))

sponsor_channels = [
    {
        "title": "BudaStars",
        "url": "https://t.me/BudaStars",
        "username": "@BudaStars"
    }
]

def check_subscriptions(user_id):
    for channel in sponsor_channels:
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


@bot.message_handler(commands=["start"])
def start(message):
    keyboard = InlineKeyboardMarkup()

    for channel in sponsor_channels:
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


@bot.callback_query_handler(func=lambda c: c.data == "check_subs")
def check(callback):
    ok = check_subscriptions(callback.from_user.id)

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


print("Bot started")
bot.infinity_polling()
