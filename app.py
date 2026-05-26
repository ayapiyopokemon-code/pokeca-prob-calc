import re
import json as _json
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote
from flask import Flask, render_template, request, jsonify, redirect, url_for, Response

import database as db
import probability as prob

app = Flask(__name__)
app.secret_key = 'pokeca_prob_2024'

PTCG_API      = 'https://api.pokemontcg.io/v2'
TCGDEX_JA     = 'https://api.tcgdex.net/v2/ja'
OFFICIAL_BASE = 'https://www.pokemon-card.com'
LIMITLESS_BASE = 'https://limitlesstcg.nyc3.cdn.digitaloceanspaces.com/tpc'

_PTCG_HEADERS = {'User-Agent': 'PokecaProbCalc/1.0'}
_PC_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Referer': 'https://www.pokemon-card.com/card-search/',
    'X-Requested-With': 'XMLHttpRequest',
    'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
}

_TCGDEX_CATEGORY = {
    'Pokemon': 'Pokémon', 'Trainer': 'Trainer', 'Energy': 'Energy',
}

TYPE_JP = {
    'Grass': '草', 'Fire': '炎', 'Water': '水', 'Lightning': '雷',
    'Psychic': '超', 'Fighting': '闘', 'Darkness': '悪', 'Metal': '鋼',
    'Colorless': '無色', 'Dragon': 'ドラゴン', 'Fairy': 'フェアリー',
}
SUPERTYPE_JP = {
    'Pokémon': 'ポケモン', 'Trainer': 'トレーナーズ', 'Energy': 'エネルギー',
}
TYPE_COLORS = {
    'Grass': '#5DBD58', 'Fire': '#F08030', 'Water': '#6890F0',
    'Lightning': '#F8C030', 'Psychic': '#F85888', 'Fighting': '#C03028',
    'Darkness': '#705848', 'Metal': '#B8B8D0', 'Colorless': '#A8A878',
    'Dragon': '#7038F8', 'Fairy': '#EE99AC',
}


# ---- Pages ----

@app.route('/')
def index():
    decks = db.get_all_decks()
    return render_template('index.html', decks=decks)


@app.route('/deck/new', methods=['POST'])
def new_deck():
    deck_id = db.create_deck()
    return redirect(url_for('deck_builder', deck_id=deck_id))


@app.route('/deck/<int:deck_id>/edit')
def deck_builder(deck_id):
    deck = db.get_deck(deck_id)
    if not deck:
        return redirect(url_for('index'))
    cards = db.get_deck_cards(deck_id)
    total = sum(c['quantity'] for c in cards)
    return render_template(
        'deck_builder.html',
        deck=deck, cards=cards, total=total,
        supertype_jp=SUPERTYPE_JP, type_jp=TYPE_JP, type_colors=TYPE_COLORS,
    )


@app.route('/deck/<int:deck_id>/calc')
def calculator(deck_id):
    deck = db.get_deck(deck_id)
    if not deck:
        return redirect(url_for('index'))
    cards = db.get_deck_cards(deck_id)
    total = sum(c['quantity'] for c in cards)
    return render_template(
        'calc.html',
        deck=deck, cards=cards, total=total,
        supertype_jp=SUPERTYPE_JP, type_jp=TYPE_JP, type_colors=TYPE_COLORS,
    )


# ---- API: 画像プロキシ（公式サイト画像の CORS 回避用） ----

@app.route('/proxy-image')
def proxy_image():
    url = request.args.get('url', '')
    if not url.startswith(('http://', 'https://')):
        return '', 400
    try:
        resp = requests.get(url, headers=_PC_HEADERS, timeout=10)
        return Response(
            resp.content,
            status=resp.status_code,
            content_type=resp.headers.get('Content-Type', 'image/jpeg'),
        )
    except Exception:
        return '', 404


# ---- API: カード名の予測変換 ----

@app.route('/api/suggest')
def api_suggest():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])
    try:
        r = requests.get(f'{TCGDEX_JA}/cards', params={'name': q}, timeout=6)
        if r.status_code != 200:
            return jsonify([])
        cards = r.json()
        if not isinstance(cards, list):
            return jsonify([])

        seen: set[str] = set()
        names = []
        for c in cards:
            name = c.get('name', '')
            if name and name not in seen:
                seen.add(name)
                names.append(name)
                if len(names) >= 10:
                    break
        return jsonify(names)
    except Exception:
        return jsonify([])


# ---- API: Card search (日本語) ----

