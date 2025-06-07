import sqlite3
import json
import os
from datetime import datetime

# Ruta del fichero config.json (aún no contiene “PRODUCTS” fijos).
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

config = load_config()
DB_PATH = config.get("DATABASE_PATH", "alerts.db")

def get_connection():
    """
    Abre una conexión a la base de datos SQLite.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """
    Crea (si no existen) las tablas necesarias:
      - suscriptores: lista de chat_id que usaron /start en el bot.
      - watchers: cada vigilancia dinámica (query genérica + precio objetivo).
      - history: historial de precios detectados para cada watcher.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # 1) Tabla de suscriptores (solo se conserva por si queremos listar o filtrar).
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS suscriptores (
        chat_id INTEGER PRIMARY KEY
    );
    """)

    # 2) Tabla de watchers: 
    #    id (auto), chat_id, query (texto libre), precio_objetivo (REAL)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS watchers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        query TEXT NOT NULL,
        precio_objetivo REAL NOT NULL,
        FOREIGN KEY (chat_id) REFERENCES suscriptores(chat_id)
    );
    """)

    # 3) Tabla de historial de precios: 
    #    id (auto), watcher_id, precio_encontrado, fecha (UTC ISO), 
    #    notificado (0/1)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        watcher_id INTEGER NOT NULL,
        precio_encontrado REAL NOT NULL,
        fecha TEXT NOT NULL,
        notificado INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (watcher_id) REFERENCES watchers(id)
    );
    """)

    conn.commit()
    conn.close()

def agregar_suscriptor(chat_id: int):
    """
    Inserta en suscriptores si no existe ya.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO suscriptores(chat_id) VALUES (?);",
        (chat_id,)
    )
    conn.commit()
    conn.close()

def eliminar_suscriptor(chat_id: int):
    """
    Elimina al usuario de suscriptores; opcionalmente podríamos 
    borrar todos sus watchers en cascada si lo deseamos.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM suscriptores WHERE chat_id = ?;", (chat_id,))
    # Si queremos limpiar también watchers:
    cursor.execute("DELETE FROM watchers WHERE chat_id = ?;", (chat_id,))
    conn.commit()
    conn.close()

def obtener_suscriptores() -> list[int]:
    """
    Devuelve lista de chat_id de todos los suscriptores.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id FROM suscriptores;")
    rows = cursor.fetchall()
    conn.close()
    return [row["chat_id"] for row in rows]

def agregar_watcher(chat_id: int, query: str, precio_objetivo: float) -> int:
    """
    Inserta una nueva vigilancia (watcher) en la tabla watchers.
    Devuelve el watcher_id (clave primaria) recién creado.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO watchers(chat_id, query, precio_objetivo) VALUES (?, ?, ?);",
        (chat_id, query, precio_objetivo)
    )
    watcher_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return watcher_id

def eliminar_watcher(watcher_id: int) -> None:
    """
    Borra un watcher por su ID (y, opcionalmente, todo su historial).
    """
    conn = get_connection()
    cursor = conn.cursor()
    # Borramos historial asociado
    cursor.execute("DELETE FROM history WHERE watcher_id = ?;", (watcher_id,))
    # Borramos el watcher en sí
    cursor.execute("DELETE FROM watchers WHERE id = ?;", (watcher_id,))
    conn.commit()
    conn.close()

def listar_watchers_de_usuario(chat_id: int) -> list[dict]:
    """
    Devuelve todos los watchers que ha creado el usuario (chat_id).
    Cada dict contiene: {'id', 'query', 'precio_objetivo'}.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, query, precio_objetivo FROM watchers WHERE chat_id = ?;",
        (chat_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def insertar_history(watcher_id: int, precio: float, notificado: int = 0) -> None:
    """
    Inserta un nuevo registro en history con el precio detectado y si 
    ya se notificó (0=pendiente, 1=enviado).
    """
    ahora = datetime.utcnow().isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO history(watcher_id, precio_encontrado, fecha, notificado) VALUES (?, ?, ?, ?);",
        (watcher_id, precio, ahora, notificado)
    )
    conn.commit()
    conn.close()

def obtener_ultimo_history(watcher_id: int) -> dict | None:
    """
    Recupera el registro más reciente de history para un watcher dado.
    Devuelve dict {'precio_encontrado', 'fecha', 'notificado'} o None.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT precio_encontrado, fecha, notificado 
        FROM history 
        WHERE watcher_id = ? 
        ORDER BY fecha DESC 
        LIMIT 1;
    """, (watcher_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def obtener_watchers_activos() -> list[dict]:
    """
    Devuelve TODOS los watchers (independientemente del usuario), con 
    sus datos: {'id', 'chat_id', 'query', 'precio_objetivo'}.
    Lo usará el scraper para recorrer todas las vigilancias.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, chat_id, query, precio_objetivo FROM watchers;")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]
