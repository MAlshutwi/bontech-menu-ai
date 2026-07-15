"""
app/trial_models.py - shared trial-model builders and rankers.
All helpers operate on basket collections, so the same logic works for full-data
and train-only evaluations. Dependencies are limited to numpy and stdlib.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from itertools import combinations
import numpy as np


# ---------- Time buckets ----------
def hour_to_bucket(hour: int, buckets: dict) -> str:
    """Map an hour to a configured bucket, including buckets that cross midnight."""
    for name, (lo, hi) in buckets.items():
        if hi <= 24:
            if lo <= hour < hi:
                return name
        else:  # Crosses midnight: [lo,24) plus [0, hi-24).
            if hour >= lo or hour < (hi - 24):
                return name
    return "unknown"


# ---------- Basket statistics ----------
def basket_stats(baskets):
    item_cnt, pair_cnt, n = Counter(), Counter(), 0
    for items in baskets:
        if not items:
            continue
        n += 1
        for it in items:
            item_cnt[it] += 1
        for a, b in combinations(sorted(items), 2):
            pair_cnt[(a, b)] += 1
    return item_cnt, pair_cnt, n


# ---------- Directional FBT and rank maps ----------
def fbt_directional(item_cnt, pair_cnt, n, min_pair):
    """a -> {b: (conf, lift, pair_count)}"""
    cand = defaultdict(dict)
    for (a, b), c in pair_cnt.items():
        if c < min_pair:
            continue
        conf_ab = c / item_cnt[a]
        conf_ba = c / item_cnt[b]
        cand[a][b] = (conf_ab, conf_ab / (item_cnt[b] / n), c)
        cand[b][a] = (conf_ba, conf_ba / (item_cnt[a] / n), c)
    return cand


def _minmax(d):
    if not d:
        return {}
    vals = list(d.values())
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return {k: 0.5 for k in d}  # Neutral score for one-neighbor or tie cases.
    return {k: (v - lo) / (hi - lo) for k, v in d.items()}


def fbt_rank_maps(cand, item_cnt, n, pair_weights, alphas, topk):
    """
    Build per-seed ranking maps for each FBT model:
      fbt_confidence, fbt_lift, fbt_paircount, fbt_hybrid, smoothed_fbt_a{alpha}
    Return dict[model] -> {a: [(b, score), ...]}.
    """
    models = defaultdict(dict)
    W = pair_weights
    for a, bs in cand.items():
        conf = {b: v[0] for b, v in bs.items()}
        lift = {b: v[1] for b, v in bs.items()}
        pc = {b: v[2] for b, v in bs.items()}
        cn, ln, pn = _minmax(conf), _minmax(lift), _minmax(pc)
        hybrid = {b: W["confidence"] * cn[b] + W["lift"] * ln[b] + W["pair_count"] * pn[b] for b in bs}
        # smoothed Bayesian confidence: (pair + alpha*P(b)) / (a_count + alpha)
        smoothed = {}
        a_count = item_cnt[a]
        for alpha in alphas:
            sm = {}
            for b, v in bs.items():
                pair = v[2]
                prior = item_cnt[b] / n
                sm[b] = (pair + alpha * prior) / (a_count + alpha)
            smoothed[alpha] = sm

        def top(d):
            return sorted(d.items(), key=lambda x: x[1], reverse=True)[:topk]
        models["fbt_confidence"][a] = top(conf)
        models["fbt_lift"][a] = top(lift)
        models["fbt_paircount"][a] = top(pc)
        models["fbt_hybrid"][a] = top(hybrid)
        for alpha in alphas:
            models[f"smoothed_fbt_a{alpha}"][a] = top(smoothed[alpha])
    return models


# ---------- item2vec using PPMI and SVD ----------
def item2vec_neighbors(baskets, item_cnt, dim, min_count, topk):
    """Return item -> [(neighbor, cosine_sim)] for the top K neighbors."""
    items = [i for i, c in item_cnt.items() if c >= min_count]
    M = len(items)
    if M < 3:
        return {}
    idx = {it: k for k, it in enumerate(items)}
    co = np.zeros((M, M), dtype=np.float64)
    for s in baskets:
        loc = [idx[i] for i in s if i in idx]
        for x in range(len(loc)):
            for y in range(x + 1, len(loc)):
                co[loc[x], loc[y]] += 1.0
                co[loc[y], loc[x]] += 1.0
    total = co.sum()
    if total <= 0:
        return {}
    row = co.sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ppmi = np.log((co * total) / (np.outer(row, row) + 1e-12) + 1e-12)
    ppmi[~np.isfinite(ppmi)] = 0.0
    ppmi[ppmi < 0] = 0.0
    d = int(min(dim, M - 1))
    try:
        U, S, _ = np.linalg.svd(ppmi)
    except np.linalg.LinAlgError:
        return {}
    emb = U[:, :d] * np.sqrt(S[:d])
    norm = np.linalg.norm(emb, axis=1, keepdims=True)
    norm[norm == 0] = 1.0
    emb = emb / norm
    sims = emb @ emb.T
    out = {}
    for it, k in idx.items():
        order = np.argsort(-sims[k])
        out[it] = [(items[j], float(sims[k, j])) for j in order if j != k and sims[k, j] > 0][:topk]
    return out


# ---------- bundles (frequent itemsets) ----------
def mine_bundles(baskets, min_support, max_len, big_basket_cap=12):
    cnt = Counter()
    for s in baskets:
        items = sorted(s)
        L = len(items)
        if L < 2:
            continue
        upper = max_len if L <= big_basket_cap else 2  # Avoid triple blowups for large baskets.
        for k in range(2, min(upper, L) + 1):
            for combo in combinations(items, k):
                cnt[combo] += 1
    return [(set(c), v) for c, v in cnt.items() if v >= min_support]


def bundle_rank_map(bundles, topk):
    m = defaultdict(lambda: defaultdict(float))
    for itemset, sup in bundles:
        for a in itemset:
            for b in itemset:
                if a != b:
                    m[a][b] += sup
    return {a: sorted(d.items(), key=lambda x: x[1], reverse=True)[:topk] for a, d in m.items()}


# ---------- Category affinity for category-aware reranking ----------
def category_cooc(baskets, item_cat):
    """Return cross-category affinity P(category B in basket | category A in basket)."""
    cc = defaultdict(Counter)
    catcnt = Counter()
    for s in baskets:
        cats = set(item_cat.get(i) for i in s if item_cat.get(i) is not None)
        for c in cats:
            catcnt[c] += 1
        for a in cats:
            for b in cats:
                if a != b:
                    cc[a][b] += 1
    aff = defaultdict(dict)
    for a, d in cc.items():
        for b, v in d.items():
            aff[a][b] = v / catcnt[a]
    return aff


# ---------- Serving-time rankers ----------
def rank_from_map(rec_map, seed, k, blocked=None):
    """Aggregate scores across cart items and return the top K candidates."""
    blocked = blocked or set()
    agg = {}
    for it in seed:
        for b, sc in rec_map.get(it, []):
            if b in seed or b in blocked:
                continue
            agg[b] = agg.get(b, 0.0) + sc
    return [b for b, _ in sorted(agg.items(), key=lambda x: x[1], reverse=True)[:k]], agg


def rank_popularity(pop_list, seed, k, blocked=None):
    blocked = blocked or set()
    return [p for p in pop_list if p not in seed and p not in blocked][:k]
