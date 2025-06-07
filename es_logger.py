# es_logger.py (versión final, sin prints de depuración)
import logging
from datetime import datetime
from elasticsearch import Elasticsearch

class ElasticsearchHandler(logging.Handler):
    def __init__(self, es_client, index_prefix: str = "telegram-bot-logs"):
        super().__init__()
        self.es = es_client
        self.index_prefix = index_prefix

    def emit(self, record: logging.LogRecord):
        try:
            log_record = {
                "@timestamp": datetime.utcnow().isoformat(),
                "level": record.levelname,
                "bot_name": record.__dict__.get("bot_name", "unknown_bot"),
                "module": record.module,
                "funcName": record.funcName,
                "lineno": record.lineno,
                "message": record.getMessage(),
            }
            if record.__dict__.get("extra"):
                log_record.update(record.__dict__["extra"])

            index_name = f"{self.index_prefix}-{datetime.utcnow().strftime('%Y.%m.%d')}"
            self.es.index(index=index_name, document=log_record)

        except Exception:
            # En producción puedes seguir silencian­do o
            # registrar en consola: print(f"Falló indexación ES: {e}")
            pass
