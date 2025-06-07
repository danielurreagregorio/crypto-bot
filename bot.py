import os
import json
import time
from datetime import datetime, timedelta
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler
import requests

# --- Variables globales de CoinGecko ---
COIN_LIST_URL = "https://api.coingecko.com/api/v3/coins/list"
COIN_ALIASES = {
    "btc": "bitcoin", "bitcoin": "bitcoin",
    "eth": "ethereum", "ethereum": "ethereum",
    "doge": "dogecoin", "dogecoin": "dogecoin",
    "ada": "cardano", "cardano": "cardano",
    "sol": "solana", "solana": "solana",
    "xrp": "ripple", "ripple": "ripple"
}
TOP_COINS = COIN_ALIASES  # Atajo para popular el mapping
CACHE_FILE = "coin_list_cache.json"
CACHE_TTL = timedelta(hours=24)

coin_symbol_to_id = {}   # symbol -> [id, ...]
coin_name_to_id = {}     # name -> id

def load_coin_mappings():
    """Carga la lista de monedas con caché local (24h)."""
    global coin_symbol_to_id, coin_name_to_id

    now = datetime.utcnow()

    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
        ts = datetime.fromisoformat(data.get("_cached_at"))
        if now - ts < CACHE_TTL:
            entries = data["coins"]
        else:
            entries = None
    else:
        entries = None

    if entries is None:
        try:
            resp = requests.get(COIN_LIST_URL, timeout=10)
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 429:
                print("Rate limited by CoinGecko, waiting 60 seconds…")
                time.sleep(60)
                resp = requests.get(COIN_LIST_URL, timeout=10)
                resp.raise_for_status()
        entries = resp.json()
        cache_payload = {"_cached_at": now.isoformat(), "coins": entries}
        with open(CACHE_FILE, "w") as f:
            json.dump(cache_payload, f)

    coin_symbol_to_id.clear()
    coin_name_to_id.clear()
    for entry in entries:
        cid  = entry["id"]
        sym  = entry["symbol"].lower()
        name = entry["name"].lower()
        coin_symbol_to_id.setdefault(sym, []).append(cid)
        coin_name_to_id[name] = cid

    # Forzar alias populares (sobrescribe si hace falta)
    for sym, cid in TOP_COINS.items():
        coin_symbol_to_id[sym] = [cid]
        coin_name_to_id[cid] = cid

def resolve_coin(user_input: str):
    """Devuelve el ID CoinGecko de la cripto introducida."""
    key = user_input.strip().lower()
    if key in coin_name_to_id:
        return coin_name_to_id[key]
    if key in coin_symbol_to_id:
        # Si varias, devuelve la más popular
        return coin_symbol_to_id[key][0]
    return None

# --- Config Flask y Telegram ---
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
app = Flask(__name__)
bot = Bot(token=TELEGRAM_TOKEN)
dispatcher = Dispatcher(bot, None, workers=1, use_context=True)

def start(update, context):
    update.message.reply_text(
        "¡Hola! El bot funciona correctamente 😊\nUsa /precio <cripto> para consultar un precio."
    )

def precio(update, context):
    load_coin_mappings()
    args = context.args
    if len(args) != 1:
        update.message.reply_text("Uso: /precio <cripto>\nEjemplo: /precio bitcoin")
        return

    entrada = args[0]
    coin_id = resolve_coin(entrada)
    if not coin_id:
        # Sugerir hasta 10 alias válidos si no se encuentra
        sugerencias = ", ".join(list(coin_symbol_to_id.keys())[:10])
        update.message.reply_text(
            f"⚠️ Criptomoneda '{entrada}' no reconocida.\nEjemplo de válidos: {sugerencias}"
        )
        return

    moneda = "usd"
    url = (
        f"https://api.coingecko.com/api/v3/simple/price?"
        f"ids={coin_id}&vs_currencies={moneda}"
    )
    resp = requests.get(url)
    if resp.status_code != 200:
        update.message.reply_text(f"⚠️ No pude obtener precio de '{entrada}'.")
        return

    data = resp.json()
    if coin_id not in data or moneda not in data[coin_id]:
        update.message.reply_text(
            f"⚠️ La criptomoneda '{entrada}' o la divisa '{moneda}' no existen."
        )
        return

    price_raw = data[coin_id][moneda]
    price_str = f"{price_raw:.8f}" if price_raw < 1 else f"{price_raw:,.2f}".replace(",", ".")
    texto = f"💲 *{entrada.upper()}* = *{price_str} {moneda.upper()}*"
    update.message.reply_text(texto, parse_mode="Markdown")

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