@app.route('/api/search')
def api_search():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])

    # TCGdex と公式サイトを並列で取得
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_tcgdex   = ex.submit(_search_tcgdex_ja, q)
        f_official = ex.submit(_search_official_ja, q)
        tcgdex_cards   = f_tcgdex.result()
        official_cards = f_official.result()

    if not tcgdex_cards:
        # TCGdex に結果なし → 公式サイトのみ
        return jsonify(official_cards)

    # 画像が欠けているカードを公式サイト画像で補完
    # 公式カードを「名前 → リスト」でインデックス化
    off_idx: dict[str, list] = {}
    for c in official_cards:
        off_idx.setdefault(c['name'], []).append(c)

    merged = []
    used_off_ids: set[str] = set()

    for card in tcgdex_cards:
        if not card['image_small'] and card['name'] in off_idx:
            # TCGdex に画像なし → 同名の公式カードを1枚ずつ展開
            # supertype も公式側から取る（TCGdex リストは category を返さないため）
            for off_card in off_idx[card['name']]:
                if off_card['id'] in used_off_ids:
                    continue
                used_off_ids.add(off_card['id'])
                merged.append({**card,
                    'id':          off_card['id'],
                    'set_name':    off_card['set_name'] or card['set_name'],
                    'supertype':   off_card['supertype'],
                    'image_small': off_card['image_small'],
                    'image_large': off_card['image_large'],
                })
        else:
            merged.append(card)

    # 公式サイトにしかない追加カード（TCGdex未登録の最新セット等）
    tcgdex_names = {c['name'] for c in tcgdex_cards}
    for c in official_cards:
        if c['name'] not in tcgdex_names and c['id'] not in used_off_ids:
            merged.append(c)
            used_off_ids.add(c['id'])

    # 画像のないカードは除外（古いセットで画像データが存在しない場合）
    merged = [c for c in merged if c.get('image_small')]
    return jsonify(merged[:24])


def _search_tcgdex_ja(query):
    """TCGdex 日本語APIでカードを検索（category は詳細エンドポイントを並列取得）"""
    try:
        r = requests.get(f'{TCGDEX_JA}/cards', params={'name': query}, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        if not isinstance(data, list):
            return []
        data = data[:24]
    except Exception:
        return []

    def _fetch_one(c):
        card_id = c.get('id', '')
        img     = c.get('image', '')
        category = ''
        # 詳細エンドポイントで category を取得
        if card_id:
            try:
                dr = requests.get(f'{TCGDEX_JA}/cards/{card_id}', timeout=5)
                if dr.status_code == 200:
                    category = dr.json().get('category', '')
            except Exception:
                pass
        return {
            'id':          card_id,
            'name':        c.get('name', ''),
            'supertype':   _TCGDEX_CATEGORY.get(category, 'Pokémon'),
            'subtypes':    [],
            'types':       [],
            'set_name':    (c.get('set') or {}).get('name', ''),
            'number':      c.get('localId', ''),
            'image_small': f'{img}/low.jpg'  if img else '',
            'image_large': f'{img}/high.jpg' if img else '',
        }

    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(_fetch_one, data))


def _search_official_ja(query):
    """公式 pokemon-card.com APIでカードを検索（最新カード・画像補完用）"""
    try:
        r = requests.get(
            f'{OFFICIAL_BASE}/card-search/resultAPI.php',
            params={'keyword': query, 'sm_and_keyword': 'true', 'regulation_sidebar_form': 'ALL'},
            headers=_PC_HEADERS,
            timeout=10,
        )
        if r.status_code != 200:
            return []
        card_list = r.json().get('cardList', [])[:24]

        cards = []
        for c in card_list:
            thumb = c.get('cardThumbFile', '')
            # 公式画像はサーバー経由でプロキシして取得（CORS回避）
            raw_url = f'{OFFICIAL_BASE}{thumb}' if thumb else ''
            img_url = f'/proxy-image?url={quote(raw_url, safe="")}' if raw_url else ''
            # サムネイルパスからスーパータイプを判定: /001_P_ = Pokémon, _T_ = Trainer, _E_ = Energy
            m = re.search(r'/\d+_([A-Z])_', thumb)
            prefix = m.group(1) if m else 'P'
            supertype = {'P': 'Pokémon', 'T': 'Trainer', 'E': 'Energy'}.get(prefix, 'Pokémon')
            name = c.get('cardNameAltText') or c.get('cardName', '')
            # セット名をサムネイルパスから抽出: /large/M3/049703... → "M3"
            m_set = re.search(r'/large/([^/]+)/', thumb)
            set_name = m_set.group(1) if m_set else c.get('expansionName', '')
            uid = f"official-{c.get('cardID', thumb)}"
            cards.append({
                'id':          uid,
                'name':        name,
                'supertype':   supertype,
                'subtypes':    [],
                'types':       [],
                'set_name':    set_name,
                'number':      c.get('cardNumber', ''),
                'image_small': img_url,
                'image_large': img_url,
            })
        return cards
    except Exception:
        return []


