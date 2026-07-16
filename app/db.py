"""
app/db.py - read-only database access and standard cleaned query helpers.
All helpers are SELECT-only. No production database writes.
"""
from __future__ import annotations
import pandas as pd
from sqlalchemy import create_engine, text
from .config import db_url, CLEANING

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        # Enforce read-only behavior at the database session level.
        _engine = create_engine(
            db_url(), pool_pre_ping=True,
            connect_args={"options": "-c default_transaction_read_only=on"},
        )
    return _engine


def q(sql: str, **params) -> pd.DataFrame:
    """Execute a single SELECT/WITH statement and return a DataFrame."""
    stripped = sql.strip().rstrip(";").strip()
    head = stripped[:6].upper()
    if not (head.startswith("SELECT") or head.startswith("WITH")):
        raise ValueError("q() للقراءة فقط: يُسمح بـ SELECT/WITH فقط")
    if ";" in stripped:  # Reject multi-statement input before it reaches the database.
        raise ValueError("q() يرفض الجُمل المتعددة (multi-statement)")
    with get_engine().connect() as c:
        return pd.read_sql(text(sql), c, params=params)


def _clean_where() -> str:
    """Build cleaning predicates from config."""
    w = []
    if CLEANING.get("exclude_canceled_orders", True):
        w.append("ro.canceledate IS NULL")
    if CLEANING.get("exclude_deleted_orders", True):
        w.append("ro.isdeleted = false")
    if CLEANING.get("exclude_deleted_lines", True):
        w.append("mir.isdeleted = false")
    if CLEANING.get("exclude_wasted_lines", True):
        w.append("mir.iswasted = false")
    if CLEANING.get("exclude_recalled_lines", True):
        w.append("mir.isrecalled = false")
    if CLEANING.get("main_items_only", True):
        w.append("mir.parentid IS NULL")
    return " AND ".join(w) if w else "TRUE"


def clean_lines_sql() -> str:
    """
    Cleaned rows: one row per order and main item, with restaurant, time, and order type.
    Customer mapping is fetched separately because coverage is sparse and joins are heavy.
    """
    return f"""
        SELECT r.restaurantsid        AS restaurant_id,
               mir.reservationorderid AS order_id,
               mir.menuitemid         AS item_id,
               mir.quantity           AS quantity,
               ro.orderdate           AS order_date,
               CASE
                   WHEN ro.takeawayordertype = 'Delivery' THEN 'delivery'
                   WHEN ro.takeawayordertype IN ('Outside','Takeaway','DriveThru') THEN 'takeaway'
                   ELSE 'dine_in'
               END                    AS order_type
        FROM menuitemreservation mir
        JOIN reservationorder ro ON ro.id = mir.reservationorderid
        JOIN reservation r       ON r.id  = ro.reservationid
        WHERE {_clean_where()}
    """


def fetch_clean_lines() -> pd.DataFrame:
    """Fetch all cleaned line rows into memory."""
    return q(clean_lines_sql())


def fetch_clean_lines_cached(refresh: bool = False) -> pd.DataFrame:
    """Return cached cleaned rows, refreshing the parquet cache when requested."""
    from .config import ARTIFACTS
    p = ARTIFACTS / "_clean_lines.parquet"
    if p.exists() and not refresh:
        return pd.read_parquet(p)
    df = fetch_clean_lines()
    df.to_parquet(p, index=False)
    return df


def fetch_order_customer_map() -> pd.DataFrame:
    """
    Order-to-customer mapping from loyalty data; current coverage is sparse.
    Returns: order_id, customer_id.
    """
    return q("""
        SELECT DISTINCT reservationorderid AS order_id, usersid AS customer_id
        FROM customerpointsearned
        WHERE usersid IS NOT NULL
    """)


def fetch_item_catalog() -> pd.DataFrame:
    return q("""
        SELECT id AS item_id,
               restaurantsid AS restaurant_id,
               menucategoryid AS category_id,
               COALESCE(NULLIF(title_en,''), 'item_'||id) AS title_en,
               COALESCE(NULLIF(title_ar,''), '')          AS title_ar,
               COALESCE(iscombo, false) AS is_combo,
               ispublished,
               isdeleted
        FROM menuitem
    """)


def fetch_restaurants() -> pd.DataFrame:
    return q("""
        SELECT id AS restaurant_id,
               COALESCE(NULLIF(title_en,''), shortname, 'rest_'||id) AS name,
               COALESCE(NULLIF(title_ar,''), '') AS name_ar
        FROM restaurants
    """)


def fetch_restaurants_with_menu_counts() -> pd.DataFrame:
    """Restaurant names plus live menu counts for a usable menu selector."""
    return q("""
        SELECT r.id AS restaurant_id,
               COALESCE(NULLIF(r.title_en,''), r.shortname, 'rest_' || r.id) AS name,
               COALESCE(NULLIF(r.title_ar,''), '') AS name_ar,
               COUNT(DISTINCT mi.id) AS total_item_count,
               COUNT(DISTINCT mi.id) FILTER (
                   WHERE COALESCE(mi.ispublished, false) = true
                     AND COALESCE(mi.isdeleted, false) = false
               ) AS active_item_count
        FROM restaurants r
        LEFT JOIN menuitem mi ON mi.restaurantsid = r.id
        GROUP BY r.id, r.title_en, r.title_ar, r.shortname
        ORDER BY r.id
    """)


