# bot.py

import os
import sys
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from flask import Flask, request
import psycopg2
from psycopg2.extras import RealDictCursor
from requests import get
from telegram import Bot, Update, ParseMode
from telegram.utils.request import Request as TGRequest
from telegram.ext import Dispatcher, CommandHandler, CallbackContext
from apscheduler.schedulers.background import BackgroundScheduler
from elasticsearch import Elasticsearch
from es_logger import ElasticsearchHandler
import json

# Load environment
load_dotenv()
import os
print("ENTORNO:", dict(os.environ))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ELASTICSEARCH_URL = os.getenv("ELASTICSEARCH_URL", "")
APP_URL = os.getenv("RENDER_EXTERNAL_URL", "crypto-bot-ntrg.onrender.com")
print(f"DEBUG: APP_URL = '{APP_URL}'")

PORT = int(os.environ.get("PORT", 5000))

if not TELEGRAM_TOKEN:
    print("❌ ERROR: TELEGRAM_TOKEN no encontrado en el entorno.", file=sys.stderr)
    sys.exit(1)
if not DATABASE_URL:
    print("❌ ERROR: DATABASE_URL no encontrado en el entorno.", file=sys.stderr)
    sys.exit(1)


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("crypto_bot")
console_handler = logging.StreamHandler()
console_formatter = logging.Formatter("%(asctime)s %(levelname)s:%(name)s:%(message)s")
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

# Elasticsearch (optional)
es = None
if ELASTICSEARCH_URL:
    es_client = Elasticsearch([ELASTICSEARCH_URL], max_retries=3, retry_on_timeout=True)
    es_handler = ElasticsearchHandler(es_client, index_prefix="telegram-bot-logs")
    logger.addHandler(es_handler)
    es = es_client
    logger.info(f"Conectado a Elasticsearch en {ELASTICSEARCH_URL}")
else:
    logger.info("Elasticsearch desactivado: no se configuró ELASTICSEARCH_URL")

# Database connection
def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

# ================================================
# CoinGecko mappings
COIN_LIST_URL = "https://api.coingecko.com/api/v3/coins/list"
coin_symbol_to_id = {}
coin_name_to_id = {}
TOP_COINS = {
    "btc": "bitcoin",
    "eth": "ethereum",
    "doge": "dogecoin",
    "xrp": "ripple",
    "ada": "cardano",
    "bnb": "binancecoin",
    "matic": "matic-network",
    "sol": "solana",
}

CACHE_FILE = "coin_list_cache.json"
CACHE_TTL = timedelta(hours=24)

def load_coin_mappings():
    """
    Descarga y cachea coin list de CoinGecko.
    Sólo hace la petición si el cache tiene más de 24h.
    """
    now = datetime.utcnow()

    # 1) Si existe cache y es reciente, lo leemos
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
        # Aquí sólo llamar a CoinGecko si no hay cache reciente
        resp = get(COIN_LIST_URL, timeout=10)
        resp.raise_for_status()
        entries = resp.json()
        # Guardar en cache
        cache_payload = {
            "_cached_at": now.isoformat(),
            "coins": entries
        }
        with open(CACHE_FILE, "w") as f:
            json.dump(cache_payload, f)


    # 3) Llenamos los diccionarios
    coin_symbol_to_id.clear()
    coin_name_to_id.clear()
    for entry in entries:
        cid  = entry["id"]
        sym  = entry["symbol"].lower()
        name = entry["name"].lower()
        coin_symbol_to_id.setdefault(sym, []).append(cid)
        coin_name_to_id[name] = cid

    # 4) Forzar TOP_COINS si quieres
    for sym, cid in TOP_COINS.items():
        coin_symbol_to_id[sym] = [cid]
        coin_name_to_id[cid] = cid

def elegir_top_coin_por_symbol(symbol: str) -> str:
    ids = coin_symbol_to_id.get(symbol, [])
    if len(ids) == 1:
        return ids[0]
    url = (f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids={','.join(ids)}&"  
           f"order=market_cap_desc&per_page={len(ids)}")
    try:
        data = get(url, timeout=10).json()
        return data[0]["id"] if data else ids[0]
    except:
        return ids[0]


def resolve_coin(user_input: str) -> str:
    key = user_input.strip().lower()
    if key in TOP_COINS:
        return TOP_COINS[key]
    if key in coin_name_to_id:
        return coin_name_to_id[key]
    if key in coin_symbol_to_id:
        return elegir_top_coin_por_symbol(key)
    return None


def format_price(price: float) -> str:
    if price < 1:
        return f"{price:.8f}"
    if price < 1000:
        return f"{price:.4f}"
    return f"{price:.2f}"

