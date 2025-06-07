# scraper.py

import requests
import logging

logger = logging.getLogger("telegram_bot.scraper")

COINGECKO_API = "https://api.coingecko.com/api/v3/simple/price"

def obtener_precio_actual(crypto_id: str, vs_currency: str = "usd") -> float | None:
    """
    Consulta CoinGecko y devuelve el precio actual de crypto_id en vs_currency (ej. usd).
    Retorna un float o None si hay error.
    """
    params = {
        "ids": crypto_id.lower(),
        "vs_currencies": vs_currency.lower()
    }
    try:
        resp = requests.get(COINGECKO_API, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if crypto_id.lower() in data:
            return data[crypto_id.lower()][vs_currency.lower()]
        else:
            return None
    except Exception as e:
        logger.error(f"Error en CoinGecko API para {crypto_id}: {e}")
        return None
