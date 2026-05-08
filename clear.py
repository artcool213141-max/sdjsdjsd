import telebot
from telebot import types

# Вставь сюда свой токен
bot = telebot.TeleBot("8431093842:AAG0IwMcX2miNvvhKay_WUaIWGaMQAjduWo")

@bot.message_handler(commands=['start'])
def start(message):
    # Создаем команду на удаление кнопок
    markup = types.ReplyKeyboardRemove()
    bot.send_message(message.chat.id, "Кнопки удалены! Теперь открывай Mini App через кнопку меню.", reply_markup=markup)

print("Бот запущен. Напиши ему /start в Телеге...")
bot.polling()
