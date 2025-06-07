import os
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, CallbackContext
import requests

TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("❌ Debes definir la variable de entorno TELEGRAM_TOKEN")

bot = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, workers=4, use_context=True)
app = Flask(__name__)

# ===============================
# Comando /start
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 ¡Hola! Soy tu CryptoPriceBot.\n"
        "Usa /precio <criptomoneda> para consultar el precio.\n"
        "Ejemplo: /precio bitcoin"
    )

dispatcher.add_handler(CommandHandler("start", start))

# ===============================
# Comando /precio <cripto>
def precio(update: Update, context: CallbackContext):
    args = context.args
    if not args:
        update.message.reply_text("⚠️ Debes indicar una criptomoneda.\nEjemplo: /precio bitcoin")
        return

    coin = args[0].lower()
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=eur,usd"
    try:
        resp = requests.get(url, timeout=8)
        data = resp.json()
        if coin in data:
            price_eur = data[coin].get("eur")
            price_usd = data[coin].get("usd")
            texto = f"💲 *{coin.capitalize()}*\n"
            if price_eur is not None:
                texto += f"EUR: {price_eur}\n"
            if price_usd is not None:
                texto += f"USD: {price_usd}\n"
            update.message.reply_text(texto, parse_mode="Markdown")
        else:
            update.message.reply_text("❌ Criptomoneda no encontrada en CoinGecko. Intenta con su nombre en inglés, ej: /precio bitcoin")
    except Exception as e:
        update.message.reply_text("⚠️ Error consultando CoinGecko.")
        print("Error CoinGecko:", e)

dispatcher.add_handler(CommandHandler("precio", precio))

# ===============================
# Endpoint para webhook de Telegram
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK"

# Endpoint de prueba (opcional)
@app.route("/ping", methods=["GET"])
def ping():
    return "pong"

if __name__ == "__main__":
    # ¡Importante! Poner el webhook en cada arranque en Render:
    render_url = os.getenv("APP_URL", "crypto-bot-ntrg.onrender.com")
    bot.set_webhook(f"https://{render_url}/{TOKEN}")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
