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

import requests

# Diccionario de alias (puedes ampliarlo)
COIN_ALIASES = {
    "btc": "bitcoin",
    "bitcoin": "bitcoin",
    "eth": "ethereum",
    "ethereum": "ethereum",
    "doge": "dogecoin",
    "dogecoin": "dogecoin",
    "ada": "cardano",
    "cardano": "cardano",
    "sol": "solana",
    "solana": "solana",
    "xrp": "ripple",
    "ripple": "ripple"
}

def precio(update, context):
    if not context.args:
        update.message.reply_text(
            "Uso: /precio <criptomoneda>\nEjemplo: /precio bitcoin o /precio btc"
        )
        return

    user_input = context.args[0].lower()
    coin_id = COIN_ALIASES.get(user_input, user_input)  # Usa alias o lo que haya puesto

    url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=eur"
    r = requests.get(url)
    data = r.json()
    # Responde con el precio si existe
    if coin_id in data and "eur" in data[coin_id]:
        price = data[coin_id]["eur"]
        update.message.reply_text(f"💶 Precio de {coin_id.title()}: {price} EUR")
    else:
        ejemplos = ", ".join(list(COIN_ALIASES.keys())[:5])
        update.message.reply_text(
            f"❌ Criptomoneda no encontrada: '{user_input}'\n"
            f"Ejemplos válidos: {ejemplos}\n"
            f"Puedes ver todos los IDs soportados aquí:\n"
            "https://api.coingecko.com/api/v3/coins/list"
        )


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
