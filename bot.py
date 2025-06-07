import os
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler
import requests

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]

app = Flask(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
dispatcher = Dispatcher(bot, None, workers=1, use_context=True)

def start(update, context):
    update.message.reply_text("¡Hola! El bot funciona correctamente 😊\nUsa /precio <cripto> para consultar un precio.")

def precio(update, context):
    if not context.args:
        update.message.reply_text("Uso: /precio <criptomoneda>\nEjemplo: /precio bitcoin")
        return

    coin = context.args[0].lower()
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=eur"
    r = requests.get(url)
    if r.status_code != 200 or coin not in r.json():
        update.message.reply_text("Criptomoneda no encontrada.")
        return

    price = r.json()[coin]['eur']
    update.message.reply_text(f"El precio de {coin} es {price} EUR")

dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("precio", precio))

@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "ok"

@app.route("/")
def home():
    return "Bot de Telegram activo"

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 10000))
    HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "TU_HOSTNAME_RENDER")
    webhook_url = f"https://{HOSTNAME}/{TELEGRAM_TOKEN}"
    bot.set_webhook(webhook_url)
    print(f"Webhook establecido en: {webhook_url}")
    app.run(host="0.0.0.0", port=PORT)