def fetch_restaurant_menu(restaurant_id: int, include_inactive: bool = True) -> pd.DataFrame:
    """Fetch every menu item for one restaurant directly from the source DB.

    This is intentionally separate from the model artifacts: a newly added item is
    visible in the trial menu immediately, even if it has not been trained on yet.
    ``include_inactive`` is useful for auditing the complete menu; the default is
    True because the trial page is meant to show *all* items.
    """
    active_filter = "" if include_inactive else "AND COALESCE(mi.ispublished, false) = true AND COALESCE(mi.isdeleted, false) = false"
    return q(f"""
        SELECT mi.id AS item_id,
               mi.restaurantsid AS restaurant_id,
               mi.menucategoryid AS category_id,
               COALESCE(NULLIF(mi.title_ar, ''), '') AS title_ar,
               COALESCE(NULLIF(mi.title_en, ''), 'item_' || mi.id) AS title_en,
               COALESCE(mi.ispublished, false) AS is_published,
               COALESCE(mi.isdeleted, false) AS is_deleted,
               COALESCE(mi.iscombo, false) AS is_combo
        FROM menuitem mi
        WHERE mi.restaurantsid = :restaurant_id
          {active_filter}
        ORDER BY mi.menucategoryid NULLS LAST, mi.title_en, mi.id
    """, restaurant_id=int(restaurant_id))


def fetch_restaurant_menu_with_sizes(restaurant_id: int, include_inactive: bool = False) -> pd.DataFrame:
    """Fetch a restaurant's menu and configured sizes directly from the DB.

    A menu item may have multiple sizes, so the result has one row per item-size
    pair. The API layer groups these rows into one item with a ``sizes`` array.
    """
    active_filter = "" if include_inactive else """
        AND COALESCE(mi.ispublished, false) = true
        AND COALESCE(mi.isdeleted, false) = false
        AND (isz.id IS NULL OR COALESCE(isz.isdeleted, false) = false)
    """
    return q(f"""
        WITH current_menu AS (
            SELECT rm.id
            FROM restaurantmenu rm
            WHERE rm.restaurantid = :restaurant_id
              AND COALESCE(rm.isdefault, false) = true
              AND COALESCE(rm.isactive, false) = true
              AND COALESCE(rm.ispuplish, false) = true
              AND COALESCE(rm.isdeleted, false) = false
            ORDER BY COALESCE(rm.updated, rm.created) DESC, rm.id DESC
            LIMIT 1
        )
        SELECT mi.id AS item_id,
               mi.restaurantsid AS restaurant_id,
               COALESCE(NULLIF(mi.title_ar, ''), '') AS title_ar,
               COALESCE(NULLIF(mi.title_en, ''), 'item_' || mi.id) AS title_en,
               mi.menucategoryid AS category_id,
               COALESCE(NULLIF(mc.title_ar, ''), '') AS category_ar,
               COALESCE(NULLIF(mc.title_en, ''), '') AS category_en,
               COALESCE(mi.ispublished, false) AS is_published,
               COALESCE(mi.isdeleted, false) AS is_deleted,
               COALESCE(mi.iscombo, false) AS is_combo,
               mi.calories,
               isz.id AS item_size_id,
               COALESCE(NULLIF(rs.title_ar, ''), NULLIF(sc.title_ar, ''), '') AS size_ar,
               COALESCE(NULLIF(rs.title_en, ''), NULLIF(sc.title_en, ''), '') AS size_en,
               rs.refrenceclassificationcode AS size_code,
               isz.price,
               isz.takeawayprice AS takeaway_price,
               COALESCE(isz.isdeleted, false) AS size_is_deleted,
               availability.settingsvalue AS availability_mode,
               availability.availabilityvalue AS availability_value,
               availability.curruntvalue AS current_availability_value
        FROM menuitem mi
        LEFT JOIN menucategory mc ON mc.id = mi.menucategoryid
        LEFT JOIN itemsizes isz ON isz.menuitemid = mi.id
        LEFT JOIN restaurantsizes rs ON rs.id = isz.restaurantsizesid
        LEFT JOIN sizeclassification sc ON sc.id = rs.sizeclassificationid
        LEFT JOIN LATERAL (
            SELECT ias.settingsvalue,
                   ias.availabilityvalue,
                   ias.curruntvalue
            FROM itemsavailabilitysettings ias
            JOIN current_menu cm ON cm.id = ias.menuid
            WHERE COALESCE(ias.isdeleted, false) = false
              AND ias.restaurantsizesid = isz.restaurantsizesid
              AND (ias.menuitemid = mi.id OR ias.menuitemid IS NULL)
            ORDER BY (ias.menuitemid IS NOT NULL) DESC,
                     COALESCE(ias.updated, ias.created) DESC,
                     ias.id DESC
            LIMIT 1
        ) availability ON true
        WHERE mi.restaurantsid = :restaurant_id
          {active_filter}
        ORDER BY mi.menucategoryid NULLS LAST, mi.title_en, mi.id, isz.id NULLS LAST
    """, restaurant_id=int(restaurant_id))
