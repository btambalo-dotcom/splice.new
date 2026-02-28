import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def find_candidate_dbs():
    candidates = []
    # .db na raiz
    for name in os.listdir(BASE_DIR):
        if name.lower().endswith(".db"):
            candidates.append(os.path.join(BASE_DIR, name))
    # .db dentro de instance/
    inst_dir = os.path.join(BASE_DIR, "instance")
    if os.path.isdir(inst_dir):
        for name in os.listdir(inst_dir):
            if name.lower().endswith(".db"):
                candidates.append(os.path.join(inst_dir, name))
    return candidates

def ensure_columns(db_path):
    print(f"Verificando banco: {db_path}")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    try:
        # ---- Tabela user (já existia nas versões anteriores) ----
        cur.execute("PRAGMA table_info('user')")
        rows = cur.fetchall()
        if not rows:
            print("  - Tabela 'user' não encontrada, pulando este arquivo.")
            return False
        cols = [r[1] for r in rows]

        def add_col_user(name, coltype):
            if name in cols:
                print(f"  ✔ Coluna já existe: user.{name}")
                return
            sql = f'ALTER TABLE "user" ADD COLUMN {name} {coltype};'
            print(f"  ➕ Adicionando coluna em user: {sql}")
            cur.execute(sql)

        add_col_user("is_admin", "INTEGER DEFAULT 0")
        add_col_user("splicer_name", "VARCHAR(120)")
        add_col_user("is_company_owner", "INTEGER DEFAULT 0")
        add_col_user("company_name", "VARCHAR(120)")

        # ---- NOVO: Tabela record (para mapa interativo) ----
        cur.execute("PRAGMA table_info('record')")
        rec_rows = cur.fetchall()
        if not rec_rows:
            print("  - Tabela 'record' não encontrada, seguindo mesmo assim.")
        else:
            rec_cols = [r[1] for r in rec_rows]

            def add_col_record(name, coltype):
                if name in rec_cols:
                    print(f"  ✔ Coluna já existe: record.{name}")
                    return
                sql = f'ALTER TABLE "record" ADD COLUMN {name} {coltype};'
                print(f"  ➕ Adicionando coluna em record: {sql}")
                cur.execute(sql)

            add_col_record("latitude", "FLOAT")
            add_col_record("longitude", "FLOAT")
            add_col_record("device_info", "VARCHAR(255)")
            add_col_record("section", "VARCHAR(120)")


        # ---- NOVO: Tabela company_map (cores de seção por mapa) ----
        try:
            cur.execute("PRAGMA table_info('company_map')")
            cmap_rows = cur.fetchall()
        except Exception:
            cmap_rows = []
        if cmap_rows:
            cmap_cols = [r[1] for r in cmap_rows]

            def add_col_company_map(name, coltype):
                if name in cmap_cols:
                    print(f"  ✔ Coluna já existe: company_map.{name}")
                    return
                sql = f'ALTER TABLE "company_map" ADD COLUMN {name} {coltype};'
                print(f"  ➕ Adicionando coluna em company_map: {sql}")
                cur.execute(sql)

            add_col_company_map("section_colors_json", "TEXT")

        conn.commit()
        print("  ✅ Atualização concluída neste banco.")
        return True
    finally:
        conn.close()
def main():
    print("=== Atualizador de bancos SQLite (tabela user) ===")
    dbs = find_candidate_dbs()
    if not dbs:
        print("Nenhum arquivo .db encontrado na pasta ou em instance/.")
        return

    any_updated = False
    for path in dbs:
        try:
            if ensure_columns(path):
                any_updated = True
        except Exception as e:
            print(f"  ⚠ Erro ao atualizar {path}: {e}")

    if not any_updated:
        print("Nenhum banco foi atualizado (ou nenhuma tabela 'user' encontrada).")
    else:
        print("=== Finalizado. Agora você pode rodar o sistema normalmente. ===")

if __name__ == "__main__":
    main()