# ---- API: Deck management ----

@app.route('/api/deck/<int:deck_id>/rename', methods=['POST'])
def api_rename(deck_id):
    name = (request.json or {}).get('name', '')
    db.rename_deck(deck_id, name)
    return jsonify({'ok': True})


@app.route('/api/deck/<int:deck_id>/delete', methods=['POST'])
def api_delete_deck(deck_id):
    db.delete_deck(deck_id)
    return jsonify({'ok': True})


@app.route('/api/deck/<int:deck_id>/add', methods=['POST'])
def api_add_card(deck_id):
    card_data = request.json or {}
    new_qty = db.add_or_increment_card(deck_id, card_data)
    total = db.get_deck_total(deck_id)
    return jsonify({'ok': True, 'quantity': new_qty, 'total': total})


@app.route('/api/deck/<int:deck_id>/update', methods=['POST'])
def api_update_qty(deck_id):
    data = request.json or {}
    db.update_quantity(deck_id, data['card_id'], int(data['quantity']))
    total = db.get_deck_total(deck_id)
    return jsonify({'ok': True, 'total': total})


@app.route('/api/deck/<int:deck_id>/remove', methods=['POST'])
def api_remove_card(deck_id):
    card_id = (request.json or {}).get('card_id')
    db.remove_card(deck_id, card_id)
    total = db.get_deck_total(deck_id)
    return jsonify({'ok': True, 'total': total})


@app.route('/api/deck/<int:deck_id>/import-code', methods=['POST'])
def api_import_code(deck_id):
    """pokemon-card.com の公式デッキコードからインポート"""
    raw = (request.json or {}).get('code', '').strip()
    if not raw:
        return jsonify({'ok': False, 'error': 'コードを入力してください'}), 400

    # URL形式でも受け付ける（deckID/XXXXX or 共有URL全体）
    m = _DECK_CODE_RE.search(raw)
    if not m:
        return jsonify({'ok': False,
                        'error': 'デッキコードの形式が正しくありません（例: aabbcc-112233-xxyyzz）'}), 400
    code = m.group(0)

    cards = _fetch_official_deck(code)
    if cards is None:
        return jsonify({'ok': False,
                        'error': 'デッキが見つかりませんでした。コードをご確認ください'}), 404
    if not cards:
        return jsonify({'ok': False,
                        'error': 'カードデータを読み取れませんでした'}), 422

    db.bulk_add_cards(deck_id, cards)
    total = db.get_deck_total(deck_id)
    return jsonify({'ok': True, 'count': len(cards), 'total': total})


@app.route('/api/deck/<int:deck_id>/import', methods=['POST'])
def api_import(deck_id):
    """PTCGL テキスト形式（コピペ）をインポート"""
    text = (request.json or {}).get('text', '')
    parsed = _parse_ptcgl(text)
    if not parsed:
        return jsonify({'ok': False, 'error': 'カードが読み取れませんでした'}), 400

    # API でカード画像を取得（最大20枚一括）
    enriched = _enrich_cards(parsed)
    db.bulk_add_cards(deck_id, enriched)
    total = db.get_deck_total(deck_id)
    return jsonify({'ok': True, 'count': len(enriched), 'total': total})


# ---- API: Probability ----

@app.route('/api/calc', methods=['POST'])
def api_calc():
    data = request.json or {}
    deck_size   = int(data.get('deck_size', 60))
    drawn       = int(data.get('drawn', 0))
    target_cards = data.get('cards', [])  # [{card_id, card_name, copies}]

    results = []
    for card in target_cards:
        copies = int(card.get('copies', 0))
        turn_probs = prob.calc_card_probs(deck_size, copies, drawn)
        results.append({
            'card_id':   card['card_id'],
            'card_name': card['card_name'],
            'copies':    copies,
            'turns':     turn_probs,
        })

    return jsonify(results)


# ---- Helpers ----

