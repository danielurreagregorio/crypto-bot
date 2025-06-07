# test_es.py

import logging
import time
from datetime import datetime
from elasticsearch import Elasticsearch
from es_logger import ElasticsearchHandler

# 1. Instanciamos el cliente de Elasticsearch apuntando a localhost:9200
es_client = Elasticsearch(["http://localhost:9200"])

# 2. Creamos un logger con el mismo nombre que usa tu bot
logger = logging.getLogger("crypto_price_bot")
logger.setLevel(logging.INFO)

# 3. StreamHandler para ver por pantalla los mensajes de log
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
console_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

# 4. Instanciamos tu ElasticsearchHandler usando el parámetro es_client
es_handler = ElasticsearchHandler(
    es_client=es_client,
    index_prefix="telegram-bot-logs"
)
es_handler.setLevel(logging.INFO)
logger.addHandler(es_handler)

# 5. Emitimos logs de prueba
logger.info("Prueba de indexación en ES desde test_es.py", extra={"extra": {"bot_name": "test_bot"}})
time.sleep(1)
logger.warning("Prueba de warning en ES", extra={"extra": {"bot_name": "test_bot"}})
time.sleep(1)
logger.error("Prueba de error en ES", extra={"extra": {"bot_name": "test_bot"}})
