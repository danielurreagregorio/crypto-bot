import os
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, CallbackContext

TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise Exception("TELEGRAM_TOKEN debe estar en las variables de entorno")

APP_URL = os.environ.get("APP_URL", "crypto-bot-ntrg.onrender.com")
PORT = int(os.environ.get("PORT", 10000))

bot = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, workers=4, use_context=True)

app = Flask(__name__)

def start(update: Update, context: CallbackContext):
    update.message.reply_text("¡Bot funcionando! 🎉 Prueba /start o /precio <cripto>")

dispatcher.add_handler(CommandHandler("start", start))

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK"

@app.route("/ping", methods=["GET"])
def ping():
    return "pong"

if __name__ == "__main__":
    bot.set_webhook(f"https://{APP_URL}/{TOKEN}")
    app.run(host="0.0.0.0", port=PORT)