# ================================================
# Database initialization

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS price_alerts (
      id SERIAL PRIMARY KEY,
      user_id BIGINT NOT NULL,
      crypto_id TEXT NOT NULL,
      condition TEXT NOT NULL,
      threshold DOUBLE PRECISION NOT NULL,
      active BOOLEAN NOT NULL DEFAULT TRUE,
      created_at TIMESTAMPTZ NOT NULL
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_prefs (
      user_id BIGINT PRIMARY KEY,
      currency TEXT NOT NULL
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS portfolio (
      id SERIAL PRIMARY KEY,
      user_id BIGINT NOT NULL,
      crypto_id TEXT NOT NULL,
      cantidad DOUBLE PRECISION NOT NULL,
      precio_compra DOUBLE PRECISION NOT NULL,
      fecha TIMESTAMPTZ NOT NULL
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS variation_alerts (
      id SERIAL PRIMARY KEY,
      user_id BIGINT NOT NULL,
      crypto_id TEXT NOT NULL,
      base_price DOUBLE PRECISION NOT NULL,
      porcentaje DOUBLE PRECISION NOT NULL,
      active BOOLEAN NOT NULL DEFAULT TRUE,
      created_at TIMESTAMPTZ NOT NULL
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS portfolio_variation_alerts (
      id SERIAL PRIMARY KEY,
      user_id BIGINT UNIQUE NOT NULL,
      base_value DOUBLE PRECISION NOT NULL,
      porcentaje DOUBLE PRECISION NOT NULL,
      active BOOLEAN NOT NULL DEFAULT TRUE,
      created_at TIMESTAMPTZ NOT NULL
    )""")
    conn.commit()
    cur.close()
    conn.close()

def add_alert(user_id: int, crypto_id: str, condition: str, threshold: float):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO price_alerts (user_id, crypto_id, condition, threshold, active, created_at)
        VALUES (%s, %s, %s, %s, 1, %s)
    """, (user_id, crypto_id, condition, threshold, datetime.utcnow()))
    conn.commit()
    cursor.close()
    conn.close()

def list_alerts(user_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, crypto_id, condition, threshold, created_at
          FROM price_alerts
         WHERE user_id = %s AND active = TRUE
        """,
        (user_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def deactivate_alert(alert_id: int):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("UPDATE price_alerts SET active = 0 WHERE id = %s", (alert_id,))
    conn.commit()
    cursor.close()
    conn.close()

def set_currency(user_id: int, currency: str):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO user_prefs (user_id, currency)
        VALUES (%s, %s)
        ON CONFLICT(user_id) DO UPDATE SET currency = excluded.currency
    """, (user_id, currency.lower()))
    conn.commit()
    cursor.close()
    conn.close()

def get_currency(user_id: int) -> str:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT currency FROM user_prefs WHERE user_id = %s",
        (user_id,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else "usd"



# ------------------------------------------------------------
# 2) CARGAR TOKEN y CONFIGURAR LOGGERS
# ------------------------------------------------------------


# Elasticsearch (opcional)
es_host = os.getenv("ELASTICSEARCH_HOST", "http://localhost:9200")
es_user = os.getenv("ELASTIC_USER", "elastic")
es_pass = os.getenv("ELASTIC_PASSWORD", "")

print("Usando Telegram token:", TELEGRAM_TOKEN[:10] + "…")

logger = logging.getLogger("telegram_bot")
logger.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_formatter = logging.Formatter("%(asctime)s %(levelname)s:%(name)s:%(message)s")
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)


class BotLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        extra = self.extra.copy()
        if "extra" in kwargs:
            extra.update(kwargs["extra"])
        kwargs["extra"] = extra
        return msg, kwargs

log = BotLoggerAdapter(logger, {"bot_name": "crypto_price_bot"})


# ------------------------------------------------------------
# 3) HANDLERS DE TELEGRAM (ESPAÑOL)
# ------------------------------------------------------------
def start(update: Update, context: CallbackContext) -> None:
    user = update.effective_user.first_name or update.effective_user.username
    texto = (
        f"👋 ¡Hola, *{user}*! Soy tu CryptoPriceBot.\n\n"
        "/precio <cripto>  — Consultar precio\n"
        "/alerta_crear  — Crear alerta de precio\n"
        "/alerta_listar — Listar alertas activas\n"
        "/config_divisa — Cambiar divisa predeterminada\n"
        "/portafolio     — Gestionar portafolio"
    )
    update.effective_chat.send_message(text=texto, parse_mode=ParseMode.MARKDOWN)

# Define precio, alerta_crear, alerta_listar, alerta_borrar, config_divisa,
# portafolio_handler, help_handler, check_price_alerts, check_variation_alerts,
# check_portfolio_variation_alerts, get_portfolio_total_value, etc.,
# following la lógica original, pero referenciando solo las funciones CRUD definidas.

# ================================================
# Application setup



def precio(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    args = context.args

    if len(args) != 1:
        update.message.reply_text(
            "⚠️ Uso: `/precio <cripto>`\nEjemplo: `/precio bitcoin`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    entrada = args[0]
    coin_id = resolve_coin(entrada)
    log.info("Resolviendo criptomoneda", extra={"extra": {"input": entrada, "coin_id": coin_id}})
    if not coin_id:
        update.message.reply_text(f"⚠️ Criptomoneda '{entrada}' no reconocida.")
        return

    moneda = get_currency(chat_id)  # ej. "usd", "eur"
    url = (
        f"https://api.coingecko.com/api/v3/simple/price?"
        f"ids={coin_id}&vs_currencies={moneda}"
    )
    resp = get(url)
    if resp.status_code != 200:
        update.message.reply_text(f"⚠️ No pude obtener precio de '{entrada}'.")
        return

    data = resp.json()
    if coin_id not in data or moneda not in data[coin_id]:
        update.message.reply_text(f"⚠️ La criptomoneda '{entrada}' o la divisa '{moneda}' no existen.")
        return

    price_raw = data[coin_id][moneda]

    # —–> REGISTRAR ALERTA DE VARIACIÓN 5%
    register_variation_alert(chat_id, coin_id, price_raw)

    # Formatear número: si precio < 1, mostrar 8 decimales; si no, 2 decimales.
    if price_raw < 1:
        price_str = f"{price_raw:.8f}"
    else:
        price_str = f"{price_raw:,.2f}".replace(",", ".")

    texto = f"💲 *{entrada.upper()}* = *{price_str} {moneda.upper()}*"
    context.bot.send_message(chat_id=chat_id, text=texto, parse_mode=ParseMode.MARKDOWN)

    log.info(
        "Precio enviado",
        extra={"extra": {
            "command": "/precio",
            "coin_id": coin_id,
            "input": entrada,
            "price_raw": price_raw,
            "formatted": price_str,
            "currency": moneda,
            "chat_id": chat_id
        }}
    )


def alerta_crear(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    args = context.args

    if len(args) != 3:
        update.message.reply_text(
            "⚠️ Uso correcto:\n"
            "/alerta_crear <cripto> <arriba|abajo> <umbral>\n"
            "Ejemplo: /alerta_crear bitcoin arriba 30000"
        )
        return

    entrada = args[0]
    condicion = args[1].lower()
    try:
        umbral = float(args[2])
    except ValueError:
        update.message.reply_text("⚠️ El umbral debe ser un número, p.ej. 30000")
        return

    if condicion not in ("arriba", "abajo"):
        update.message.reply_text("⚠️ La condición debe ser 'arriba' o 'abajo'.")
        return

    coin_id = resolve_coin(entrada)
    if not coin_id:
        update.message.reply_text(f"⚠️ Criptomoneda '{entrada}' no reconocida.")
        return

    cond_eng = "above" if condicion == "arriba" else "below"
    add_alert(chat_id, coin_id, cond_eng, umbral)

    moneda = get_currency(chat_id).upper()
    update.message.reply_text(
        f"✅ Alerta creada: Te avisaré cuando {entrada.upper()} esté {condicion} {umbral:.2f} {moneda}.",
        parse_mode=ParseMode.MARKDOWN
    )
    log.info(
        "Alerta creada",
        extra={"extra": {
            "command": "/alerta_crear",
            "coin_id": coin_id,
            "input": entrada,
            "condition": cond_eng,
            "threshold": umbral,
            "currency": moneda,
            "chat_id": chat_id
        }}
    )

    # —–> OTRA VEZ: Registrar alerta de variación 5%, tomando el UMBRAL como precio base
    register_variation_alert(chat_id, coin_id, umbral)


def alerta_listar(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    rows = list_alerts(chat_id)
    if not rows:
        update.message.reply_text("No tienes alertas activas.")
        return

    mensajes = ["🔔 Tus alertas activas:"]
    moneda = get_currency(chat_id).upper()
    for aid, cid, cond, thr, created in rows:
        cond_es = "arriba" if cond == "above" else "abajo"
        mensajes.append(f"{aid}. {cid.upper()} {cond_es} {thr:.2f} {moneda} (creada: {created[:10]})")
    update.message.reply_text("\n".join(mensajes))

def alerta_borrar(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    args = context.args
    if len(args) != 1:
        update.message.reply_text("⚠️ Uso: /alerta_borrar <id>")
        return

    try:
        alert_id = int(args[0])
    except ValueError:
        update.message.reply_text("⚠️ El ID debe ser un número entero.")
        return

    deactivate_alert(alert_id)
    update.message.reply_text(f"🗑️ Alerta {alert_id} desactivada.")
    log.info(
        "Alerta borrada",
        extra={"extra": {
            "command": "/alerta_borrar",
            "alert_id": alert_id,
            "chat_id": chat_id
        }}
    )

def config_divisa(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    args = context.args

    if len(args) != 1:
        update.message.reply_text(
            "⚠️ Uso: `/config_divisa <moneda>`\n"
            "Ejemplo: `/config_divisa eur`"
        )
        return

    moneda = args[0].lower()

    # 1) Comprobar que CoinGecko soporta esa moneda
    resp = get(f"https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies={moneda}")
    if resp.status_code != 200 or moneda not in resp.json().get("bitcoin", {}):
        update.message.reply_text(f"⚠️ La divisa '{moneda}' no es válida o no está soportada.")
        return

    # 2) Verificar que *no* sea el símbolo de otra cripto
    if resolve_coin(moneda) is not None:
        update.message.reply_text(
            f"⚠️ “{moneda.upper()}” es una criptomoneda. "
            "Para la divisa solo admite fiat: USD, EUR, ARS, MXN, etc."
        )
        return

    # 3) Guardar la preferencia
    set_currency(chat_id, moneda)
    update.message.reply_text(f"✅ Cambié tu divisa a *{moneda.upper()}*.", parse_mode=ParseMode.MARKDOWN)
    log.info(
        "Divisa configurada",
        extra={"extra": {
            "command": "/config_divisa",
            "currency": moneda,
            "chat_id": chat_id
        }}
    )

# ------------------------------------------------------------
# 5) JOB DE CHEQUEO PERIÓDICO DE ALERTAS
# ------------------------------------------------------------
def check_price_alerts():
    conn = get_conn()
    cursor = conn.cursor()  
    cursor.execute("""
        SELECT id, user_id, crypto_id, condition, threshold
        FROM price_alerts
        WHERE active = 1
    """)
    alerts = cursor.fetchall()
    cursor.close()
    conn.close()

    for alert_id, user_id, crypto_id, condition, threshold in alerts:
        moneda = get_currency(user_id)  # ej. "eur", "usd"
        url = (
            f"https://api.coingecko.com/api/v3/simple/price?"
            f"ids={crypto_id}&vs_currencies={moneda}"
        )
        resp = get(url)
        if resp.status_code != 200:
            continue

        data = resp.json()
        if crypto_id in data and moneda in data[crypto_id]:
            price = data[crypto_id][moneda]
        else:
            continue

        if (condition == "above" and price > threshold) or \
           (condition == "below" and price < threshold):
            price_str = format_price(price)
            bot.send_message(
                chat_id=user_id,
                text=(
                    f"⚠️ Alerta 🔔\n"
                    f"{crypto_id.upper()} está " +
                    ("arriba " if condition == "above" else "abajo ") +
                    f"{threshold:.2f} {moneda.upper()}.\n"
                    f"Precio actual: {price_str} {moneda.upper()}."
                )
            )
            deactivate_alert(alert_id)
            log.info(
                "Alerta disparada",
                extra={"extra": {
                    "alert_id": alert_id,
                    "crypto_id": crypto_id,
                    "condition": condition,
                    "threshold": threshold,
                    "price": price,
                    "currency": moneda,
                    "chat_id": user_id
                }}
            )
# -----------------------------
#  Funciones de BD para portafolio
# -----------------------------
def portfolio_add(user_id: int, crypto_id: str, cantidad: float, precio_compra: float):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO portfolio (user_id, crypto_id, cantidad, precio_compra, fecha)
        VALUES (%s, %s, %s, %s, %s)
    """, (user_id, crypto_id, cantidad, precio_compra, datetime.utcnow()))
    conn.commit()
    cursor.close()
    conn.close()

def portfolio_remove(user_id: int, crypto_id: str):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM portfolio
        WHERE user_id = %s AND crypto_id = %s
    """, (user_id, crypto_id))
    conn.commit()
    cursor.close()
    conn.close()

def portfolio_get_all(user_id: int):
    """
    Devuelve una lista de tuplas (crypto_id, cantidad, precio_compra, fecha)
    para todas las posiciones de user_id en PostgreSQL.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT crypto_id, cantidad, precio_compra, fecha
          FROM portfolio
         WHERE user_id = %s
        """,
        (user_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def register_variation_alert(user_id: int, crypto_id: str, base_price: float):
    """
    Inserta en variation_alerts una fila (user_id, crypto_id, base_price, porcentaje=5.0)
    *solo si* no existe ya una alerta activa para ese par. Por defecto se fija porcentaje=5.0.
    """
    conn = get_conn()
    cursor = conn.cursor()

    # Verificar si ya existe una alerta activa para ese user_id y crypto_id
    cursor.execute("""
        SELECT id FROM variation_alerts
        WHERE user_id = %s AND crypto_id = %s AND active = 1
    """, (user_id, crypto_id))
    fila = cursor.fetchone()

    if fila:
        # Ya existe alerta activa: no hacemos nada
        conn.close()
        return

    # Si no existe, insertamos una nueva con porcentaje=5.0 y active=1
    creado = datetime.utcnow()
    cursor.execute("""
        INSERT OR REPLACE INTO variation_alerts
        (user_id, crypto_id, base_price, porcentaje, active, created_at)
        VALUES (%s, %s, %s, %s, 1, %s)
    """, (user_id, crypto_id, base_price, 5.0, creado))

    conn.commit()
    cursor.close()
    conn.close()


def portafolio_handler(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    args = context.args  # lista de strings tras "/portafolio"

    if not args:
        update.message.reply_text(
            "⚠️ Uso de /portafolio:\n"
            "• /portafolio añadir <cripto> <cantidad>\n"
            "• /portafolio ver\n"
            "• /portafolio eliminar <cripto>"
        )
        log.info("Portafolio: ayuda mostrada", extra={"extra": {"command": "/portafolio", "chat_id": chat_id}})
        return

    acción = args[0].lower()

    # 1) AÑADIR posición
    if acción == "añadir":
        # /portafolio añadir <cripto> <cantidad>
        if len(args) != 3:
            update.message.reply_text(
                "⚠️ Uso: /portafolio añadir <cripto> <cantidad>\n"
                "Ejemplo: /portafolio añadir BTC 0.1"
            )
            log.warning("Portafolio: sintaxis añadir incorrecta", extra={"extra": {"args": args, "chat_id": chat_id}})
            return

        entrada = args[1]
        try:
            cantidad = float(args[2])
        except ValueError:
            update.message.reply_text("⚠️ La cantidad debe ser un número válido (ej: 0.1).")
            log.warning("Portafolio: cantidad inválida", extra={"extra": {"input": args[2], "chat_id": chat_id}})
            return

        coin_id = resolve_coin(entrada)
        if not coin_id:
            update.message.reply_text(f"⚠️ Criptomoneda '{entrada}' no reconocida.")
            log.warning("Portafolio: cripto no reconocida", extra={"extra": {"input": entrada, "chat_id": chat_id}})
            return

        # Obtener precio de mercado
        moneda = get_currency(chat_id)
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies={moneda}"
        resp = get(url, timeout=10)
        if resp.status_code != 200:
            update.message.reply_text("⚠️ No pude obtener el precio de mercado. Intenta más tarde.")
            log.error("Portafolio: fallo petición CoinGecko", extra={"extra": {"url": url, "status": resp.status_code}})
            return
        data = resp.json()
        price_raw = data.get(coin_id, {}).get(moneda)
        if price_raw is None:
            update.message.reply_text("⚠️ Error al leer el precio de mercado.")
            log.error("Portafolio: precio de mercado nulo", extra={"extra": {"data": data, "chat_id": chat_id}})
            return

        # Guardar posición y actualizar alerta de variación
        portfolio_add(chat_id, coin_id, cantidad, price_raw)
        register_portfolio_variation_alert(chat_id)

        price_str = format_price(price_raw)
        update.message.reply_text(
            f"✅ Añadido a tu portafolio: {cantidad} {coin_id.upper()} @ {price_str} {moneda.upper()}.",
            parse_mode=ParseMode.MARKDOWN
        )
        log.info("Portafolio: posición añadida", extra={"extra": {
            "user_id": chat_id, "crypto_id": coin_id,
            "cantidad": cantidad, "precio_compra": price_raw
        }})
        return
    
    # -----------------------------
# 2) VER portafolio (sin gráfico)
# -----------------------------
    if acción == "ver":
        filas = portfolio_get_all(chat_id)
        if not filas:
            update.message.reply_text("ℹ️ No tienes posiciones en tu portafolio.")
            log.info("Portafolio: ver sin posiciones", extra={"extra": {"chat_id": chat_id}})
            return

        moneda = get_currency(chat_id)
        coin_ids = [fila[0] for fila in filas]
        ids_param = ",".join(coin_ids)
        resp = get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={ids_param}&vs_currencies={moneda}",
            timeout=10
        )
        if resp.status_code != 200:
            update.message.reply_text("⚠️ No pude obtener los precios actuales. Intenta más tarde.")
            log.error("Portafolio: fallo petición CoinGecko ver", extra={"extra": {"status": resp.status_code}})
            return
        data = resp.json()

        lineas = ["📊 *Estado de tu portafolio:*"]
        total_invertido = total_actual = 0.0

        for crypto_id, cantidad, precio_compra, _ in filas:
            precio_actual = data.get(crypto_id, {}).get(moneda)
            if precio_actual is None:
                continue

            invertido = cantidad * precio_compra
            actual = cantidad * precio_actual
            diff = actual - invertido
            pct = (diff / invertido * 100) if invertido else 0

            total_invertido += invertido
            total_actual += actual

            # Elegimos emoji según signo
            emoji = "🟢" if diff >= 0 else "🔴"
            signo = "+" if diff >= 0 else "-"
            pct_str = f"{emoji}{signo}{pct:.2f}%"

            lineas.append(
                f"{crypto_id.upper()}: {cantidad}×{format_price(precio_compra)} = {format_price(invertido)} {moneda.upper()}\n"
                f"Valor actual: {format_price(actual)} {moneda.upper()} ({pct_str})"
            )

        # Totales
        diff_tot = total_actual - total_invertido
        pct_tot = (diff_tot / total_invertido * 100) if total_invertido else 0
        emoji_tot = "🟢" if diff_tot >= 0 else "🔴"
        signo_tot = "+" if diff_tot >= 0 else "-"
        pct_tot_str = f"{emoji_tot}{signo_tot}{pct_tot:.2f}%"

        lineas.append("-----------------------------------")
        lineas.append(
            f"*Total invertido:* {format_price(total_invertido)} {moneda.upper()}\n"
            f"*Valor actual:* {format_price(total_actual)} {moneda.upper()} ({pct_tot_str})"
        )

        update.message.reply_markdown("\n\n".join(lineas))
        log.info("Portafolio: estado mostrado", extra={"extra": {"chat_id": chat_id}})
        return



            # 3) ELIMINAR posición
    if acción == "eliminar":
        if len(args) != 2:
            update.message.reply_text("⚠️ Uso: /portafolio eliminar <cripto>\nEj: /portafolio eliminar ETH")
            log.warning("Portafolio: sintaxis eliminar incorrecta", extra={"extra": {"args": args, "chat_id": chat_id}})
            return

        entrada = args[1]
        coin_id = resolve_coin(entrada)
        if not coin_id:
            update.message.reply_text(f"⚠️ Criptomoneda '{entrada}' no reconocida.")
            log.warning("Portafolio: cripto no reconocida eliminar", extra={"extra": {"input": entrada, "chat_id": chat_id}})
            return

        portfolio_remove(chat_id, coin_id)
        register_portfolio_variation_alert(chat_id)
        update.message.reply_text(
             f"🗑️ He eliminado todas las posiciones de *{coin_id.upper()}* de tu portafolio.",
            parse_mode=ParseMode.MARKDOWN
        )
        log.info("Portafolio: posición eliminada", extra={"extra": {"user_id": chat_id, "crypto_id": coin_id}})
        return

    # Acción desconocida
    update.message.reply_text(
        "⚠️ Uso de /portafolio:\n"
        "• /portafolio añadir <cripto> <cantidad>\n"
        "• /portafolio ver\n"
        "• /portafolio eliminar <cripto>"
    )
    log.warning("Portafolio: acción desconocida", extra={"extra": {"args": args, "chat_id": chat_id}})


def help_handler(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    args = context.args  # lista con lo que viene tras /help

    if not args:
        texto = (
            "⚠️ Uso: `/help <sección>`\n"
            "Secciones disponibles:\n"
            "• `precio`   – Explica cómo consultar precios.\n"
            "• `avisos`   – Explica cómo crear y gestionar alertas.\n"
            "• `portafolio` – Explica cómo usar el portafolio.\n\n"
            "Ejemplo: `/help precio`."
        )
        update.message.reply_text(texto, parse_mode=ParseMode.MARKDOWN)
        return

    sección = args[0].lower()

    if sección == "precio":
        texto = (
            "*🔎 Consultar precio*\n\n"
            "Usa este comando para obtener el precio actual de cualquier criptomoneda:\n"
            "```\n"
            "/precio <cripto>\n"
            "```\n"
            "• `<cripto>` puede ser nombre o símbolo (por ejemplo: `bitcoin` o `BTC`, `ethereum` o `ETH`).\n"
            "• Ejemplo de uso:\n"
            "  • `/precio bitcoin`  → muestra el precio de Bitcoin.\n"
            "  • `/precio ETH`      → muestra el precio de Ethereum.\n"
            "• El precio se mostrará en la divisa que tengas configurada (USD, EUR, ARS, etc.).\n"
            "• Si quieres cambiar tu divisa predeterminada, ve a `/help avisos` y sigue las instrucciones de `/config_divisa`.\n"
        )
        update.message.reply_markdown(texto)
        return

    if sección == "avisos":
        texto = (
            "*🔔 Añadir o gestionar avisos*\n\n"
            "Con estos comandos puedes crear alertas de precio y verlas o borrarlas:\n\n"
            "1. Crear un nuevo aviso:\n"
            "```\n"
            "/alerta_crear <cripto> <arriba|abajo> <umbral>\n"
            "```\n"
            "• `<cripto>`: nombre o símbolo (ej: `ETH` o `bitcoin`).\n"
            "• `arriba` o `abajo`: condición para el umbral.\n"
            "• `<umbral>`: valor numérico (ej: `3000`).\n"
            "• Ejemplo: `/alerta_crear ETH arriba 3000` → te avisará cuando Ethereum supere 3 000.\n\n"
            "2. Listar tus avisos activos:\n"
            "```\n"
            "/alerta_listar\n"
            "```\n"
            "• Muestra todas las alertas que aún no han disparado.\n\n"
            "3. Borrar o desactivar un aviso:\n"
            "```\n"
            "/alerta_borrar <id>\n"
            "```\n"
            "• `<id>` es el número que ves al listar (`/alerta_listar`).\n"
            "• Ejemplo: `/alerta_borrar 2` desactiva la alerta con ID 2.\n\n"
            "4. Cambiar moneda de cotización para tus avisos y precios:\n"
            "```\n"
            "/config_divisa <moneda>\n"
            "```\n"
            "• `<moneda>` solo acepta divisas FIAT (USD, EUR, ARS, MXN, etc.).\n"
            "• Ejemplo: `/config_divisa eur` → a partir de ahora, todos los precios y avisos se calculan en EUR.\n"
        )
        update.message.reply_markdown(texto)
        return

    if sección == "portafolio":
        texto = (
            "*💼 Gestionar tu portafolio*\n\n"
            "El único comando es `/portafolio` más la acción deseada.\n\n"
            "1. Añadir una posición (usa el precio de mercado actual):\n"
            "```\n"
            "/portafolio añadir <cripto> <cantidad>\n"
            "```\n"
            "• `<cripto>`: nombre o símbolo (ej: `BTC`, `bitcoin`).\n"
            "• `<cantidad>`: cuántos tokens quieres añadir (ej: `0.1`).\n\n"
            "2. Ver el estado completo del portafolio:\n"
            "```\n"
            "/portafolio ver\n"
            "```\n"
            "• Muestra para cada cripto la inversión inicial, el valor actual y ganancia/pérdida.\n\n"
            "3. Eliminar todas tus posiciones de una cripto:\n"
            "```\n"
            "/portafolio eliminar <cripto>\n"
            "```\n"
            "• Ejemplo: `/portafolio eliminar ETH` → quita todas las filas de Ethereum en tu portafolio.\n"
        )
        update.message.reply_markdown(texto)
        return

    update.message.reply_text(
        "⚠️ Sección no válida. Usa:\n"
        "• `/help precio`\n"
        "• `/help avisos`\n"
        "• `/help portafolio`",
        parse_mode=ParseMode.MARKDOWN
    )

def check_portfolio_variation_alerts():
    """
    Recorre las alertas activas de portfolio_variation_alerts y:
      - Si la variación ≥ 10 %, envía mensaje URGENTE y desactiva.
      - Si la variación ≥ 5 % (pero < 10 %), envía mensaje estándar y desactiva.
    """
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, user_id, base_value, porcentaje
        FROM portfolio_variation_alerts
        WHERE active = 1
    """)
    filas = cursor.fetchall()
    cursor.close()
    conn.close()

    for alert_id, user_id, base_value, porcentaje in filas:
        valor_actual = get_portfolio_total_value(user_id)
        if valor_actual <= 0:
            continue

        try:
            cambio_pct = (valor_actual - base_value) / base_value * 100
        except Exception:
            continue

        abs_pct = abs(cambio_pct)
        moneda = get_currency(user_id).upper()
        direction = "subido" if cambio_pct > 0 else "bajado"

        # 1) Nivel URGENTE: ≥10 %
        if abs_pct >= 10.0:
            msg = (
                f"🚨 *ALERTA CRÍTICA*: tu portafolio ha {direction} un *{abs_pct:.2f}%* respecto al valor base!\n"
                f"• Valor base:     {format_price(base_value)} {moneda}\n"
                f"• Valor actual:   {format_price(valor_actual)} {moneda}\n"
                f"Revisa tu estrategia inmediatamente."
            )

        # 2) Nivel estándar: ≥5 % y <10 %
        elif abs_pct >= porcentaje:  # porcentaje sigue siendo 5.0
            msg = (
                f"⚠️ Alerta: tu portafolio ha {direction} {abs_pct:.2f}% "
                f"respecto al valor base.\n"
                f"• Valor base:   {format_price(base_value)} {moneda}\n"
                f"• Valor actual: {format_price(valor_actual)} {moneda}"
            )
        else:
            continue  # menos de 5 %, no hacemos nada

        # Enviar notificación
        try:
            bot.send_message(chat_id=user_id, text=msg, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

        # Desactivar la alerta (un solo disparo)
        conn2 = get_conn()
        cursor2 = conn.cursor()
        cursor2.execute("""
            UPDATE portfolio_variation_alerts
            SET active = 0
            WHERE id = %s
        """, (alert_id,))
        conn2.commit()
        cursor2.close()
        conn2.close()


def get_portfolio_total_value(user_id: int) -> float:
    """
    Devuelve el valor total actual del portafolio de user_id,
    en la divisa que tenga configurada (get_currency).
    Si no hay posiciones o falla, retorna 0.0.
    """
    # 1) Obtener todas las posiciones
    filas = portfolio_get_all(user_id)
    if not filas:
        return 0.0

    moneda = get_currency(user_id)  # ej. "usd", "eur"
    # Construir lista de coin_ids para consultar de golpe
    coin_ids = [fila[0] for fila in filas]
    ids_param = ",".join(coin_ids)
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids_param}&vs_currencies={moneda}"
    try:
        resp = get(url, timeout=10)
        if resp.status_code != 200:
            return 0.0
        data = resp.json()
    except Exception:
        return 0.0

    total = 0.0
    for crypto_id, cantidad, precio_compra, fecha in filas:
        precio_actual = data.get(crypto_id, {}).get(moneda)
        if precio_actual is None:
            continue
        total += cantidad * precio_actual


    return total
def register_portfolio_variation_alert(user_id: int):
    """
    Calcula el valor actual del portafolio y lo guarda como base_value
    en portfolio_variation_alerts. Si ya existía una alerta activa, la actualiza.
    Por defecto, porcentaje = 5.0, active = 1.
    """
    # 1) Calcular valor total actual del portafolio
    base_value = get_portfolio_total_value(user_id)
    if base_value <= 0:
        return  # Si no tiene portafolio o valor 0, no registramos

    ahora = datetime.utcnow()
    conn = get_conn()
    cursor = conn.cursor()

    # Insertar o actualizar (sobrescribir) la fila de ese user_id
    cursor.execute("""
        INSERT INTO portfolio_variation_alerts
        (user_id, base_value, porcentaje, active, created_at)
        VALUES (%s, %s, %s, 1, %s)
        ON CONFLICT(user_id) DO UPDATE
          SET base_value = excluded.base_value,
              porcentaje = excluded.porcentaje,
              active = excluded.active,
              created_at = excluded.created_at
    """, (user_id, base_value, 5.0, ahora))
    conn.commit()
    cursor.close()
    conn.close()


def check_variation_alerts():
    """
    Recorre todas las filas activas de variation_alerts.
    Para cada (user_id, crypto_id, base_price, porcentaje), obtiene el precio actual
    en la misma moneda del user y calcula la variación porcentual:
        cambio_pct = abs((precio_actual - base_price) / base_price * 100)
    Si cambio_pct >= porcentaje (5.0), envía notificación al usuario y marca la alerta como inactiva.
    """
    conn = get_conn()
    cursor = conn.cursor()
    # Seleccionamos solo las alertas activas
    cursor.execute("""
        SELECT id, user_id, crypto_id, base_price, porcentaje
        FROM variation_alerts
        WHERE active = 1
    """)
    filas = cursor.fetchall()
    cursor.close()
    conn.close()

    for alert_id, user_id, crypto_id, base_price, porcentaje in filas:
        # 1) Obtener la moneda del usuario (misma en la que guardamos base_price)
        moneda = get_currency(user_id)  # ej. "usd", "eur", etc.

        # 2) Preguntar a CoinGecko el precio actual de crypto_id en esa moneda
        url = (
            f"https://api.coingecko.com/api/v3/simple/price?"
            f"ids={crypto_id}&vs_currencies={moneda}"
        )
        try:
            resp = get(url, timeout=10)
            if resp.status_code != 200:
                continue  # saltar si falla la petición
            data = resp.json()
            precio_actual = data.get(crypto_id, {}).get(moneda)
            if precio_actual is None:
                continue  # si CoinGecko no devuelve precio, saltamos
        except Exception:
            continue  # en caso de timeout o error de red, saltamos

        # 3) Calcular variación porcentual absoluta
        try:
            cambio_pct = abs((precio_actual - base_price) / base_price * 100)
        except Exception:
            continue  # si base_price es 0 o algún otro error, saltamos

        # 4) Si la variación alcanza o supera el porcentaje (5.0), notificamos y desactivamos
        if cambio_pct >= porcentaje:
            # Empaquetamos mensaje
            direction = "subido" if precio_actual > base_price else "bajado"
            msg = (
                f"⚠️ Alerta automática: *{crypto_id.upper()}* ha {direction} "
                f"{cambio_pct:.2f}% respecto a {format_price(base_price)} {moneda.upper()}.\n"
                f"Precio base: {format_price(base_price)} {moneda.upper()}\n"
                f"Precio actual: {format_price(precio_actual)} {moneda.upper()}"
            )
            try:
                bot.send_message(chat_id=user_id, text=msg, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                # Si falla el envío (usuario bloqueó bot o similar), seguimos al siguiente
                pass

            # 5) Desactivar la alerta en la BD
            conn2 = get_conn()
            cursor2 = conn.cursor()
            cursor2.execute("""
                UPDATE variation_alerts
                SET active = 0
                WHERE id = %s
            """, (alert_id,))
            conn2.commit()
            cursor2.close()
            conn2.close()

app = Flask(__name__)
bot = Bot(token=TELEGRAM_TOKEN, request=TGRequest(con_pool_size=32))

dispatcher = Dispatcher(bot=bot, update_queue=None, workers=4, use_context=True)
# Registrar handlers en dispatcher
# e.g. dispatcher.add_handler(CommandHandler("start", start))

@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK"

if __name__ == "__main__":
    load_coin_mappings()
    init_db()
    # Registrar handlers
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("precio", precio))
    dispatcher.add_handler(CommandHandler("alerta_crear", alerta_crear))
    dispatcher.add_handler(CommandHandler("alerta_listar", alerta_listar))
    dispatcher.add_handler(CommandHandler("alerta_borrar", alerta_borrar))
    dispatcher.add_handler(CommandHandler("config_divisa", config_divisa))
    dispatcher.add_handler(CommandHandler("portafolio", portafolio_handler))

    # Scheduler for alerts\   
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(lambda: dispatcher.run_async(check_price_alerts), "interval", minutes=5)
    scheduler.add_job(lambda: dispatcher.run_async(check_variation_alerts), "interval", minutes=5)
    scheduler.add_job(lambda: dispatcher.run_async(check_portfolio_variation_alerts), "interval", minutes=5)
    scheduler.start()

    # Set webhook on Telegram side
    APP_URL = APP_URL.replace("https://", "").replace("http://", "")
    print(f"DEBUG: RENDER_EXTERNAL_URL = '{os.getenv('RENDER_EXTERNAL_URL')}'")
    webhook_url = f"https://{APP_URL}/{TELEGRAM_TOKEN}"
    logger.info(f"Preparando webhook en → {webhook_url}")
    bot.set_webhook(webhook_url)

    logger.info(f"Webhook establecido: {webhook_url}")

    # Start Flask server
    app.run(host="0.0.0.0", port=PORT)