# ── 公式デッキコードのフェッチ & パース ────────────────────────────────────
# pokemon-card.com の print.html/deckID/{code}/ を BeautifulSoup でパース。
# テーブルの行: [カード名, 枚数] または [カード名, 枚数, エキスパンション, No.]

_DECK_CODE_RE = re.compile(r'[A-Za-z0-9]{6}-[A-Za-z0-9]{6}-[A-Za-z0-9]{6}')

# print.html テーブルでスキップするセクションヘッダ等
_DECK_PRINT_SKIP = {
    'ポケモン', 'グッズ', 'ポケモンのどうぐ', 'ワザマシン', 'サポート',
    'スタジアム', 'エネルギー', 'ACE SPEC',
    '枚数', 'エキスパンション', 'コレクションNo.', '小計', '合計',
}

# セクションヘッダ → supertype
_SECTION_SUPERTYPE = {
    'ポケモン':        'Pokémon',
    'グッズ':          'Trainer',
    'ポケモンのどうぐ': 'Trainer',
    'ワザマシン':      'Trainer',
    'サポート':        'Trainer',
    'スタジアム':      'Trainer',
    'ACE SPEC':        'Trainer',
    'エネルギー':      'Energy',
}


def _fetch_limitless_image(expansion: str, collection_no: str) -> str:
    """Limitless CDN から画像URLを解決する。存在しなければ空文字を返す。"""
    if not expansion or not collection_no:
        return ''
    num_str = collection_no.split('/')[0].lstrip('0') or '0'
    url = f'{LIMITLESS_BASE}/{expansion}/{expansion}_{num_str}_R_JP_SM.png'
    try:
        r = requests.head(url, timeout=6)
        if r.status_code == 200:
            return url
    except Exception:
        pass
    return ''


# TCGdex stage → DB subtypes 文字列への変換マップ
_STAGE_TO_SUBTYPE = {
    'Basic':    '',
    'Stage1':   'Stage 1',
    'Stage2':   'Stage 2',
    'RESTORED': 'RESTORED',
    'BREAK':    'BREAK',
    'MEGA':     'MEGA',
    'LEVEL-UP': 'LEVEL-UP',
}


def _fetch_card_stage(expansion: str, collection_no: str, supertype: str) -> str:
    """TCGdex から カードのステージ（subtypes）を取得する。
    Pokémon以外は空文字を返す。取得失敗時も空文字を返す。"""
    if supertype != 'Pokémon' or not expansion or not collection_no:
        return ''
    # TCGdex は小文字のセットIDを使用 (例: SV8a → sv8a)
    tcg_set = expansion.lower()
    local_num = collection_no.split('/')[0].lstrip('0') or '0'
    try:
        r = requests.get(f'{TCGDEX_JA}/sets/{tcg_set}/{local_num}', timeout=6)
        if r.status_code == 200:
            data = r.json()
            stage = data.get('stage', '')
            return _STAGE_TO_SUBTYPE.get(stage, '')
    except Exception:
        pass
    return ''


def _fetch_card_image(card_name: str, expansion: str = '', collection_no: str = '') -> str:
    """カード画像URLを優先順に解決して返す。"""
    # 1. Limitless CDN 直接
    url = _fetch_limitless_image(expansion, collection_no)
    if url:
        return url

    # 2. TCGdex セットID＋番号
    if expansion and collection_no:
        local_num = collection_no.split('/')[0]
        try:
            r = requests.get(f'{TCGDEX_JA}/sets/{expansion}/{local_num}', timeout=8)
            if r.status_code == 200:
                img = r.json().get('image', '')
                if img:
                    return f'{img}/high.jpg'
        except Exception:
            pass

    # 3. TCGdex 名前検索 → Limitless CDN 再試行 → TCGdex 画像
    clean = re.sub(r'\(.*?\)', '', card_name).strip()
    try:
        r = requests.get(f'{TCGDEX_JA}/cards', params={'name': clean}, timeout=8)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                exact = [c for c in data if c.get('name') == clean]
                candidates = exact if exact else data
                # Limitless CDN を再試行
                for c in candidates:
                    cid = c.get('id', '')
                    if '-' in cid:
                        exp, num = cid.split('-', 1)
                        lu = _fetch_limitless_image(exp, num)
                        if lu:
                            return lu
                # TCGdex 画像フォールバック
                for c in candidates:
                    img = c.get('image', '')
                    if img:
                        return f'{img}/high.jpg'
    except Exception:
        pass

    # 4. 公式 API 最終フォールバック
    try:
        r = requests.get(
            f'{OFFICIAL_BASE}/card-search/resultAPI.php',
            params={'keyword': card_name, 'sm_and_keyword': 'true',
                    'regulation_sidebar_form': 'ALL'},
            headers=_PC_HEADERS, timeout=10,
        )
        if r.status_code == 200:
            hits = r.json().get('cardList', [])
            if hits:
                thumb = hits[0].get('cardThumbFile', '')
                if thumb:
                    raw_url = f'{OFFICIAL_BASE}{thumb}'
                    return f'/proxy-image?url={quote(raw_url, safe="")}'
    except Exception:
        pass

    return ''


