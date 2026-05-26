import os
import psycopg2
import psycopg2.extras
from contextlib import contextmanager

DATABASE_URL = os.environ.get('DATABASE_URL', '')


@contextmanager
def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _cur(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def init_db():
    with get_db() as conn:
        with _cur(conn) as cur:
            cur.execute('''
                CREATE TABLE IF NOT EXISTS decks (
                    id         SERIAL PRIMARY KEY,
                    name       TEXT NOT NULL DEFAULT '新しいデッキ',
                    notes      TEXT DEFAULT '',
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cur.execute('''
                CREATE TABLE IF NOT EXISTS deck_cards (
                    deck_id    INTEGER NOT NULL,
                    card_id    TEXT NOT NULL,
                    card_name  TEXT NOT NULL,
                    quantity   INTEGER DEFAULT 1,
                    supertype  TEXT DEFAULT '',
                    subtypes   TEXT DEFAULT '',
                    types      TEXT DEFAULT '',
                    set_name   TEXT DEFAULT '',
                    image_small TEXT DEFAULT '',
                    PRIMARY KEY (deck_id, card_id),
                    FOREIGN KEY (deck_id) REFERENCES decks(id) ON DELETE CASCADE
                )
            ''')
            cur.execute('''
                CREATE TABLE IF NOT EXISTS quiz_questions (
                    id              SERIAL PRIMARY KEY,
                    title           TEXT NOT NULL DEFAULT '新しい問題',
                    scenario        TEXT NOT NULL DEFAULT '',
                    deck_remaining  INTEGER DEFAULT 20,
                    side_remaining  INTEGER DEFAULT 3,
                    win_card_name   TEXT DEFAULT '',
                    win_card_count  INTEGER DEFAULT 2,
                    explanation     TEXT DEFAULT '',
                    correct_choice  INTEGER DEFAULT 1,
                    created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    updated_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cur.execute('''
                CREATE TABLE IF NOT EXISTS quiz_choices (
                    id           SERIAL PRIMARY KEY,
                    question_id  INTEGER NOT NULL,
                    choice_num   INTEGER NOT NULL,
                    card_name    TEXT NOT NULL DEFAULT '',
                    draw_count   INTEGER DEFAULT 3,
                    is_search    INTEGER DEFAULT 0,
                    memo         TEXT DEFAULT '',
                    FOREIGN KEY (question_id) REFERENCES quiz_questions(id) ON DELETE CASCADE
                )
            ''')


# ---- Deck CRUD ----

def get_all_decks():
    with get_db() as conn:
        with _cur(conn) as cur:
            cur.execute('''
                SELECT d.*, COALESCE(SUM(dc.quantity), 0) AS card_count
                FROM decks d
                LEFT JOIN deck_cards dc ON d.id = dc.deck_id
                GROUP BY d.id
                ORDER BY d.updated_at DESC
            ''')
            return [dict(r) for r in cur.fetchall()]


def get_deck(deck_id):
    with get_db() as conn:
        with _cur(conn) as cur:
            cur.execute('SELECT * FROM decks WHERE id = %s', (deck_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def create_deck(name='新しいデッキ'):
    with get_db() as conn:
        with _cur(conn) as cur:
            cur.execute('INSERT INTO decks (name) VALUES (%s) RETURNING id', (name,))
            return cur.fetchone()['id']


def rename_deck(deck_id, name):
    with get_db() as conn:
        with _cur(conn) as cur:
            cur.execute(
                'UPDATE decks SET name = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s',
                (name.strip() or '新しいデッキ', deck_id)
            )


def delete_deck(deck_id):
    with get_db() as conn:
        with _cur(conn) as cur:
            cur.execute('DELETE FROM deck_cards WHERE deck_id = %s', (deck_id,))
            cur.execute('DELETE FROM decks WHERE id = %s', (deck_id,))


# ---- Card CRUD ----

def get_deck_cards(deck_id):
    with get_db() as conn:
        with _cur(conn) as cur:
            cur.execute('''
                SELECT * FROM deck_cards
                WHERE deck_id = %s
                ORDER BY
                    CASE supertype
                        WHEN 'Pokémon' THEN 1
                        WHEN 'Trainer' THEN 2
                        WHEN 'Energy'  THEN 3
                        ELSE 4
                    END,
                    card_name
            ''', (deck_id,))
            return [dict(r) for r in cur.fetchall()]


def get_deck_total(deck_id):
    with get_db() as conn:
        with _cur(conn) as cur:
            cur.execute(
                'SELECT COALESCE(SUM(quantity), 0) AS total FROM deck_cards WHERE deck_id = %s',
                (deck_id,)
            )
            return cur.fetchone()['total']


def add_or_increment_card(deck_id, card_data):
    with get_db() as conn:
        with _cur(conn) as cur:
            cur.execute(
                'SELECT quantity FROM deck_cards WHERE deck_id = %s AND card_id = %s',
                (deck_id, card_data['id'])
            )
            row = cur.fetchone()
            supertype = card_data.get('supertype', '')
            max_qty = 60 if supertype == 'Energy' else 4

            if row:
                new_qty = min(row['quantity'] + 1, max_qty)
                cur.execute(
                    'UPDATE deck_cards SET quantity = %s WHERE deck_id = %s AND card_id = %s',
                    (new_qty, deck_id, card_data['id'])
                )
            else:
                cur.execute('''
                    INSERT INTO deck_cards
                        (deck_id, card_id, card_name, supertype, subtypes, types, set_name, image_small)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ''', (
                    deck_id,
                    card_data['id'],
                    card_data['name'],
                    supertype,
                    ','.join(card_data.get('subtypes', [])),
                    ','.join(card_data.get('types', [])),
                    card_data.get('set_name', ''),
                    card_data.get('image_small', ''),
                ))
                new_qty = 1

            cur.execute(
                'UPDATE decks SET updated_at = CURRENT_TIMESTAMP WHERE id = %s',
                (deck_id,)
            )
            return new_qty


def update_quantity(deck_id, card_id, quantity):
    with get_db() as conn:
        with _cur(conn) as cur:
            if quantity <= 0:
                cur.execute(
                    'DELETE FROM deck_cards WHERE deck_id = %s AND card_id = %s',
                    (deck_id, card_id)
                )
            else:
                cur.execute(
                    'UPDATE deck_cards SET quantity = %s WHERE deck_id = %s AND card_id = %s',
                    (quantity, deck_id, card_id)
                )
            cur.execute(
                'UPDATE decks SET updated_at = CURRENT_TIMESTAMP WHERE id = %s',
                (deck_id,)
            )


def remove_card(deck_id, card_id):
    update_quantity(deck_id, card_id, 0)


# ---- Quiz CRUD ----

def get_all_questions():
    with get_db() as conn:
        with _cur(conn) as cur:
            cur.execute('''
                SELECT q.*, COUNT(c.id) AS choice_count
                FROM quiz_questions q
                LEFT JOIN quiz_choices c ON q.id = c.question_id
                GROUP BY q.id
                ORDER BY q.updated_at DESC
            ''')
            return [dict(r) for r in cur.fetchall()]


def get_question(question_id):
    with get_db() as conn:
        with _cur(conn) as cur:
            cur.execute('SELECT * FROM quiz_questions WHERE id = %s', (question_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def get_choices(question_id):
    with get_db() as conn:
        with _cur(conn) as cur:
            cur.execute(
                'SELECT * FROM quiz_choices WHERE question_id = %s ORDER BY choice_num',
                (question_id,)
            )
            return [dict(r) for r in cur.fetchall()]


def create_question(data):
    with get_db() as conn:
        with _cur(conn) as cur:
            cur.execute('''
                INSERT INTO quiz_questions
                    (title, scenario, deck_remaining, side_remaining,
                     win_card_name, win_card_count, explanation, correct_choice)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            ''', (
                data.get('title', '新しい問題'),
                data.get('scenario', ''),
                int(data.get('deck_remaining', 20)),
                int(data.get('side_remaining', 3)),
                data.get('win_card_name', ''),
                int(data.get('win_card_count', 1)),
                data.get('explanation', ''),
                int(data.get('correct_choice', 1)),
            ))
            qid = cur.fetchone()['id']
            _save_choices(cur, qid, data.get('choices', []))
            return qid


def update_question(question_id, data):
    with get_db() as conn:
        with _cur(conn) as cur:
            cur.execute('''
                UPDATE quiz_questions SET
                    title = %s, scenario = %s, deck_remaining = %s, side_remaining = %s,
                    win_card_name = %s, win_card_count = %s, explanation = %s,
                    correct_choice = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            ''', (
                data.get('title', '新しい問題'),
                data.get('scenario', ''),
                int(data.get('deck_remaining', 20)),
                int(data.get('side_remaining', 3)),
                data.get('win_card_name', ''),
                int(data.get('win_card_count', 1)),
                data.get('explanation', ''),
                int(data.get('correct_choice', 1)),
                question_id,
            ))
            cur.execute('DELETE FROM quiz_choices WHERE question_id = %s', (question_id,))
            _save_choices(cur, question_id, data.get('choices', []))


def delete_question(question_id):
    with get_db() as conn:
        with _cur(conn) as cur:
            cur.execute('DELETE FROM quiz_choices WHERE question_id = %s', (question_id,))
            cur.execute('DELETE FROM quiz_questions WHERE id = %s', (question_id,))


def _save_choices(cur, question_id, choices):
    for i, c in enumerate(choices, start=1):
        card_name = (c.get('card_name') or '').strip()
        if not card_name:
            continue
        cur.execute('''
            INSERT INTO quiz_choices (question_id, choice_num, card_name, draw_count, is_search, memo)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', (
            question_id, i,
            card_name,
            int(c.get('draw_count', 3)),
            1 if c.get('is_search') else 0,
            c.get('memo', ''),
        ))


def bulk_add_cards(deck_id, cards):
    with get_db() as conn:
        with _cur(conn) as cur:
            cur.execute('DELETE FROM deck_cards WHERE deck_id = %s', (deck_id,))
            for c in cards:
                cur.execute('''
                    INSERT INTO deck_cards
                        (deck_id, card_id, card_name, quantity, supertype, subtypes, types, set_name, image_small)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (
                    deck_id,
                    c.get('id', c['card_name']),
                    c['card_name'],
                    int(c.get('quantity', 1)),
                    c.get('supertype', ''),
                    c.get('subtypes', ''),
                    c.get('types', ''),
                    c.get('set_name', ''),
                    c.get('image_small', ''),
                ))
            cur.execute(
                'UPDATE decks SET updated_at = CURRENT_TIMESTAMP WHERE id = %s',
                (deck_id,)
            )
