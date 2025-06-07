# log_formatter.py

import logging
import json

class JSONFormatter(logging.Formatter):
    """
    Formatea cada registro de log como un JSON con campos:
      timestamp, level, bot_name, module, funcName, lineno, message
    """
    def format(self, record):
        log_record = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "bot_name": record.__dict__.get("bot_name", "unknown_bot"),
            "module": record.module,
            "funcName": record.funcName,
            "lineno": record.lineno,
            "message": record.getMessage(),
        }
        if record.__dict__.get("extra"):
            log_record.update(record.__dict__["extra"])
        return json.dumps(log_record)
