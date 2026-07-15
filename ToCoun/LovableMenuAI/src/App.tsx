import { useEffect, useMemo, useState } from "react";
import { getRestaurantMenu, getRestaurants } from "./lib/menuApi";
import type { MenuItem, Restaurant, RestaurantMenuResponse } from "./types/menu";

const restaurantName = (restaurant: Restaurant) => restaurant.name_ar || restaurant.name || `مطعم #${restaurant.restaurant_id}`;
const itemName = (item: MenuItem) => item.title_ar || item.title_en || `صنف #${item.item_id}`;
const categoryName = (item: MenuItem) => item.category_ar || item.category_en || "بدون فئة";

function formatPrice(value: number | null) {
  return value == null ? "—" : new Intl.NumberFormat("ar-SA", { style: "currency", currency: "SAR" }).format(value);
}

export default function App() {
  const [restaurants, setRestaurants] = useState<Restaurant[]>([]);
  const [restaurantId, setRestaurantId] = useState<number | null>(null);
  const [menu, setMenu] = useState<RestaurantMenuResponse | null>(null);
  const [search, setSearch] = useState("");
  const [categoryId, setCategoryId] = useState<string>("all");
  const [includeInactive, setIncludeInactive] = useState(false);
  const [loadingRestaurants, setLoadingRestaurants] = useState(true);
  const [loadingMenu, setLoadingMenu] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    getRestaurants()
      .then((data) => {
        setRestaurants(data);
        const firstWithActiveMenu = data.find((restaurant) => restaurant.active_item_count > 0);
        if (firstWithActiveMenu || data.length) setRestaurantId((firstWithActiveMenu || data[0]).restaurant_id);
      })
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "تعذر تحميل المطاعم"))
      .finally(() => setLoadingRestaurants(false));
  }, []);

  useEffect(() => {
    if (restaurantId == null) return;
    setLoadingMenu(true);
    setError("");
    setCategoryId("all");
    getRestaurantMenu(restaurantId, includeInactive)
      .then(setMenu)
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "تعذر تحميل القائمة"))
      .finally(() => setLoadingMenu(false));
  }, [restaurantId, includeInactive]);

  const visibleItems = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase("ar");
    return (menu?.items || []).filter((item) => {
      if (categoryId !== "all" && String(item.category_id ?? "uncategorized") !== categoryId) return false;
      if (!needle) return true;
      return `${item.item_id} ${item.title_ar} ${item.title_en} ${item.category_ar} ${item.category_en}`
        .toLocaleLowerCase("ar")
        .includes(needle);
    });
  }, [categoryId, menu?.items, search]);

  return (
    <main className="page-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">BONTECH · LIVE DATABASE</p>
          <h1>قوائم المطاعم</h1>
          <p className="muted">البيانات تُسحب مباشرة من قاعدة البيانات عند اختيار المطعم.</p>
        </div>
        <div className="ai-ready">
          <span className="status-dot" />
          جاهز لربط AI
        </div>
      </header>

      <section className="control-panel" aria-label="اختيارات القائمة">
        <label>
          <span>المطعم</span>
          <select
            value={restaurantId ?? ""}
            disabled={loadingRestaurants || !restaurants.length}
            onChange={(event) => setRestaurantId(Number(event.target.value))}
          >
            {restaurants.map((restaurant) => (
              <option key={restaurant.restaurant_id} value={restaurant.restaurant_id}>
                {restaurantName(restaurant)} · #{restaurant.restaurant_id} · {restaurant.active_item_count} صنف
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>بحث</span>
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="اسم الصنف أو رقمه" />
        </label>
        <label className="toggle-label">
          <input type="checkbox" checked={includeInactive} onChange={(event) => setIncludeInactive(event.target.checked)} />
          <span>عرض غير المنشور والمحذوف</span>
        </label>
      </section>

      {error ? <div className="notice error">{error}</div> : null}
      {loadingRestaurants || loadingMenu ? <div className="notice">جارٍ تحميل البيانات الحية…</div> : null}

      {menu ? (
        <>
          <section className="menu-heading">
            <div>
              <h2>{restaurantName(menu.restaurant)}</h2>
              <p className="muted">{menu.count.toLocaleString("ar-SA")} صنف من قاعدة البيانات</p>
            </div>
            <span className="source-pill">LIVE DB</span>
          </section>

          <nav className="category-list" aria-label="فئات القائمة">
            <button className={categoryId === "all" ? "chip active" : "chip"} onClick={() => setCategoryId("all")}>
              الكل · {menu.count}
            </button>
            {menu.categories.map((category) => {
              const value = String(category.category_id ?? "uncategorized");
              return (
                <button key={value} className={categoryId === value ? "chip active" : "chip"} onClick={() => setCategoryId(value)}>
                  {category.title_ar || category.title_en || "بدون فئة"} · {category.count}
                </button>
              );
            })}
          </nav>

          <section className="menu-grid" aria-live="polite">
            {visibleItems.map((item) => (
              <article className="item-card" key={item.item_id}>
                <div className="card-topline">
                  <span className="item-id">#{item.item_id}</span>
                  {item.is_combo ? <span className="tag">كومبو</span> : null}
                  {item.is_deleted ? <span className="tag danger">محذوف</span> : null}
                  {!item.is_published ? <span className="tag muted-tag">غير منشور</span> : null}
                </div>
                <h3>{itemName(item)}</h3>
                {item.title_ar && item.title_en ? <p className="english-name">{item.title_en}</p> : null}
                <p className="category">{categoryName(item)}</p>
                {item.calories != null ? <p className="meta">{item.calories} سعرة</p> : null}
                <div className="sizes">
                  {item.sizes.length ? item.sizes.map((size) => (
                    <div className="size-row" key={size.item_size_id}>
                      <span>{size.title_ar || size.title_en || size.code || "حجم"}</span>
                      <strong>{formatPrice(size.price)}</strong>
                      {size.takeaway_price != null ? <small>تيك أواي {formatPrice(size.takeaway_price)}</small> : null}
                    </div>
                  )) : <span className="no-size">لا توجد أحجام أو أسعار مضافة</span>}
                </div>
              </article>
            ))}
          </section>
          {!visibleItems.length && !loadingMenu ? <div className="notice">لا توجد أصناف مطابقة.</div> : null}
        </>
      ) : null}
    </main>
  );
}
