import os
import psycopg2
import psycopg2.extras
from contextlib import contextmanager

DATABASE_URL = os.environ.get('DATABASE_URL', '')

def get_conn():
    url = DATABASE_URL
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    return psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)

@contextmanager
def db():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS countries (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            language TEXT NOT NULL DEFAULT 'en',
            telegram_channel TEXT DEFAULT '',
            map_code TEXT DEFAULT '',
            active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS sources (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL DEFAULT '',
            rss_url TEXT NOT NULL DEFAULT '',
            country_id INTEGER REFERENCES countries(id) ON DELETE SET NULL,
            category TEXT DEFAULT 'General',
            enabled BOOLEAN DEFAULT TRUE,
            interval_min INTEGER DEFAULT 30,
            max_news INTEGER DEFAULT 10,
            parser_type TEXT DEFAULT 'rss',
            css_item TEXT DEFAULT '',
            css_title TEXT DEFAULT '',
            css_link TEXT DEFAULT '',
            base_url TEXT DEFAULT '',
            last_parsed_at TIMESTAMPTZ DEFAULT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS telegram_channels (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            bot_token TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            country_id INTEGER REFERENCES countries(id) ON DELETE SET NULL,
            status TEXT DEFAULT 'unknown',
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS news (
            id SERIAL PRIMARY KEY,
            source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
            original_title TEXT NOT NULL,
            original_url TEXT NOT NULL,
            generated_title TEXT DEFAULT '',
            generated_text TEXT DEFAULT '',
            image_url TEXT DEFAULT '',
            status TEXT DEFAULT 'new',
            published_at TIMESTAMPTZ,
            parsed_at TIMESTAMPTZ DEFAULT NOW(),
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """)

        # Migrate: add new columns if they don't exist yet
        migrations = [
            "ALTER TABLE sources ADD COLUMN IF NOT EXISTS parser_type TEXT DEFAULT 'rss'",
            "ALTER TABLE sources ADD COLUMN IF NOT EXISTS css_item TEXT DEFAULT ''",
            "ALTER TABLE sources ADD COLUMN IF NOT EXISTS css_title TEXT DEFAULT ''",
            "ALTER TABLE sources ADD COLUMN IF NOT EXISTS css_link TEXT DEFAULT ''",
            "ALTER TABLE sources ADD COLUMN IF NOT EXISTS base_url TEXT DEFAULT ''",
            "ALTER TABLE sources ADD COLUMN IF NOT EXISTS last_parsed_at TIMESTAMPTZ DEFAULT NULL",
        ]
        for sql in migrations:
            try:
                cur.execute(sql)
            except Exception:
                pass

        defaults = [
            ('chatgpt_api_key', ''),
            ('chatgpt_model', 'gpt-4o'),
            ('chatgpt_temperature', '0.7'),
            ('chatgpt_max_tokens', '1000'),
            ('prompt', 'Пиши в стиле BBC News.\nПереписывай текст полностью, а не переводи дословно.\nИспользуй нейтральный журналистский стиль.\nНе добавляй фактов, которых нет в оригинале.\nЗаголовок должен быть коротким и цепляющим.\nТекст должен хорошо читаться в Telegram.'),
            ('auto_parse', 'false'),
            ('auto_chatgpt', 'false'),
            ('auto_draft', 'false'),
            ('dedup_by_url', 'true'),
            ('dedup_by_title', 'true'),
        ]
        for key, val in defaults:
            cur.execute("""
                INSERT INTO settings (key, value)
                VALUES (%s, %s)
                ON CONFLICT (key) DO NOTHING
            """, (key, val))


def get_setting(key, default=''):
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key = %s", (key,))
        row = cur.fetchone()
        return row['value'] if row else default


def set_setting(key, value):
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO settings (key, value, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """, (key, str(value)))


def get_all_settings():
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM settings")
        rows = cur.fetchall()
        return {r['key']: r['value'] for r in rows}


def save_parsed_articles(source_id: int, items: list, dedup_url: bool = True, dedup_title: bool = False) -> int:
    """Save parsed articles to DB, return count of newly saved."""
    if not items:
        return 0
    saved = 0
    with db() as conn:
        cur = conn.cursor()
        for item in items:
            url = item.get('link', '')
            title = item.get('title', '')
            if not url or not title:
                continue
            if dedup_url and url:
                cur.execute("SELECT id FROM news WHERE original_url=%s", (url,))
                if cur.fetchone():
                    continue
            if dedup_title and title:
                cur.execute("SELECT id FROM news WHERE original_title=%s", (title,))
                if cur.fetchone():
                    continue
            cur.execute("""INSERT INTO news (source_id, original_title, original_url, image_url, status)
                VALUES (%s, %s, %s, %s, 'new')""",
                (source_id, title, url, item.get('image', '') or ''))
            saved += 1
        if saved > 0:
            cur.execute("UPDATE sources SET last_parsed_at=NOW() WHERE id=%s", (source_id,))
    return saved
