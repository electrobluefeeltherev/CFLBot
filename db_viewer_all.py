# db_viewer_all.py
import sqlite3
from pathlib import Path
from tabulate import tabulate   # ← install with: pip install tabulate

DB_PATH = Path("stats.db")

def view_all_tables():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # Get all user tables
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = [row[0] for row in c.fetchall()]

        print(f"\nDatabase: {DB_PATH.absolute()}")
        print(f"Total tables: {len(tables)}\n")

        for table in tables:
            print(f"\n{'='*40}")
            print(f"TABLE: {table.upper()}")
            print(f"{'='*40}")

            # Columns
            c.execute(f"PRAGMA table_info({table})")
            columns = [col['name'] for col in c.fetchall()]

            # Data (limit to 50 rows)
            c.execute(f"SELECT * FROM {table} LIMIT 50")
            rows = [tuple(row) for row in c.fetchall()]

            if rows:
                print(tabulate(rows, headers=columns, tablefmt="github"))
            else:
                print("→ Empty table")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    view_all_tables()