def _fetch_official_deck(code: str):
    """pokemon-card.com の print.html からデッキカードリストを取得。
    失敗時は None、成功時はカードの list を返す。"""
    try:
        url = f'{OFFICIAL_BASE}/deck/print.html/deckID/{code}/'
        resp = requests.get(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                                   'Chrome/120.0.0.0 Safari/537.36'},
            timeout=15,
        )
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, 'html.parser')
        raw_cards = []
        current_supertype = 'Pokémon'

        for table in soup.find_all('table'):
            for row in table.find_all('tr'):
                cells = [td.get_text(strip=True) for td in row.find_all('td')]
                if not cells:
                    continue

                first = cells[0]

                # セクションヘッダ検出 → supertype を切り替え
                if first in _SECTION_SUPERTYPE:
                    current_supertype = _SECTION_SUPERTYPE[first]
                    continue

                # その他スキップ対象行
                if first in _DECK_PRINT_SKIP:
                    continue

                if len(cells) < 2:
                    continue

                qty_str = cells[1]
                if qty_str in _DECK_PRINT_SKIP:
                    continue

                try:
                    qty = int(qty_str)
                    if first and qty > 0:
                        expansion     = cells[2] if len(cells) >= 4 else ''
                        collection_no = cells[3] if len(cells) >= 4 else ''
                        raw_cards.append({
                            'name':          first,
                            'qty':           qty,
                            'expansion':     expansion,
                            'collection_no': collection_no,
                            'supertype':     current_supertype,
                        })
                except ValueError:
                    pass

        if not raw_cards:
            return []

        # 画像＋ステージを並列で解決
        def _resolve(entry):
            img   = _fetch_card_image(
                entry['name'],
                entry['expansion'],
                entry['collection_no'],
            )
            stage = _fetch_card_stage(
                entry['expansion'],
                entry['collection_no'],
                entry['supertype'],
            )
            uid = (f"official-{entry['expansion']}-{entry['collection_no']}"
                   if entry['expansion']
                   else f"official-{entry['name']}")
            return {
                'id':          uid,
                'card_name':   entry['name'],
                'supertype':   entry['supertype'],
                'subtypes':    stage,
                'types':       '',
                'set_name':    entry['expansion'],
                'image_small': img,
                'image_large': img,
                'quantity':    entry['qty'],
            }

        with ThreadPoolExecutor(max_workers=8) as ex:
            return list(ex.map(_resolve, raw_cards))

    except Exception:
        return None


# ────────────────────────────────────────────────────────────────────────────

_LINE_RE = re.compile(
    r'^(\d+)\s+(.+?)\s+([A-Z]{1,5}\d*)\s+(\d+[A-Z]?)\s*$'
)

def _parse_ptcgl(text):
    """PTCGL エクスポートテキストをパース"""
    cards = []
    current_type = ''
    for raw in text.splitlines():
        line = raw.strip()
        lower = line.lower()
        if lower.startswith('pokémon') or lower.startswith('pokemon'):
            current_type = 'Pokémon'
        elif lower.startswith('trainer'):
            current_type = 'Trainer'
        elif lower.startswith('energy'):
            current_type = 'Energy'
        m = _LINE_RE.match(line)
        if m:
            qty     = int(m.group(1))
            name    = m.group(2).strip()
            set_cd  = m.group(3)
            num     = m.group(4)
            cards.append({
                'card_name': name,
                'quantity':  qty,
                'supertype': current_type,
                'set_code':  set_cd,
                'number':    num,
            })
    return cards


