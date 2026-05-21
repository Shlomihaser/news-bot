import os
import sqlite3

# DB lives in <repo>/data/sent_links.db. On GitHub Actions GITHUB_WORKSPACE
# points to the repo root; locally we fall back to the project root computed
# from this file's location.
_PROJECT_ROOT = os.getenv(
    "GITHUB_WORKSPACE",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
DB_FILE = os.path.join(_PROJECT_ROOT, "data", "sent_links.db")

def initialize_db():
    """
    Initializes the SQLite database and creates the 'sent' table if it doesn't exist.
    """
    # Ensure the directory for the database file exists
    db_dir = os.path.dirname(DB_FILE)
    if not os.path.exists(db_dir):
        os.makedirs(db_dir)
        print(f"Created directory for DB: {db_dir}")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sent (
            link TEXT PRIMARY KEY
        )
    """)
    conn.commit()
    conn.close()
    print(f"Database '{DB_FILE}' initialized and 'sent' table ensured.")

# load_sent_links and save_sent_link functions remain the same
def load_sent_links():
    """
    Loads all previously sent links from the database.
    Returns them as a set for efficient lookup.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT link FROM sent")
    links = {row[0] for row in cursor.fetchall()}
    conn.close()
    print(f"Loaded {len(links)} previously sent links from DB.")
    return links

def save_sent_link(link):
    """
    Saves a new link to the database. Ignores if the link already exists.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO sent (link) VALUES (?)", (link,))
        conn.commit()
    except sqlite3.IntegrityError:
        # This means the link (PRIMARY KEY) already exists, so we ignore.
        pass
    finally:
        conn.close()
