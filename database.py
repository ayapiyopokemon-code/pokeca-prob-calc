import sqlite3
import os
from contextlib import contextmanager

_default_db = os.path.join(os.path.dirname(__file__), 'calc.db')
DB_PATH = os.environ.get('DB_PATH', _default_db)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS decks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL DEFAULT '新しいデッキ',
                notes      TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

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
            );

            CREATE TABLE IF NOT EXISTS quiz_questions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                title           TEXT NOT NULL DEFAULT '新しい問題',
                scenario        TEXT NOT NULL DEFAULT '',
                deck_remaining  INTEGER DEFAULT 20,
                side_remaining  INTEGER DEFAULT 3,
                win_card_name   TEXT DEFAULT '',
                win_card_count  INTEGER DEFAULT 2,
                explanation     TEXT DEFAULT '',
                correct_choice  INTEGER DEFAULT 1,
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS quiz_choices (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id  INTEGER NOT NULL,
                choice_num   INTEGER NOT NULL,
                card_name    TEXT NOT NULL DEFAULT '',
                draw_count   INTEGER DEFAULT 3,
                is_search    INTEGER DEFAULT 0,
                memo         TEXT DEFAULT '',
                FOREIGN KEY (question_id) REFERENCES quiz_questions(id) ON DELETE CASCADE
            );
        ''')


# ---- Deck CRUD ----

def get_all_decks():
    with get_db() as conn:
        rows = conn.execute('''
            SELECT d.*, COALESCE(SUM(dc.quantity), 0) AS card_count
            FROM decks d
            LEFT JOIN deck_cards dc ON d.id = dc.deck_id
            GROUP BY d.id
            ORDER BY d.updated_at DESC
        ''').fetchall()
        return [dict(r) for r in rows]


def get_deck(deck_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM decks WHERE id = ?', (deck_id,)).fetchone()
        return dict(row) if row else None


def create_deck(name='新しいデッキ'):
    with get_db() as conn:
        cur = conn.execute('INSERT INTO decks (name) VALUES (?)', (name,))
        return cur.lastrowid


def rename_deck(deck_id, name):
    with get_db() as conn:
        conn.execute(
            'UPDATE decks SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            (name.strip() or '新しいデッキ', deck_id)
        )


def delete_deck(deck_id):
    with get_db() as conn:
        conn.execute('DELETE FROM deck_cards WHERE deck_id = ?', (deck_id,))
        conn.execute('DELETE FROM decks WHERE id = ?', (deck_id,))


# ---- Card CRUD ----

def get_deck_cards(deck_id):
    with get_db() as conn:
        rows = conn.execute('''
            SELECT * FROM deck_cards
            WHERE deck_id = ?
            ORDER BY
                CASE supertype
                    WHEN 'Pokémon' THEN 1
                    WHEN 'Trainer' THEN 2
                    WHEN 'Energy'  THEN 3
                    ELSE 4
                END,
                card_name
        ''', (deck_id,)).fetchall()
        return [dict(r) for r in rows]


def get_deck_total(deck_id):
    with get_db() as conn:
        row = conn.execute(
            'SELECT COALESCE(SUM(quantity), 0) AS total FROM deck_cards WHERE deck_id = ?',
            (deck_id,)
        ).fetchone()
        return row['total']


def add_or_increment_card(deck_id, card_data):
    """カードを追加。すでにあれば枚数+1。4枚制限あり（エネルギーは制限なし）。"""
    with get_db() as conn:
        row = conn.execute(
            'SELECT quantity FROM deck_cards WHERE deck_id = ? AND card_id = ?',
            (deck_id, card_data['id'])
        ).fetchone()

        supertype = card_data.get('supertype', '')
        max_qty = 60 if supertype == 'Energy' else 4

        if row:
            new_qty = min(row['quantity'] + 1, max_qty)
            conn.execute(
                'UPDATE deck_cards SET quantity = ? WHERE deck_id = ? AND card_id = ?',
                (new_qty, deck_id, card_data['id'])
            )
        else:
            conn.execute('''
                INSERT INTO deck_cards
                    (deck_id, card_id, card_name, supertype, subtypes, types, set_name, image_small)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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

        conn.execute(
            'UPDATE decks SET updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            (deck_id,)
        )
        return new_qty


def update_quantity(deck_id, card_id, quantity):
    with get_db() as conn:
        if quantity <= 0:
            conn.execute(
                'DELETE FROM deck_cards WHERE deck_id = ? AND card_id = ?',
                (deck_id, card_id)
            )
        else:
            conn.execute(
                'UPDATE deck_cards SET quantity = ? WHERE deck_id = ? AND card_id = ?',
                (quantity, deck_id, card_id)
            )
        conn.execute(
            'UPDATE decks SET updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            (deck_id,)
        )


def remove_card(deck_id, card_id):
    update_quantity(deck_id, card_id, 0)


# ---- Quiz CRUD ----

def get_all_questions():
    with get_db() as conn:
        rows = conn.execute('''
            SELECT q.*, COUNT(c.id) AS choice_count
            FROM quiz_questions q
            LEFT JOIN quiz_choices c ON q.id = c.question_id
            GROUP BY q.id
            ORDER BY q.updated_at DESC
        ''').fetchall()
        return [dict(r) for r in rows]


def get_question(question_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM quiz_questions WHERE id = ?', (question_id,)).fetchone()
        return dict(row) if row else None


def get_choices(question_id):
    with get_db() as conn:
        rows = conn.execute(
            'SELECT * FROM quiz_choices WHERE question_id = ? ORDER BY choice_num',
            (question_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def create_question(data):
    with get_db() as conn:
        cur = conn.execute('''
            INSERT INTO quiz_questions
                (title, scenario, deck_remaining, side_remaining,
                 win_card_name, win_card_count, explanation, correct_choice)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
        qid = cur.lastrowid
        _save_choices(conn, qid, data.get('choices', []))
        return qid


def update_question(question_id, data):
    with get_db() as conn:
        conn.execute('''
            UPDATE quiz_questions SET
                title = ?, scenario = ?, deck_remaining = ?, side_remaining = ?,
                win_card_name = ?, win_card_count = ?, explanation = ?,
                correct_choice = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
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
        conn.execute('DELETE FROM quiz_choices WHERE question_id = ?', (question_id,))
        _save_choices(conn, question_id, data.get('choices', []))


def delete_question(question_id):
    with get_db() as conn:
        conn.execute('DELETE FROM quiz_choices WHERE question_id = ?', (question_id,))
        conn.execute('DELETE FROM quiz_questions WHERE id = ?', (question_id,))


def _save_choices(conn, question_id, choices):
    for i, c in enumerate(choices, start=1):
        card_name = (c.get('card_name') or '').strip()
        if not card_name:
            continue
        conn.execute('''
            INSERT INTO quiz_choices (question_id, choice_num, card_name, draw_count, is_search, memo)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            question_id, i,
            card_name,
            int(c.get('draw_count', 3)),
            1 if c.get('is_search') else 0,
            c.get('memo', ''),
        ))


def bulk_add_cards(deck_id, cards):
    """カードのリストを一括追加（インポート用）。既存デッキはクリアして上書き。"""
    with get_db() as conn:
        conn.execute('DELETE FROM deck_cards WHERE deck_id = ?', (deck_id,))
        for c in cards:
            conn.execute('''
                INSERT INTO deck_cards
                    (deck_id, card_id, card_name, quantity, supertype, subtypes, types, set_name, image_small)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        conn.execute(
            'UPDATE decks SET updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            (deck_id,)
        )