def _enrich_cards(parsed):
    """パース結果に画像URLを付与する（TCGdex → 公式サイト → PTCG APIの順で試みる）"""
    enriched = []
    for c in parsed:
        name = c['card_name']
        set_code = c.get('set_code', '')
        number   = c.get('number', '')
        image_small = ''
        card_id = f"import-{name.lower().replace(' ', '-')}-{set_code}"

        # 1. TCGdex: セットコード＋番号で直接取得（最速）
        if set_code and number:
            try:
                r = requests.get(f'{TCGDEX_JA}/sets/{set_code}/{number}', timeout=6)
                if r.status_code == 200:
                    data = r.json()
                    img = data.get('image', '')
                    if img:
                        card_id     = data.get('id', card_id)
                        image_small = f'{img}/low.jpg'
            except Exception:
                pass

        # 2. TCGdex: 英語名で検索
        if not image_small:
            try:
                r = requests.get(f'{TCGDEX_JA}/cards', params={'name': name}, timeout=6)
                if r.status_code == 200:
                    hits = r.json()
                    if isinstance(hits, list) and hits:
                        img = hits[0].get('image', '')
                        if img:
                            card_id     = hits[0].get('id', card_id)
                            image_small = f'{img}/low.jpg'
            except Exception:
                pass

        # 3. PTCG API（英語カード名で検索）
        if not image_small:
            try:
                r = requests.get(
                    f'{PTCG_API}/cards',
                    params={'q': f'name:"{name}"', 'pageSize': 1},
                    headers=_PTCG_HEADERS,
                    timeout=8,
                )
                hits = r.json().get('data', [])
                if hits:
                    card_id     = hits[0]['id']
                    image_small = hits[0].get('images', {}).get('small', '')
            except Exception:
                pass

        enriched.append({
            'id':          card_id,
            'card_name':   name,
            'supertype':   c.get('supertype', ''),
            'subtypes':    '',
            'types':       '',
            'set_name':    set_code,
            'image_small': image_small,
            'quantity':    c['quantity'],
        })

    return enriched


# ---- Quiz ----

@app.route('/quiz')
def quiz_list():
    questions = db.get_all_questions()
    return render_template('quiz_list.html', questions=questions)


@app.route('/quiz/new')
def quiz_new():
    return render_template('quiz_edit.html', question=None, choices=[])


@app.route('/quiz/create', methods=['POST'])
def quiz_create():
    data = _parse_quiz_form(request.form)
    qid = db.create_question(data)
    return redirect(url_for('quiz_play', question_id=qid))


@app.route('/quiz/<int:question_id>')
def quiz_play(question_id):
    q = db.get_question(question_id)
    if not q:
        return redirect(url_for('quiz_list'))
    choices = db.get_choices(question_id)
    # 各選択肢の確率を計算
    probs = []
    for c in choices:
        p = prob.prob_at_least_one(
            q['deck_remaining'],
            q['win_card_count'],
            c['draw_count'],
        )
        probs.append(round(p * 100, 1))
    return render_template('quiz_play.html', q=q, choices=choices, probs=probs)


@app.route('/quiz/<int:question_id>/edit')
def quiz_edit(question_id):
    q = db.get_question(question_id)
    if not q:
        return redirect(url_for('quiz_list'))
    choices = db.get_choices(question_id)
    return render_template('quiz_edit.html', question=q, choices=choices)


@app.route('/quiz/<int:question_id>/update', methods=['POST'])
def quiz_update(question_id):
    data = _parse_quiz_form(request.form)
    db.update_question(question_id, data)
    return redirect(url_for('quiz_play', question_id=question_id))


@app.route('/quiz/<int:question_id>/delete', methods=['POST'])
def quiz_delete(question_id):
    db.delete_question(question_id)
    return redirect(url_for('quiz_list'))


def _parse_quiz_form(form):
    choices = []
    for i in range(1, 5):
        name = form.get(f'choice_{i}_name', '').strip()
        if name:
            choices.append({
                'card_name':  name,
                'draw_count': int(form.get(f'choice_{i}_draw', 3)),
                'is_search':  form.get(f'choice_{i}_search') == '1',
                'memo':       form.get(f'choice_{i}_memo', '').strip(),
            })
    return {
        'title':          form.get('title', '').strip() or '新しい問題',
        'scenario':       form.get('scenario', '').strip(),
        'deck_remaining': int(form.get('deck_remaining', 20)),
        'side_remaining': int(form.get('side_remaining', 3)),
        'win_card_name':  form.get('win_card_name', '').strip(),
        'win_card_count': int(form.get('win_card_count', 1)),
        'explanation':    form.get('explanation', '').strip(),
        'correct_choice': int(form.get('correct_choice', 1)),
        'choices':        choices,
    }


db.init_db()

if __name__ == '__main__':
    app.run(debug=True, port=5005)
