from math import comb


def hypergeometric(N, K, n, k):
    """P(X = k): N枚デッキ・K枚対象・n枚ドロー・ちょうどk枚引く確率"""
    if N <= 0 or n <= 0 or K < 0:
        return 0.0
    if k < max(0, n - (N - K)) or k > min(K, n):
        return 0.0
    return comb(K, k) * comb(N - K, n - k) / comb(N, n)


def prob_at_least_one(N, K, n):
    """1枚以上引く確率 = 1 - P(0枚)"""
    if K <= 0 or N <= 0 or n <= 0:
        return 0.0
    return 1.0 - hypergeometric(N, K, n, 0)


def prob_distribution(N, K, n):
    """0〜min(K,n)枚引く確率の分布を返す"""
    return {k: hypergeometric(N, K, n, k) for k in range(min(K, n) + 1)}


def calc_card_probs(deck_size, copies, drawn_so_far=0):
    """
    1枚以上引く確率をターンごとに計算。
    drawn_so_far: すでにドロー済みの枚数（手札に入っている分を除く）
    Returns: list of {turn, hand_size, prob, out_of_100}
    """
    remaining = deck_size - drawn_so_far
    remaining_copies = max(0, copies)
    results = []

    # 初手(ターン0): 7枚
    for turn in range(0, 9):
        hand_size = 7 + turn
        hand_size = min(hand_size, remaining)
        p = prob_at_least_one(remaining, remaining_copies, hand_size)
        results.append({
            'turn': turn,
            'label': 'はじめの7まい' if turn == 0 else f'{turn}ターン目まで',
            'hand_size': hand_size,
            'prob': round(p * 100, 1),
            'out_of_100': round(p * 100),
        })

    return results
