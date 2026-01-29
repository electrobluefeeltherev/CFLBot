from jinja2 import Environment, FileSystemLoader
import sqlite3

DB_PATH = "stats.db"

env = Environment(loader=FileSystemLoader("templates"))
template = env.get_template("card.html")

def render_player_card(user_id: str):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()

        c.execute("""
            SELECT 
                p.player_name,
                s.total_runs,
                s.batting_innings,
                s.highest_score,
                s.six,
                s.four,
                s.wickets_taken
            FROM players p
            JOIN stats s ON p.user_id = s.user_id
            WHERE p.user_id = ?
        """, (user_id,))

        row = c.fetchone()
        if not row:
            return None

    name, runs, inn, hs, six, four, wkt = row

    html = template.render(
        name=name.upper(),
        inn=inn,
        run=runs,
        wkt=wkt,
        hs=hs,
        six=six,
        four=four
    )

    output_path = f"cards/{user_id}.html"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return output_path

if __name__ == "__main__":
    test_user_id = "960634184597131295"  # replace with a real user_id from DB

    html_path = render_player_card(test_user_id)
    print("HTML generated at:", html_path)

