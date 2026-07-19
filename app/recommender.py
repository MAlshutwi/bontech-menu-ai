"""
app/recommender.py - hybrid recommendation engine for artifact-only serving.

Loads prebuilt artifacts and blends weighted recommendation sources from config.yaml:
  restaurant_fbt · restaurant_popularity · global_common · customer_affinity · recency

Serving rules:
  - Do not recommend items unavailable in the current restaurant.
  - Do not recommend items already in the cart unless configured as repeatable.
  - Without customer_id, redistribute customer affinity weight to FBT and popularity.
  - Use a fallback hierarchy: FBT, restaurant popularity, then global common items.
"""
from __future__ import annotations
import json
from datetime import datetime
import pandas as pd

from .config import (ARTIFACTS, HYBRID_WEIGHTS, SERVING, CUSTOMER, MODEL_VERSION,
                     PHASE09, PHASE10)
from .trial_models import hour_to_bucket


def _f(x):
    try:
        return float(x)
    except Exception:
        return 0.0


def _norm_title_str(s) -> str:
    return " ".join(str(s or "").strip().lower().split())


class Recommender:
    def __init__(self, *, load_customer_profiles: bool | None = None):
        self.model_version = MODEL_VERSION
        self.only_available = bool(SERVING.get("only_items_available_in_restaurant", True))
        self.exclude_in_cart = bool(SERVING.get("exclude_items_in_cart", True))
        self.candidate_pool_size = max(10, min(int(SERVING.get("candidate_pool_size", 40)), 100))
        self.load_customer_profiles = bool(
            CUSTOMER.get("enabled", False)
            if load_customer_profiles is None
            else load_customer_profiles
        )
        # Categories that may be recommended repeatedly, configured for items such as drinks.
        self.repeatable = set(int(c) for c in CUSTOMER.get("repeatable_categories", []) if str(c).strip())
        self._load()

    # ---------------- Artifact loading ----------------
    def _load(self):
        A = ARTIFACTS
        fbt = pd.read_parquet(A / "fbt_pairs.parquet")
        pop = pd.read_parquet(A / "restaurant_popularity.parquet")
        gpop = pd.read_parquet(A / "global_popularity.parquet")
        mapping = pd.read_parquet(A / "item_scope_mapping.parquet")
        try:
            rest_items_df = pd.read_parquet(A / "restaurant_items.parquet")
        except Exception:
            rest_items_df = mapping[["restaurant_id", "item_id"]].copy()
        profiles = pd.DataFrame(columns=["customer_id", "top_items", "restaurants"])
        if self.load_customer_profiles:
            try:
                profiles = pd.read_parquet(A / "customer_profiles.parquet")
            except Exception:
                pass
        try:
            recency = pd.read_parquet(A / "item_recency.parquet")
        except Exception:
            recency = pd.DataFrame(columns=["restaurant_id", "item_id", "recency_score"])

        # Item titles, primarily from the mapping artifact.
        self.titles = {}
        for r in mapping.itertuples():
            self.titles[int(r.item_id)] = (r.title_en or "", r.title_ar or "")
        for r in fbt.itertuples():  # Fill missing titles from both sides of FBT pairs.
            self.titles.setdefault(int(r.item_a), (r.item_a_title or "", ""))
            self.titles.setdefault(int(r.item_b), (r.item_b_title or "", ""))
        for r in pop.itertuples():
            self.titles.setdefault(int(r.item_id), (r.title or "", ""))

        # Item maps from mapping: common group, category, and normalized display name.
        # title_to_group maps display names to common groups for reliable deduplication.
        self.item_group = {}
        self.item_category = {}
        self.norm_title = {}
        self.title_to_group = {}
        for r in mapping.itertuples():
            iid = int(r.item_id)
            if pd.notna(r.common_group_id):
                gid = int(r.common_group_id)
                self.item_group[iid] = gid
                for t in (r.title_en, r.title_ar):
                    b = _norm_title_str(t)
                    if b:
                        self.title_to_group.setdefault(b, gid)
            if pd.notna(r.category_id):
                self.item_category[iid] = int(r.category_id)
            if r.normalized_title:
                self.norm_title[iid] = r.normalized_title

        # Availability is inferred from items actually sold in the restaurant.
        self.rest_items = {}
        for r in rest_items_df.itertuples():
            self.rest_items.setdefault(int(r.restaurant_id), set()).add(int(r.item_id))

        # Common group to local sold item in a restaurant.
        self.group_local = {}
        for rid, items in self.rest_items.items():
            gl = {}
            for it in sorted(items):
                gid = self.item_group.get(it)
                if gid is not None:
                    gl.setdefault(gid, it)
            self.group_local[rid] = gl

        # FBT: rest -> item_a -> list[dict]
        self.fbt = {}
        for r in fbt.itertuples():
            self.fbt.setdefault(int(r.restaurant_id), {}).setdefault(int(r.item_a), []).append({
                "b": int(r.item_b), "hybrid": _f(r.hybrid_score), "pc": int(r.pair_count),
                "conf": _f(r.confidence), "lift": _f(r.lift),
            })
        for table in self.fbt.values():
            for candidates in table.values():
                candidates.sort(key=lambda row: (-row["hybrid"], row["b"]))

        # Popularity: restaurant -> {item: score} plus a sorted list.
        self.pop_score = {}
        self.pop_rank = {}
        for r in pop.itertuples():
            self.pop_score.setdefault(int(r.restaurant_id), {})[int(r.item_id)] = _f(r.score)
            self.pop_rank.setdefault(int(r.restaurant_id), []).append((int(r.item_id), _f(r.score)))
        for rid in self.pop_rank:
            self.pop_rank[rid].sort(key=lambda x: (-x[1], x[0]))

        # Global common items as a sorted list of (group_id, score).
        self.global_groups = [(int(r.common_group_id), _f(r.score)) for r in gpop.itertuples()]
        self.global_groups.sort(key=lambda x: (-x[1], x[0]))

        # recency: rest -> {item: score}
        self.recency = {}
        for r in recency.itertuples():
            self.recency.setdefault(int(r.restaurant_id), {})[int(r.item_id)] = _f(r.recency_score)

        # customer profiles
        self.profiles = {}
        for r in profiles.itertuples():
            self.profiles[int(r.customer_id)] = {
                "top_items": list(r.top_items) if r.top_items is not None else [],
                "restaurants": list(r.restaurants) if r.restaurants is not None else [],
            }

        self.restaurants_with_fbt = set(self.fbt.keys())
        self.restaurants_with_pop = set(self.pop_score.keys())
        self.restaurant_names = self._load_restaurant_names(A)

        # ---------- Phase 10 artifacts, all configurable ----------
        self.p10 = PHASE10 or {}
        self.time_buckets = (PHASE09 or {}).get("time_buckets", {})
        tacfg = self.p10.get("time_aware_popularity", {})
        self.wt = float(tacfg.get("weight", 0.0)) if tacfg.get("enabled") else 0.0
        self.ta_pop = {}
        if self.wt > 0:
            try:
                ta = pd.read_parquet(A / "phase10" / "time_aware_popularity.parquet")
                for r in ta.itertuples():
                    self.ta_pop.setdefault(int(r.restaurant_id), {}).setdefault(
                        r.time_bucket, {})[int(r.item_id)] = _f(r.score)
            except Exception:
                pass
        self.sim_alt = {}
        if self.p10.get("similar_alternatives", {}).get("enabled"):
            try:
                sa = pd.read_parquet(A / "phase10" / "similar_alternatives.parquet")
                for r in sa.itertuples():
                    self.sim_alt.setdefault(int(r.restaurant_id), {}).setdefault(
                        int(r.item_id), []).append((int(r.alt_item), _f(r.similarity)))
                    self.titles.setdefault(int(r.alt_item), (r.alt_title or "", ""))
                    self.titles.setdefault(int(r.item_id), (r.item_title or "", ""))
            except Exception:
                pass
        for table in self.sim_alt.values():
            for candidates in table.values():
                candidates.sort(key=lambda row: (-row[1], row[0]))
        self.pooled = {}
        self.pooled_enabled = bool(self.p10.get("pooled_fbt_fallback", {}).get("enabled"))
        if self.pooled_enabled:
            try:
                pf = pd.read_parquet(A / "phase10" / "pooled_fbt_fallback.parquet")
                for r in pf.itertuples():
                    self.pooled.setdefault(int(r.group_a), []).append((int(r.group_b), _f(r.confidence)))
                for gg in self.pooled:
                    self.pooled[gg].sort(key=lambda x: (-x[1], x[0]))
            except Exception:
                pass

    def _load_restaurant_names(self, artifacts_path):
        """Load demo restaurant names from artifacts only; no serving-time DB access."""
        names = {}
        for rel in (
            "phase10/phase10_manual_cases_results.json",
            "model_trials/manual_cases_results.json",
        ):
            p = artifacts_path / rel
            if not p.exists():
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            for case in data.get("cases", []):
                rid = case.get("restaurant_id")
                name = case.get("restaurant_name")
                if rid and name:
                    names.setdefault(int(rid), str(name))
        return names

    # ---------------- Recommendation sources ----------------
    def _fbt_scores(self, rid, cart):
        agg, ev = {}, {}
        table = self.fbt.get(rid, {})
        for ci in cart:
            for r in table.get(ci, []):
                b = r["b"]
                if b in cart:
                    continue
                agg[b] = agg.get(b, 0.0) + r["hybrid"]
                if b not in ev or r["pc"] > ev[b]["pair_count"]:
                    ev[b] = {"with_item": ci, "pair_count": r["pc"],
                             "confidence": round(r["conf"], 4), "lift": round(r["lift"], 3)}
        mx = max(agg.values()) if agg else 0.0
        scores = {k: v / mx for k, v in agg.items()} if mx > 0 else {}
        return scores, ev

    def _pop_scores(self, rid):
        return dict(self.pop_score.get(rid, {}))

    def _global_scores(self, rid):
        local = self.group_local.get(rid, {})
        out = {}
        for gid, score in self.global_groups:
            it = local.get(gid)
            if it is not None:
                out[it] = max(out.get(it, 0.0), score)
        return out

    def _customer_scores(self, customer_id, rid):
        prof = self.profiles.get(customer_id)
        if not prof:
            return {}
        avail = self.rest_items.get(rid, set())
        items = prof["top_items"]
        n = len(items)
        out = {}
        for i, it in enumerate(items):
            it = int(it)
            if avail and it not in avail:
                continue
            out[it] = (n - i) / n  # Descending rank 1..0.
        return out

    def _bucket(self, context):
        ts = context.get("timestamp") if isinstance(context, dict) else None
        if not ts:
            return None
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except Exception:
            return None
        return hour_to_bucket(dt.hour, self.time_buckets)

    def _time_aware_scores(self, rid, context):
        if self.wt <= 0 or rid not in self.ta_pop:
            return {}
        bk = self._bucket(context)
        if not bk:
            return {}
        return self.ta_pop[rid].get(bk, {})  # Missing bucket falls back to restaurant popularity.

    def _pooled_scores(self, rid, cart):
        local = self.group_local.get(rid, {})
        agg = {}
        for ci in cart:
            g = self.item_group.get(ci)
            if g is None:
                normalized_title = self.norm_title.get(ci)
                if normalized_title:
                    g = self.title_to_group.get(normalized_title)
            if g is None:
                continue
            for gb, conf in self.pooled.get(g, []):
                loc = local.get(gb)
                if loc is not None and loc not in cart:
                    agg[loc] = max(agg.get(loc, 0.0), conf)
        mx = max(agg.values()) if agg else 0.0
        return {k: v / mx for k, v in agg.items()} if mx > 0 else {}

    def _weights(self, customer_id):
        w = dict(HYBRID_WEIGHTS)
        has_cust = customer_id is not None and customer_id in self.profiles
        if not has_cust and w.get("customer_affinity", 0) > 0:
            extra = w["customer_affinity"]
            w["customer_affinity"] = 0.0
            base = w["restaurant_fbt"] + w["restaurant_popularity"]
            if base > 0:
                w["restaurant_fbt"] += extra * w["restaurant_fbt"] / base
                w["restaurant_popularity"] += extra * w["restaurant_popularity"] / base
        return w, has_cust

    # ---------------- Main recommendation interface ----------------
    def _valid_cart_for_restaurant(self, rid, cart_items):
        cart = list(dict.fromkeys(int(x) for x in (cart_items or [])))
        available = self.rest_items.get(int(rid), set())
        if not available:
            return cart
        return [item_id for item_id in cart if item_id in available]

    def _menu_cart_fallback_scores(self, rid, cart):
        """Return weak, category-aware menu candidates for sparse restaurants."""
        available = self.rest_items.get(rid, set())
        if not available:
            return {}
        cart_categories = {
            self.item_category[item_id]
            for item_id in cart
            if item_id in self.item_category
        }
        popularity = self.pop_score.get(rid, {})
        max_popularity = max(popularity.values(), default=0.0)
        scores = {}
        for item_id in sorted(available):
            if item_id in cart:
                continue
            category_id = self.item_category.get(item_id)
            category_complement = category_id is not None and category_id not in cart_categories
            normalized_popularity = (
                max(0.0, popularity.get(item_id, 0.0)) / max_popularity
                if max_popularity > 0
                else 0.0
            )
            scores[item_id] = 0.12 + (0.08 if category_complement else 0.0) + (0.03 * normalized_popularity)
        return scores

    def recommend(
        self,
        restaurant_id,
        cart_item_ids=None,
        customer_id=None,
        top_k=None,
        context=None,
        cart_only=False,
    ):
        rid = int(restaurant_id)
        top_k = int(top_k or SERVING.get("default_top_k", 5))
        requested_cart = [int(x) for x in (cart_item_ids or [])]
        cart = set(self._valid_cart_for_restaurant(rid, requested_cart))
        weights, has_cust = self._weights(customer_id)
        avail = self.rest_items.get(rid, set())

        fbt_s, fbt_ev = self._fbt_scores(rid, cart)
        # Pooled fallback for low-data restaurants without restaurant-specific FBT.
        pooled_used = False
        if not fbt_s and self.pooled_enabled:
            ps = self._pooled_scores(rid, cart)
            if ps:
                fbt_s, pooled_used = ps, True
        menu_fallback_used = False
        if cart_only:
            # Once a cart exists, rank cross-sell candidates only from cart
            # co-occurrence. Popularity remains an empty-cart discovery signal,
            # not a hidden influence on the cart recommendation.
            pop_s = {}
            glob_s = {}
            cust_s = {}
            rec_s = {}
            ta_s = {}
            menu_s = {}
            if not fbt_s and cart:
                menu_s = self._menu_cart_fallback_scores(rid, cart)
                menu_fallback_used = bool(menu_s)
            cands = set(fbt_s) | set(menu_s)
        else:
            pop_s = self._pop_scores(rid)
            glob_s = self._global_scores(rid)
            cust_s = self._customer_scores(customer_id, rid) if has_cust else {}
            rec_s = self.recency.get(rid, {})
            ta_s = self._time_aware_scores(rid, context)
            cands = set(fbt_s) | set(pop_s) | set(glob_s) | set(cust_s) | set(ta_s)
        scored = []
        for it in cands:
            if self.exclude_in_cart and it in cart and self.item_category.get(it) not in self.repeatable:
                continue
            if self.only_available and avail and it not in avail:
                continue
            if cart_only:
                contrib = {
                    "restaurant_fbt": fbt_s.get(it, 0.0),
                    "live_menu_fallback": menu_s.get(it, 0.0),
                }
            else:
                contrib = {
                    "restaurant_fbt": weights["restaurant_fbt"] * fbt_s.get(it, 0.0),
                    "restaurant_popularity": weights["restaurant_popularity"] * pop_s.get(it, 0.0),
                    "global_common": weights["global_common"] * glob_s.get(it, 0.0),
                    "customer_affinity": weights["customer_affinity"] * cust_s.get(it, 0.0),
                    "recency": weights["recency"] * rec_s.get(it, 0.0),
                    "time_based": self.wt * ta_s.get(it, 0.0),
                }
            total = sum(contrib.values())
            if total <= 0:
                continue
            source = max(contrib, key=contrib.get)
            if source == "restaurant_fbt" and pooled_used:
                source = "pooled_fbt"
            scored.append((it, total, source, contrib))

        scored.sort(key=lambda x: (-x[1], x[0]))
        fallback_used = bool(
            pooled_used
            or menu_fallback_used
            or len(fbt_s) == 0
            or (bool(requested_cart) and not cart)
        )

        # Deduplicate by common group or normalized title to avoid duplicate display items.
        # Repeatable items are not suppressed just because they are already in the cart.
        recs, seen = [], set(self._dedup_key(c) for c in cart
                             if self.item_category.get(c) not in self.repeatable)
        for it, sc, src, _ in scored:
            k = self._dedup_key(it)
            if k in seen:
                continue
            seen.add(k)
            recs.append(self._format(it, sc, src, fbt_ev.get(it)))
            if len(recs) >= top_k:
                break
        if not recs:
            fallback_used = True
            if not cart_only:
                recs = self._fallback(rid, cart, top_k)

        return {
            "restaurant_id": rid,
            "customer_id": customer_id,
            "recommendations": recs,
            "fallback_used": fallback_used,
            "model_version": self.model_version,
        }

    def _dedup_key(self, it):
        # Unified key: common group first, then mapped display title group when available.
        # This merges ungrouped deleted variants with their grouped equivalent.
        # Otherwise fall back to normalized display title.
        g = self.item_group.get(it)
        if g is not None:
            return f"g:{g}"
        ten, tar = self.titles.get(it, ("", ""))
        base = _norm_title_str(ten or tar or str(it))
        g2 = self.title_to_group.get(base)
        if g2 is not None:
            return f"g:{g2}"
        return f"t:{base}" if base else f"i:{it}"

    # ---------------- Fallback hierarchy ----------------
    def _fallback(self, rid, cart, top_k):
        out, seen = [], set(self._dedup_key(c) for c in cart
                            if self.item_category.get(c) not in self.repeatable)
        for it, score in self.pop_rank.get(rid, []):
            k = self._dedup_key(it)
            if k in seen:
                continue
            out.append(self._format(it, score, "restaurant_popularity", None))
            seen.add(k)
            if len(out) >= top_k:
                return out
        # Then use restaurant-local versions of global common groups.
        local = self.group_local.get(rid, {})
        for gid, score in self.global_groups:
            it = local.get(gid)
            if it is None:
                continue
            k = self._dedup_key(it)
            if k in seen:
                continue
            out.append(self._format(it, score, "global_common", None))
            seen.add(k)
            if len(out) >= top_k:
                break
        return out

    # ---------------- Recommendation formatting ----------------
    REASONS = {
        "restaurant_fbt": "يُطلب غالبًا مع الأصناف في سلتك",
        "restaurant_popularity": "من الأصناف الأكثر طلبًا في هذا المطعم",
        "global_common": "صنف شائع يطلبه الزبائن عادةً",
        "customer_affinity": "بناءً على طلباتك السابقة",
        "recency": "رائج مؤخرًا في هذا المطعم",
        "time_based": "مناسب لوقت الطلب الحالي",
        "pooled_fbt": "شائع مع أصناف سلتك عبر مطاعم مشابهة",
        "live_menu_fallback": "اقتراح تكميلي من أقسام المنيو المتاحة",
    }

    def _format(self, it, score, source, ev):
        ten, tar = self.titles.get(it, (f"item_{it}", ""))
        reason = self.REASONS.get(source, "")
        if source == "restaurant_fbt" and ev and ev.get("with_item") is not None:
            wen, _ = self.titles.get(ev["with_item"], (f"item_{ev['with_item']}", ""))
            reason = f"يُطلب غالبًا مع {wen}"
        return {
            "item_id": int(it),
            "title_ar": tar,
            "title_en": ten,
            "score": round(float(score), 4),
            "reason": reason,
            "source": source,
            "evidence": ev if (source == "restaurant_fbt" and ev) else {},
        }

    # API helpers
    def popular(self, restaurant_id, top_k=10, exclude=None):
        rid = int(restaurant_id)
        exclude = set(int(x) for x in (exclude or []))
        out, seen = [], set(self._dedup_key(c) for c in exclude)
        for it, sc in self.pop_rank.get(rid, []):
            if it in exclude:
                continue
            k = self._dedup_key(it)
            if k in seen:
                continue
            seen.add(k)
            out.append(self._format(it, sc, "restaurant_popularity", None))
            if len(out) >= top_k:
                break
        if not out:  # Restaurant has no popularity signal; use global fallback.
            out = self._fallback(rid, exclude, top_k)
        return out

    def for_customer(self, customer_id, restaurant_id, top_k=10):
        return self.recommend(restaurant_id, cart_item_ids=[], customer_id=customer_id, top_k=top_k)

    def menu(self, restaurant_id):
        """Return demo menu data from artifacts only; prices are unavailable."""
        rid = int(restaurant_id)
        available = self.rest_items.get(rid, set())
        known_restaurant = bool(available or rid in self.restaurants_with_pop or rid in self.restaurants_with_fbt)
        pop_rank = {int(it): int(i + 1) for i, (it, _) in enumerate(self.pop_rank.get(rid, []))}
        pop_score = self.pop_score.get(rid, {})
        fbt_seed_items = set(self.fbt.get(rid, {}).keys())
        sim_seed_items = set(self.sim_alt.get(rid, {}).keys())
        seed_candidates = [
            int(it) for it in available
            if int(it) in fbt_seed_items or int(it) in sim_seed_items or int(it) in pop_rank
        ]
        demo_seed = None
        if seed_candidates:
            demo_seed = min(
                seed_candidates,
                key=lambda it: (
                    0 if it in fbt_seed_items else 1,
                    0 if it in sim_seed_items else 1,
                    pop_rank.get(it, 999999),
                    str(self.titles.get(it, ("", ""))[0] or self.titles.get(it, ("", ""))[1] or it).lower(),
                ),
            )

        items = []
        category_counts = {}
        for it in sorted(available):
            ten, tar = self.titles.get(it, ("", ""))
            if not (ten or tar):
                continue
            category_id = self.item_category.get(it)
            if category_id is not None:
                category_counts[int(category_id)] = category_counts.get(int(category_id), 0) + 1
            item = {
                "item_id": int(it),
                "title_ar": tar or "",
                "title_en": ten or "",
                "category": f"Category {int(category_id)}" if category_id is not None else None,
                "category_id": int(category_id) if category_id is not None else None,
                "available": True,
                "availability_source": "restaurant_items.parquet",
                "price": None,
                "price_source": None,
                "disabled_reason": None,
                "popularity_rank": pop_rank.get(int(it)),
                "popularity_score": round(float(pop_score[int(it)]), 4) if int(it) in pop_score else None,
                "has_cross_sell": int(it) in fbt_seed_items,
                "has_similar_alternatives": int(it) in sim_seed_items,
            }
            items.append(item)

        def sort_key(x):
            if demo_seed is not None and x["item_id"] == demo_seed:
                return (0, 0, "")
            rank = x["popularity_rank"] if x["popularity_rank"] is not None else 999999
            signal = 0 if (x["has_cross_sell"] or x["has_similar_alternatives"]) else 1
            title = (x["title_en"] or x["title_ar"] or str(x["item_id"])).lower()
            return (1, signal, rank, title)

        items.sort(key=sort_key)
        categories = [
            {"category_id": cid, "category": f"Category {cid}", "count": count}
            for cid, count in sorted(category_counts.items(), key=lambda x: (-x[1], x[0]))
        ]
        disabled_reason = None
        if not known_restaurant:
            disabled_reason = "unknown_restaurant_or_no_menu_artifacts"
        elif not items:
            disabled_reason = "no_titled_menu_items_in_artifacts"
        return {
            "restaurant_id": rid,
            "restaurant_name": self.restaurant_names.get(rid, f"Restaurant {rid}"),
            "source": "artifacts",
            "uses_database": False,
            "known_restaurant": known_restaurant,
            "disabled_reason": disabled_reason,
            "price_available": False,
            "availability_available": True,
            "items_count": len(items),
            "available_items_count": len(available),
            "hidden_items_without_titles": max(0, len(available) - len(items)),
            "categories": categories,
            "default_cart_item_ids": [demo_seed] if demo_seed is not None else [],
            "selection_reason": (
                "Chosen for demo because it has recommendation signals in this restaurant."
                if demo_seed is not None else ""
            ),
            "items": items,
        }

    # ---------------- Phase 10 rails ----------------
    def similar_alternatives(self, restaurant_id, cart_item_ids, top_k=None):
        """Return a separate similar-alternatives rail without changing cross-sell order."""
        rid = int(restaurant_id)
        top_k = int(top_k or self.p10.get("similar_alternatives", {}).get("top_k", 5))
        cart = set(int(x) for x in (cart_item_ids or []))
        table = self.sim_alt.get(rid, {})
        if not table or not cart:
            return []
        avail = self.rest_items.get(rid, set())
        model_name = self.p10.get("similar_alternatives", {}).get("model_name", "item2vec")
        agg = {}
        for ci in cart:
            for alt, sim in table.get(ci, []):
                if alt in cart:
                    continue
                agg[alt] = max(agg.get(alt, 0.0), sim)  # Nearest alternative for any cart item.
        out, seen = [], set(self._dedup_key(c) for c in cart)
        for alt, sim in sorted(agg.items(), key=lambda x: (-x[1], x[0])):
            if self.only_available and avail and alt not in avail:
                continue
            k = self._dedup_key(alt)
            if k in seen:
                continue
            seen.add(k)
            ten, tar = self.titles.get(alt, (f"item_{alt}", ""))
            out.append({"item_id": int(alt), "title_ar": tar, "title_en": ten,
                        "score": round(float(sim), 4),
                        "reason": "صنف مشابه بناءً على تشابه سلوك الطلبات",
                        "source": "item2vec",
                        "evidence": {"similarity_score": round(float(sim), 4), "model_name": model_name}})
            if len(out) >= top_k:
                break
        return out

    def _time_based_list(self, rid, cart, context, top_k):
        ta = self._time_aware_scores(rid, context)
        if not ta:
            return []
        avail = self.rest_items.get(rid, set())
        out, seen = [], set(self._dedup_key(c) for c in cart
                            if self.item_category.get(c) not in self.repeatable)
        for it, sc in sorted(ta.items(), key=lambda x: (-x[1], x[0])):
            if it in cart or (self.only_available and avail and it not in avail):
                continue
            k = self._dedup_key(it)
            if k in seen:
                continue
            seen.add(k)
            out.append(self._format(it, sc, "time_based", None))
            if len(out) >= top_k:
                break
        return out

    def _global_list(self, rid, cart, top_k):
        out, seen = [], set(self._dedup_key(c) for c in cart)
        for it, sc in sorted(self._global_scores(rid).items(), key=lambda x: (-x[1], x[0])):
            if it in cart:
                continue
            k = self._dedup_key(it)
            if k in seen:
                continue
            seen.add(k)
            out.append(self._format(it, sc, "global_common", None))
            if len(out) >= top_k:
                break
        return out

    def recommend_groups(
        self,
        restaurant_id,
        cart_item_ids=None,
        customer_id=None,
        top_k=None,
        include_types=None,
        context=None,
        cart_only=False,
    ):
        """Return grouped rails while preserving legacy recommendations for compatibility."""
        rid = int(restaurant_id)
        top_k = int(top_k or SERVING.get("default_top_k", 5))
        cart = self._valid_cart_for_restaurant(rid, cart_item_ids)
        gcfg = self.p10.get("groups", {})
        include = include_types or gcfg.get("default_include_types",
                                            ["cross_sell", "similar_alternative", "popular"])
        titles = gcfg.get("titles", {})
        base = self.recommend(
            rid,
            cart,
            customer_id,
            top_k,
            context,
            cart_only=cart_only,
        )
        groups = []
        for t in include:
            if t == "cross_sell":
                items = base["recommendations"]
            elif t == "similar_alternative":
                items = self.similar_alternatives(rid, cart, top_k)
            elif t == "popular":
                items = self.popular(rid, top_k, exclude=cart)
            elif t == "time_based":
                items = self._time_based_list(rid, set(cart), context, top_k)
            elif t == "global_common":
                items = self._global_list(rid, set(cart), top_k)
            else:
                continue
            groups.append({"type": t, "title_ar": titles.get(t, t), "items": items})
        return {
            "restaurant_id": rid, "customer_id": customer_id,
            "recommendations": base["recommendations"],
            "recommendation_groups": groups,
            "fallback_used": base["fallback_used"],
            "model_version": self.model_version,
            "experiment_id": None,
        }


# singleton
_engine = None


def get_engine() -> Recommender:
    global _engine
    if _engine is None:
        from .model_loader import final_model_path, load_model

        if final_model_path().exists():
            _engine = load_model().engine
        else:
            _engine = Recommender()
    return _engine
