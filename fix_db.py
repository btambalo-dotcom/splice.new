import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = "data.db"  # se o seu banco tiver outro nome, renomeie o arquivo para data.db
DB_PATH = os.path.join(BASE_DIR, DB_NAME)

def ensure_column(cur, table, column, coltype):
    cur.execute(f"PRAGMA table_info('{table}')")
    cols = [row[1] for row in cur.fetchall()]
    if column in cols:
        print(f"✔ Coluna já existe: {table}.{column}")
        return
    sql = f'ALTER TABLE "{table}" ADD COLUMN {column} {coltype};'
    print(f"➕ Adicionando coluna: {sql}")
    cur.execute(sql)

def main():
    print(f"Usando banco: {DB_PATH}")
    if not os.path.exists(DB_PATH):
        print("⚠ Arquivo de banco de dados não encontrado. Verifique se o arquivo data.db está na mesma pasta deste script.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    ensure_column(cur, "user", "is_admin", "INTEGER DEFAULT 0")
    ensure_column(cur, "user", "splicer_name", "VARCHAR(120)")
    ensure_column(cur, "user", "is_company_owner", "INTEGER DEFAULT 0")
    ensure_column(cur, "user", "company_name", "VARCHAR(120)")

    conn.commit()
    conn.close()
    print("✅ Migração concluída com sucesso.")

if __name__ == "__main__":
    main()
