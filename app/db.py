"""
app/db.py - read-only database access and standard cleaned query helpers.
All helpers are SELECT-only. No production database writes.
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import create_engine, text

from .config import CLEANING, db_url

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        # Enforce read-only behavior at the database session level.
        _engine = create_engine(
            db_url(),
            pool_pre_ping=True,
            connect_args={"options": "-c default_transaction_read_only=on"},
        )
    return _engine


def q(sql: str, **params) -> pd.DataFrame:
    """Execute a single SELECT/WITH statement and return a DataFrame."""
    stripped = sql.strip().rstrip(";").strip()
    head = stripped[:6].upper()
    if not (head.startswith("SELECT") or head.startswith("WITH")):
        raise ValueError("q() only permits SELECT/WITH statements")
    if ";" in stripped:
        raise ValueError("q() rejects multiple SQL statements")
    with get_engine().connect() as connection:
        return pd.read_sql(text(sql), connection, params=params)


def _clean_where() -> str:
    conditions = []
    if CLEANING.get("exclude_canceled_orders", True):
        conditions.append("ro.canceledate IS NULL")
    if CLEANING.get("exclude_deleted_orders", True):
        conditions.append("ro.isdeleted = false")
    if CLEANING.get("exclude_deleted_lines", True):
        conditions.append("mir.isdeleted = false")
    if CLEANING.get("exclude_wasted_lines", True):
        conditions.append("mir.iswasted = false")
    if CLEANING.get("exclude_recalled_lines", True):
        conditions.append("mir.isrecalled = false")
    if CLEANING.get("main_items_only", True):
        conditions.append("mir.parentid IS NULL")
    return " AND ".join(conditions) if conditions else "TRUE"


def clean_lines_sql() -> str:
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
    return q(clean_lines_sql())


def fetch_clean_lines_cached(refresh: bool = False) -> pd.DataFrame:
    from .config import ARTIFACTS

    path = ARTIFACTS / "_clean_lines.parquet"
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    frame = fetch_clean_lines()
    frame.to_parquet(path, index=False)
    return frame


def fetch_order_customer_map() -> pd.DataFrame:
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
               COALESCE(NULLIF(title_ar,''), '') AS title_ar,
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
    active_filter = (
        ""
        if include_inactive
        else "AND COALESCE(mi.ispublished, false) = true "
        "AND COALESCE(mi.isdeleted, false) = false"
    )
    return q(
        f"""
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
    """,
        restaurant_id=int(restaurant_id),
    )


def fetch_restaurant_item_availability(restaurant_id: int, item_id: int) -> pd.DataFrame:
    """Fetch a fresh availability snapshot for one active item and its sizes."""
    return q(
        """
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
        SELECT r.id AS restaurant_id,
               mi.id AS item_id,
               mi.menucategoryid AS category_id,
               isz.id AS item_size_id,
               COALESCE(NULLIF(rs.title_ar, ''), NULLIF(sc.title_ar, ''), '') AS size_ar,
               COALESCE(NULLIF(rs.title_en, ''), NULLIF(sc.title_en, ''), '') AS size_en,
               rs.refrenceclassificationcode AS size_code,
               isz.price,
               isz.takeawayprice AS takeaway_price,
               availability.settingsvalue AS availability_mode,
               availability.availabilityvalue AS availability_value,
               availability.curruntvalue AS current_availability_value
        FROM restaurants r
        LEFT JOIN menuitem mi
               ON mi.restaurantsid = r.id
              AND mi.id = :item_id
              AND COALESCE(mi.ispublished, false) = true
              AND COALESCE(mi.isdeleted, false) = false
        LEFT JOIN itemsizes isz
               ON isz.menuitemid = mi.id
              AND COALESCE(isz.isdeleted, false) = false
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
        WHERE r.id = :restaurant_id
        ORDER BY isz.id NULLS LAST
    """,
        restaurant_id=int(restaurant_id),
        item_id=int(item_id),
    )


def fetch_restaurant_menu_with_sizes(
    restaurant_id: int,
    include_inactive: bool = False,
) -> pd.DataFrame:
    """Fetch restaurant metadata, menu items, sizes, and live availability."""
    item_active_filter = "" if include_inactive else """
              AND COALESCE(mi.ispublished, false) = true
              AND COALESCE(mi.isdeleted, false) = false
    """
    size_active_filter = "" if include_inactive else """
              AND COALESCE(isz.isdeleted, false) = false
    """
    return q(
        f"""
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
        SELECT r.id AS restaurant_id,
               COALESCE(NULLIF(r.title_en, ''), r.shortname, 'rest_' || r.id) AS restaurant_name,
               COALESCE(NULLIF(r.title_ar, ''), '') AS restaurant_name_ar,
               mi.id AS item_id,
               COALESCE(NULLIF(mi.title_ar, ''), '') AS title_ar,
               CASE
                   WHEN mi.id IS NULL THEN ''
                   ELSE COALESCE(NULLIF(mi.title_en, ''), 'item_' || mi.id)
               END AS title_en,
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
        FROM restaurants r
        LEFT JOIN menuitem mi
               ON mi.restaurantsid = r.id
              {item_active_filter}
        LEFT JOIN menucategory mc ON mc.id = mi.menucategoryid
        LEFT JOIN itemsizes isz
               ON isz.menuitemid = mi.id
              {size_active_filter}
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
        WHERE r.id = :restaurant_id
        ORDER BY mi.menucategoryid NULLS LAST, mi.title_en, mi.id, isz.id NULLS LAST
    """,
        restaurant_id=int(restaurant_id),
    )
