import os
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler

# Configura el token de Telegram desde variable de entorno
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]

# Crea la app Flask
app = Flask(__name__)

# Crea el bot de Telegram y Dispatcher
bot = Bot(token=TELEGRAM_TOKEN)
dispatcher = Dispatcher(bot, None, workers=1, use_context=True)

# Handler del comando /start
def start(update, context):
    update.message.reply_text("¡Hola! El bot funciona correctamente 😊")

# Añade el handler al dispatcher
dispatcher.add_handler(CommandHandler("start", start))

# Endpoint del webhook (debe coincidir EXACTAMENTE con el webhook de Telegram)
@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "ok"

# Un endpoint para pruebas básicas (opcional)
@app.route("/")
def home():
    return "Bot de Telegram activo"

if __name__ == "__main__":
    # Puerto que Render expone automáticamente
    PORT = int(os.environ.get("PORT", 10000))
    # Seteamos el webhook al arrancar
    HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    if not HOSTNAME:
        # En local, puedes definirlo tú, pero en Render se autoasigna
        HOSTNAME = "TU_HOSTNAME_RENDER"
    webhook_url = f"https://{HOSTNAME}/{TELEGRAM_TOKEN}"
    bot.set_webhook(webhook_url)
    print(f"Webhook establecido en: {webhook_url}")
    app.run(host="0.0.0.0", port=PORT)
