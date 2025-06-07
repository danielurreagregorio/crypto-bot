import sqlite3
import pandas as pd

conn = sqlite3.connect("alerts.db")
df = pd.read_sql_query("SELECT * FROM price_alerts;", conn)
conn.close()

print(df)
