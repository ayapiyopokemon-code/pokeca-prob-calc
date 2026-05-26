import os
from datetime import datetime, timezone
from supabase import create_client

SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')


def _sb():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def _now():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    pass  # Tables are created in Supabase dashboard


# ---- Deck CRUD ----

def get_all_decks():
    result = _sb().table('decks').select('*, deck_cards(quantity)').order('updated_at', desc=True).execute()
    decks = []
    for d in result.data:
        d['card_count'] = sum(c['quantity'] for c in (d.pop('deck_cards') or []))
        decks.append(d)
    return decks


def get_deck(deck_id):
    result = _sb().table('decks').select('*').eq('id', deck_id).execute()
    return result.data[0] if result.data else None


def create_deck(name='新しいデッキ'):
    result = _sb().table('decks').insert({'name': name}).execute()
    return result.data[0]['id']


def rename_deck(deck_id, name):
    _sb().table('decks').update({
        'name': name.strip() or '新しいデッキ',
        'updated_at': _now(),
    }).eq('id', deck_id).execute()


def delete_deck(deck_id):
    _sb().table('deck_cards').delete().eq('deck_id', deck_id).execute()
    _sb().table('decks').delete().eq('id', deck_id).execute()


# ---- Card CRUD ----

def get_deck_cards(deck_id):
    result = _sb().table('deck_cards').select('*').eq('deck_id', deck_id).execute()
    order = {'Pokémon': 1, 'Trainer': 2, 'Energy': 3}
    return sorted(result.data, key=lambda c: (order.get(c.get('supertype', ''), 4), c.get('card_name', '')))


def get_deck_total(deck_id):
    result = _sb().table('deck_cards').select('quantity').eq('deck_id', deck_id).execute()
    return sum(c['quantity'] for c in result.data)


def add_or_increment_card(deck_id, card_data):
    sb = _sb()
    existing = sb.table('deck_cards').select('quantity').eq('deck_id', deck_id).eq('card_id', card_data['id']).execute()
    supertype = card_data.get('supertype', '')
    max_qty = 60 if supertype == 'Energy' else 4

    if existing.data:
        new_qty = min(existing.data[0]['quantity'] + 1, max_qty)
        sb.table('deck_cards').update({'quantity': new_qty}).eq('deck_id', deck_id).eq('card_id', card_data['id']).execute()
    else:
        sb.table('deck_cards').insert({
            'deck_id': deck_id,
            'card_id': card_data['id'],
            'card_name': card_data['name'],
            'supertype': supertype,
            'subtypes': ','.join(card_data.get('subtypes', [])),
            'types': ','.join(card_data.get('types', [])),
            'set_name': card_data.get('set_name', ''),
            'image_small': card_data.get('image_small', ''),
        }).execute()
        new_qty = 1

    sb.table('decks').update({'updated_at': _now()}).eq('id', deck_id).execute()
    return new_qty


def update_quantity(deck_id, card_id, quantity):
    sb = _sb()
    if quantity <= 0:
        sb.table('deck_cards').delete().eq('deck_id', deck_id).eq('card_id', card_id).execute()
    else:
        sb.table('deck_cards').update({'quantity': quantity}).eq('deck_id', deck_id).eq('card_id', card_id).execute()
    sb.table('decks').update({'updated_at': _now()}).eq('id', deck_id).execute()


def remove_card(deck_id, card_id):
    update_quantity(deck_id, card_id, 0)


# ---- Quiz CRUD ----

def get_all_questions():
    result = _sb().table('quiz_questions').select('*, quiz_choices(id)').order('updated_at', desc=True).execute()
    questions = []
    for q in result.data:
        q['choice_count'] = len(q.pop('quiz_choices') or [])
        questions.append(q)
    return questions


def get_question(question_id):
    result = _sb().table('quiz_questions').select('*').eq('id', question_id).execute()
    return result.data[0] if result.data else None


def get_choices(question_id):
    result = _sb().table('quiz_choices').select('*').eq('question_id', question_id).order('choice_num').execute()
    return result.data


def create_question(data):
    sb = _sb()
    result = sb.table('quiz_questions').insert({
        'title': data.get('title', '新しい問題'),
        'scenario': data.get('scenario', ''),
        'deck_remaining': int(data.get('deck_remaining', 20)),
        'side_remaining': int(data.get('side_remaining', 3)),
        'win_card_name': data.get('win_card_name', ''),
        'win_card_count': int(data.get('win_card_count', 1)),
        'explanation': data.get('explanation', ''),
        'correct_choice': int(data.get('correct_choice', 1)),
    }).execute()
    qid = result.data[0]['id']
    _save_choices(sb, qid, data.get('choices', []))
    return qid


def update_question(question_id, data):
    sb = _sb()
    sb.table('quiz_questions').update({
        'title': data.get('title', '新しい問題'),
        'scenario': data.get('scenario', ''),
        'deck_remaining': int(data.get('deck_remaining', 20)),
        'side_remaining': int(data.get('side_remaining', 3)),
        'win_card_name': data.get('win_card_name', ''),
        'win_card_count': int(data.get('win_card_count', 1)),
        'explanation': data.get('explanation', ''),
        'correct_choice': int(data.get('correct_choice', 1)),
        'updated_at': _now(),
    }).eq('id', question_id).execute()
    sb.table('quiz_choices').delete().eq('question_id', question_id).execute()
    _save_choices(sb, question_id, data.get('choices', []))


def delete_question(question_id):
    sb = _sb()
    sb.table('quiz_choices').delete().eq('question_id', question_id).execute()
    sb.table('quiz_questions').delete().eq('id', question_id).execute()


def _save_choices(sb, question_id, choices):
    rows = []
    for i, c in enumerate(choices, start=1):
        card_name = (c.get('card_name') or '').strip()
        if not card_name:
            continue
        rows.append({
            'question_id': question_id,
            'choice_num': i,
            'card_name': card_name,
            'draw_count': int(c.get('draw_count', 3)),
            'is_search': 1 if c.get('is_search') else 0,
            'memo': c.get('memo', ''),
        })
    if rows:
        sb.table('quiz_choices').insert(rows).execute()


def bulk_add_cards(deck_id, cards):
    sb = _sb()
    sb.table('deck_cards').delete().eq('deck_id', deck_id).execute()
    if cards:
        rows = [{
            'deck_id': deck_id,
            'card_id': c.get('id', c['card_name']),
            'card_name': c['card_name'],
            'quantity': int(c.get('quantity', 1)),
            'supertype': c.get('supertype', ''),
            'subtypes': c.get('subtypes', ''),
            'types': c.get('types', ''),
            'set_name': c.get('set_name', ''),
            'image_small': c.get('image_small', ''),
        } for c in cards]
        sb.table('deck_cards').insert(rows).execute()
    sb.table('decks').update({'updated_at': _now()}).eq('id', deck_id).execute()
