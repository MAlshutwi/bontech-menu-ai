import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";
import { getOneRecommendation, getRestaurantMenu, getRestaurants } from "./lib/menuApi";
import type {
  CartLine,
  MenuItem,
  MenuSize,
  Restaurant,
  RestaurantMenuResponse,
  WidgetRecommendationItem,
} from "./types/menu";

const DEFAULT_RESTAURANT_ID = 277;
const QUICK_RESTAURANT_IDS = [260, 192];

const restaurantName = (restaurant: Restaurant) =>
  restaurant.name_ar || restaurant.name || `مطعم #${restaurant.restaurant_id}`;
const itemName = (item: MenuItem) => item.title_ar || item.title_en || `صنف #${item.item_id}`;
const categoryName = (item: MenuItem) => item.category_ar || item.category_en || "بدون فئة";
const sizeName = (size?: MenuSize) => size?.title_ar || size?.title_en || size?.code || "عادي";

function formatPrice(value: number | null) {
  if (value == null) return "السعر عند الطلب";
  return new Intl.NumberFormat("ar-SA", { style: "currency", currency: "SAR" }).format(value);
}

function sourceLabel(source: string) {
  return (
    {
      restaurant_fbt: "يُطلب غالبًا مع اختيارك",
      restaurant_popularity: "من الأكثر طلبًا",
      item2vec: "مشابه لاختيارك",
      pooled_fbt: "مناسب لسلتك",
      global_common: "اقتراح شائع",
    }[source] || "اختيار ذكي"
  );
}

function availabilityLabel(reason: string) {
  return (
    {
      out_of_stock: "نفد من المخزون",
      quantity_depleted: "نفدت الكمية",
      stock_depleted: "نفد من المخزون",
      all_sizes_unavailable: "جميع الأحجام نافدة",
    }[reason] || "غير متاح حاليًا"
  );
}

export default function App() {
  const [restaurants, setRestaurants] = useState<Restaurant[]>([]);
  const [restaurantId, setRestaurantId] = useState<number | null>(null);
  const [menu, setMenu] = useState<RestaurantMenuResponse | null>(null);
  const [cart, setCart] = useState<CartLine[]>([]);
  const [lastAddedItemId, setLastAddedItemId] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [categoryId, setCategoryId] = useState("all");
  const [restaurantPickerOpen, setRestaurantPickerOpen] = useState(false);
  const [widgetOpen, setWidgetOpen] = useState(true);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [widgetPosition, setWidgetPosition] = useState<{ left: number; top: number } | null>(null);
  const [recommendations, setRecommendations] = useState<WidgetRecommendationItem[]>([]);
  const [selectedRecommendationId, setSelectedRecommendationId] = useState<number | null>(null);
  const [thresholdFallbackUsed, setThresholdFallbackUsed] = useState(false);
  const [recommendationError, setRecommendationError] = useState("");
  const [loadingRestaurants, setLoadingRestaurants] = useState(true);
  const [loadingMenu, setLoadingMenu] = useState(false);
  const [loadingRecommendation, setLoadingRecommendation] = useState(false);
  const [menuRefreshToken, setMenuRefreshToken] = useState(0);
  const [recommendationRefreshToken, setRecommendationRefreshToken] = useState(0);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const widgetRef = useRef<HTMLElement>(null);
  const dragRef = useRef<{ pointerId: number; offsetX: number; offsetY: number } | null>(null);
  const activeRestaurantIdRef = useRef<number | null>(null);

  useEffect(() => {
    getRestaurants()
      .then((data) => {
        setRestaurants(data);
        const defaultRestaurant = data.find((restaurant) => restaurant.restaurant_id === DEFAULT_RESTAURANT_ID);
        const firstWithMenu = data.find((restaurant) => restaurant.active_item_count > 0);
        const initial = defaultRestaurant || firstWithMenu || data[0];
        if (initial) {
          activeRestaurantIdRef.current = initial.restaurant_id;
          setRestaurantId(initial.restaurant_id);
        }
      })
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "تعذر تحميل المطاعم"))
      .finally(() => setLoadingRestaurants(false));
  }, []);

  useEffect(() => {
    if (restaurantId == null) return;
    let cancelled = false;
    setLoadingMenu(true);
    setError("");
    setMenu(null);
    getRestaurantMenu(restaurantId, false)
      .then((payload) => {
        if (!cancelled) setMenu(payload);
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "تعذر تحميل القائمة");
      })
      .finally(() => {
        if (!cancelled) setLoadingMenu(false);
      });
    return () => {
      cancelled = true;
    };
  }, [restaurantId, menuRefreshToken]);

  const cartItemIds = useMemo(() => [...new Set(cart.map((line) => line.item_id))], [cart]);

  useEffect(() => {
    if (!restaurantId || menu?.restaurant.restaurant_id !== restaurantId) {
      setRecommendations([]);
      setRecommendationError("");
      setLoadingRecommendation(false);
      return;
    }

    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLoadingRecommendation(true);
      setRecommendationError("");
      Promise.all([
        getOneRecommendation(restaurantId, cartItemIds, lastAddedItemId, controller.signal),
        getRestaurantMenu(restaurantId, false),
      ])
        .then(([response, freshMenu]) => {
          if (controller.signal.aborted) return;
          setMenu(freshMenu);
          const liveItems = new Map(freshMenu.items.map((item) => [item.item_id, item]));
          const cartIds = new Set(cartItemIds);
          const cartCategories = new Set(
            cartItemIds
              .map((itemId) => liveItems.get(itemId)?.category_id)
              .filter((value): value is number => value != null),
          );
          const valid = (response.top_recommendations || []).filter((item) => {
            const liveItem = liveItems.get(item.item_id);
            if (!liveItem || !liveItem.is_available || item.addable === false || cartIds.has(item.item_id)) return false;
            return liveItem.category_id == null || !cartCategories.has(liveItem.category_id);
          });
          setRecommendations(valid);
          setThresholdFallbackUsed(Boolean(response.threshold_fallback_used));
          setSelectedRecommendationId(valid[0]?.item_id || null);
        })
        .catch((cause: unknown) => {
          if (cause instanceof DOMException && cause.name === "AbortError") return;
          setRecommendations([]);
          setRecommendationError(cause instanceof Error ? cause.message : "تعذر تحميل الاقتراح");
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoadingRecommendation(false);
        });
    }, 220);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [
    cartItemIds,
    lastAddedItemId,
    menu?.restaurant.restaurant_id,
    menuRefreshToken,
    recommendationRefreshToken,
    restaurantId,
  ]);

  const selectedRestaurant = useMemo(
    () => restaurants.find((restaurant) => restaurant.restaurant_id === restaurantId) || menu?.restaurant || null,
    [menu?.restaurant, restaurantId, restaurants],
  );

  const quickRestaurants = useMemo(
    () => QUICK_RESTAURANT_IDS.map((id) => restaurants.find((restaurant) => restaurant.restaurant_id === id)).filter(Boolean) as Restaurant[],
    [restaurants],
  );

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

  const cartTotal = useMemo(
    () => cart.reduce((total, line) => total + (line.price || 0) * line.quantity, 0),
    [cart],
  );
  const cartQuantity = useMemo(() => cart.reduce((total, line) => total + line.quantity, 0), [cart]);

  const recommendation = useMemo(
    () => recommendations.find((item) => item.item_id === selectedRecommendationId) || recommendations[0] || null,
    [recommendations, selectedRecommendationId],
  );
  const recommendationMenuItem = useMemo(
    () => menu?.items.find((item) => item.item_id === recommendation?.item_id) || null,
    [menu?.items, recommendation?.item_id],
  );
  const recommendationSize =
    recommendationMenuItem?.sizes.find((size) => !size.is_deleted && size.is_available)
    || recommendationMenuItem?.sizes.find((size) => !size.is_deleted);

  function changeRestaurant(nextId: number) {
    setRestaurantPickerOpen(false);
    if (!nextId || nextId === restaurantId) return;
    const hadCart = cart.length > 0;
    setCart([]);
    setLastAddedItemId(null);
    setRecommendations([]);
    setSelectedRecommendationId(null);
    setDetailsOpen(false);
    setRecommendationError("");
    setSearch("");
    setCategoryId("all");
    setMenu(null);
    activeRestaurantIdRef.current = nextId;
    setRestaurantId(nextId);
    setNotice(hadCart ? "تم تغيير المطعم وتصفير السلة." : "تم تغيير المطعم.");
  }

  function addToCart(item: MenuItem, size?: MenuSize) {
    if (!item.is_available || (size && !size.is_available)) {
      setRecommendationError(availabilityLabel(size?.availability_reason || item.availability_reason));
      return;
    }
    const key = `${item.item_id}:${size?.item_size_id || 0}`;
    setCart((current) => {
      const existing = current.find((line) => line.key === key);
      if (existing) {
        return current.map((line) => (line.key === key ? { ...line, quantity: line.quantity + 1 } : line));
      }
      return [
        ...current,
        {
          key,
          item_id: item.item_id,
          item_size_id: size?.item_size_id || null,
          title: itemName(item),
          size_title: sizeName(size),
          price: size?.price ?? null,
          quantity: 1,
        },
      ];
    });
    setLastAddedItemId(item.item_id);
    setWidgetOpen(true);
    setNotice("");
  }

  function changeQuantity(key: string, delta: number) {
    setCart((current) => {
      const next = current
        .map((line) => (line.key === key ? { ...line, quantity: line.quantity + delta } : line))
        .filter((line) => line.quantity > 0);
      const changed = next.find((line) => line.key === key);
      setLastAddedItemId(delta > 0 && changed ? changed.item_id : next.at(-1)?.item_id || null);
      return next;
    });
  }

  function removeCartLine(key: string) {
    setCart((current) => {
      const next = current.filter((line) => line.key !== key);
      setLastAddedItemId(next.at(-1)?.item_id || null);
      return next;
    });
  }

  function clearCart() {
    setCart([]);
    setLastAddedItemId(null);
    setRecommendations([]);
    setSelectedRecommendationId(null);
    setDetailsOpen(false);
    setRecommendationError("");
  }

  async function addRecommendationToCart() {
    if (!restaurantId || !recommendation || !recommendationMenuItem) return;
    const requestRestaurantId = restaurantId;
    const requestRecommendationId = recommendation.item_id;
    setLoadingRecommendation(true);
    setRecommendationError("");
    try {
      const freshMenu = await getRestaurantMenu(requestRestaurantId, false);
      if (activeRestaurantIdRef.current !== requestRestaurantId) return;
      setMenu(freshMenu);
      const liveItem = freshMenu.items.find((item) => item.item_id === requestRecommendationId);
      const preferredSizeId = recommendationSize?.item_size_id;
      const liveSize =
        liveItem?.sizes.find((size) => size.item_size_id === preferredSizeId && size.is_available)
        || liveItem?.sizes.find((size) => size.is_available);
      if (!liveItem?.is_available || (liveItem.sizes.length > 0 && !liveSize)) {
        setRecommendations((current) => current.filter((item) => item.item_id !== recommendation.item_id));
        setSelectedRecommendationId(null);
        setRecommendationError("هذا الاقتراح نفد من المخزون، جاري اختيار بديل متاح.");
        setRecommendationRefreshToken((token) => token + 1);
        return;
      }
      addToCart(liveItem, liveSize);
      setRecommendations((current) =>
        current.filter((item) =>
          item.item_id !== liveItem.item_id
          && (liveItem.category_id == null || item.category_id !== liveItem.category_id),
        ),
      );
      setSelectedRecommendationId(null);
      setDetailsOpen(false);
    } catch (cause: unknown) {
      if (activeRestaurantIdRef.current !== requestRestaurantId) return;
      setRecommendationError(cause instanceof Error ? cause.message : "تعذر التحقق من توفر الاقتراح");
    } finally {
      if (activeRestaurantIdRef.current === requestRestaurantId) {
        setLoadingRecommendation(false);
      }
    }
  }

  function beginWidgetDrag(event: ReactPointerEvent<HTMLElement>) {
    if ((event.target as HTMLElement).closest("button") || window.innerWidth <= 700 || !widgetRef.current) return;
    const rect = widgetRef.current.getBoundingClientRect();
    dragRef.current = {
      pointerId: event.pointerId,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function moveWidget(event: ReactPointerEvent<HTMLElement>) {
    if (!dragRef.current || dragRef.current.pointerId !== event.pointerId || !widgetRef.current) return;
    const margin = 10;
    const left = Math.max(margin, Math.min(window.innerWidth - widgetRef.current.offsetWidth - margin, event.clientX - dragRef.current.offsetX));
    const top = Math.max(margin, Math.min(window.innerHeight - widgetRef.current.offsetHeight - margin, event.clientY - dragRef.current.offsetY));
    setWidgetPosition({ left, top });
  }

  function endWidgetDrag(event: ReactPointerEvent<HTMLElement>) {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
  }

  const widgetStyle: CSSProperties | undefined = widgetPosition
    ? { left: widgetPosition.left, top: widgetPosition.top, right: "auto", bottom: "auto" }
    : undefined;

  return (
    <div className="restaurant-app" dir="rtl">
      <header className="topbar">
        <div className="brand-block">
          <span className="brand-mark" aria-hidden="true">✦</span>
          <div>
            <p className="eyebrow">BONTECH · LIVE MENU</p>
            <h1>{selectedRestaurant ? restaurantName(selectedRestaurant) : "جاري تحميل المطعم…"}</h1>
            <p className="muted">منيو حي مع اقتراح ذكي واحد داخل ويدجت.</p>
          </div>
        </div>
        <div className="status-pills">
          <span className="status-pill"><i /> قاعدة البيانات مباشرة</span>
          <span className="cart-pill">السلة <b>{cartQuantity}</b></span>
        </div>
      </header>

      <section className="location-bar" aria-label="اختيار المطعم">
        <button
          className="current-location"
          type="button"
          onClick={() => setRestaurantPickerOpen((open) => !open)}
          aria-expanded={restaurantPickerOpen}
        >
          <span>{restaurantId === DEFAULT_RESTAURANT_ID ? "المطعم الأساسي" : "المطعم الحالي"}</span>
          <strong>{selectedRestaurant ? restaurantName(selectedRestaurant) : "تحميل…"}</strong>
          <small>تغيير المطعم <b aria-hidden="true">⌄</b></small>
        </button>
        {quickRestaurants.map((restaurant) => (
          <button
            className={restaurant.restaurant_id === restaurantId ? "quick-location active" : "quick-location"}
            type="button"
            key={restaurant.restaurant_id}
            onClick={() => changeRestaurant(restaurant.restaurant_id)}
            disabled={loadingRestaurants || restaurant.restaurant_id === restaurantId}
          >
            <span>اختيار سريع</span>
            <strong>{restaurantName(restaurant)}</strong>
          </button>
        ))}
        <button className="refresh-button" type="button" onClick={() => setMenuRefreshToken((token) => token + 1)} disabled={!restaurantId || loadingMenu}>
          ↻ تحديث المنيو
        </button>
      </section>

      {restaurantPickerOpen ? (
        <section className="restaurant-picker" aria-label="قائمة كل المطاعم">
          <div>
            <strong>اختر مطعمًا آخر</strong>
            <p>عند اختيار مطعم مختلف سيتم تصفير السلة تلقائيًا.</p>
          </div>
          <select value={restaurantId ?? ""} onChange={(event) => changeRestaurant(Number(event.target.value))} autoFocus>
            {restaurants.map((restaurant) => (
              <option key={restaurant.restaurant_id} value={restaurant.restaurant_id}>
                {restaurantName(restaurant)} · {restaurant.active_item_count} صنف
              </option>
            ))}
          </select>
          <button className="ghost-button" type="button" onClick={() => setRestaurantPickerOpen(false)}>إغلاق</button>
        </section>
      ) : null}

      {notice ? <div className="notice success">{notice}</div> : null}
      {error ? <div className="notice error">{error}</div> : null}

      <nav className="category-list" aria-label="فئات القائمة">
        <button className={categoryId === "all" ? "category-chip active" : "category-chip"} onClick={() => setCategoryId("all")}>
          الكل <b>{menu?.count || 0}</b>
        </button>
        {(menu?.categories || []).map((category) => {
          const value = String(category.category_id ?? "uncategorized");
          return (
            <button key={value} className={categoryId === value ? "category-chip active" : "category-chip"} onClick={() => setCategoryId(value)}>
              {category.title_ar || category.title_en || "بدون فئة"} <b>{category.count}</b>
            </button>
          );
        })}
      </nav>

      <main className="workspace">
        <section className="menu-panel">
          <div className="panel-heading">
            <div>
              <h2>منيو المطعم</h2>
              <p>
                {menu
                  ? `${menu.items.filter((item) => item.is_available).length.toLocaleString("ar-SA")} متاح من ${menu.count.toLocaleString("ar-SA")} صنف`
                  : "تحميل المنيو الحي…"}
              </p>
            </div>
            <label className="search-box">
              <span aria-hidden="true">⌕</span>
              <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="ابحث عن صنف" />
            </label>
          </div>

          {loadingRestaurants || loadingMenu ? <div className="loading-state">جارٍ تحميل البيانات الحية…</div> : null}

          <div className="menu-grid" aria-live="polite">
            {visibleItems.map((item) => (
              <article className={item.is_available ? "item-card" : "item-card out-of-stock"} key={item.item_id}>
                <div className="item-card-head">
                  <span className="item-icon" aria-hidden="true">✦</span>
                  <span className={item.is_available ? "item-id" : "stock-badge"}>
                    {item.is_available ? `#${item.item_id}` : availabilityLabel(item.availability_reason)}
                  </span>
                </div>
                <h3>{itemName(item)}</h3>
                {item.title_ar && item.title_en ? <p className="english-name">{item.title_en}</p> : null}
                <p className="category-name">{categoryName(item)}</p>
                <div className="sizes">
                  {item.sizes.some((size) => !size.is_deleted) ? (
                    item.sizes.filter((size) => !size.is_deleted).map((size) => (
                      <div className={size.is_available ? "size-row" : "size-row unavailable"} key={size.item_size_id}>
                        <span>
                          <b>{sizeName(size)}</b>
                          <small>{size.is_available ? formatPrice(size.price) : availabilityLabel(size.availability_reason)}</small>
                        </span>
                        <button
                          type="button"
                          onClick={() => addToCart(item, size)}
                          aria-label={`أضف ${itemName(item)} ${sizeName(size)}`}
                          disabled={!size.is_available}
                        >
                          {size.is_available ? "+" : "×"}
                        </button>
                      </div>
                    ))
                  ) : (
                    <div className={item.is_available ? "size-row" : "size-row unavailable"}>
                      <span>
                        <b>عادي</b>
                        <small>{item.is_available ? "السعر عند الطلب" : availabilityLabel(item.availability_reason)}</small>
                      </span>
                      <button type="button" onClick={() => addToCart(item)} disabled={!item.is_available}>
                        {item.is_available ? "+" : "×"}
                      </button>
                    </div>
                  )}
                </div>
              </article>
            ))}
          </div>
          {!visibleItems.length && !loadingMenu ? <div className="loading-state">لا توجد أصناف مطابقة.</div> : null}
        </section>

        <aside className="cart-panel">
          <div className="cart-head">
            <div>
              <h2>السلة</h2>
              <p>{cartQuantity ? `${cartQuantity} عنصر` : "أضف من المنيو"}</p>
            </div>
            {cart.length ? <button type="button" onClick={clearCart}>مسح الكل</button> : null}
          </div>
          <div className="cart-body">
            {!cart.length ? (
              <div className="empty-cart">
                <span aria-hidden="true">⌑</span>
                <strong>السلة فارغة</strong>
                <p>يظهر الأكثر مبيعًا أولًا، وبعد إضافة صنف يتخصص الاقتراح حسب السلة.</p>
              </div>
            ) : (
              <div className="cart-lines">
                {cart.map((line) => (
                  <div className="cart-line" key={line.key}>
                    <div>
                      <strong>{line.title}</strong>
                      <span>{line.size_title} · {formatPrice(line.price)}</span>
                    </div>
                    <div className="quantity-control">
                      <button type="button" onClick={() => changeQuantity(line.key, 1)}>+</button>
                      <b>{line.quantity}</b>
                      <button type="button" onClick={() => changeQuantity(line.key, -1)}>−</button>
                    </div>
                    <button className="remove-line" type="button" onClick={() => removeCartLine(line.key)} aria-label="حذف الصنف">×</button>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="cart-total">
            <span>الإجمالي</span>
            <strong>{formatPrice(cartTotal)}</strong>
          </div>
        </aside>
      </main>

      {widgetOpen ? (
        <section className="ai-widget" ref={widgetRef} style={widgetStyle} aria-label="اقتراح ذكي واحد">
          <header
            className="widget-head"
            onPointerDown={beginWidgetDrag}
            onPointerMove={moveWidget}
            onPointerUp={endWidgetDrag}
            onPointerCancel={endWidgetDrag}
          >
            <div>
              <span aria-hidden="true">⋮⋮</span>
              <strong>اقتراح ذكي</strong>
              <small>{cartItemIds.length ? "مخصص حسب السلة" : "الأكثر مبيعًا كبداية"}</small>
            </div>
            <button type="button" onClick={() => setWidgetOpen(false)} aria-label="إغلاق الويدجت">×</button>
          </header>
          <div className="widget-body">
            {recommendationError ? <div className="widget-inline-error">{recommendationError}</div> : null}
            {loadingRecommendation ? (
              <div className="widget-empty"><span className="spinner" /> نجهّز اقتراحك…</div>
            ) : recommendation && recommendationMenuItem ? (
              <>
              <article className={recommendations.length > 1 ? "recommendation-card has-stack" : "recommendation-card"}>
                <span className="ai-badge">AI</span>
                <div>
                  <h3>{itemName(recommendationMenuItem)}</h3>
                  <div className="recommendation-meta">
                    <span className="type-badge">{recommendation.type_label_ar}</span>
                    <span className={recommendation.meets_threshold ? "match-badge strong" : "match-badge"}>
                      توافق {recommendation.compatibility_percent.toLocaleString("ar-SA")}%
                    </span>
                  </div>
                  <p>{recommendation.reason || sourceLabel(recommendation.source)}</p>
                  <small>{sizeName(recommendationSize)} · {formatPrice(recommendationSize?.price ?? null)}</small>
                </div>
                <button type="button" onClick={addRecommendationToCart}>+ أضف</button>
              </article>
              {recommendations.length ? (
                <button
                  className="recommendation-list-toggle"
                  type="button"
                  aria-expanded={detailsOpen}
                  onClick={() => setDetailsOpen((open) => !open)}
                >
                  <span>{detailsOpen ? "إخفاء قائمة الاقتراحات" : `عرض قائمة الاقتراحات (${recommendations.length})`}</span>
                  <b aria-hidden="true">{detailsOpen ? "⌃" : "⌄"}</b>
                </button>
              ) : null}
              {detailsOpen ? (
                <section className="recommendation-list" aria-label="أفضل الاقتراحات">
                  <div className="recommendation-list-head">
                    <strong>{thresholdFallbackUsed ? "أفضل الاقتراحات المتاحة" : "الاقتراحات التي تجاوزت 70%"}</strong>
                    <span>مرتبة بعد فحص المخزون والقسم</span>
                  </div>
                  {recommendations.map((item, index) => {
                    const liveItem = menu?.items.find((candidate) => candidate.item_id === item.item_id);
                    if (!liveItem) return null;
                    return (
                      <button
                        type="button"
                        className={item.item_id === recommendation.item_id ? "recommendation-list-row active" : "recommendation-list-row"}
                        key={item.item_id}
                        onClick={() => {
                          setSelectedRecommendationId(item.item_id);
                          setDetailsOpen(false);
                        }}
                      >
                        <span className="rank-number">{index + 1}</span>
                        <span className="list-copy">
                          <strong>{itemName(liveItem)}</strong>
                          <small>{item.type_label_ar}</small>
                          <i><b style={{ width: `${item.compatibility_percent}%` }} /></i>
                        </span>
                        <span className="list-percent">{item.compatibility_percent.toLocaleString("ar-SA")}%</span>
                      </button>
                    );
                  })}
                  <p className="compatibility-note">نسبة التوافق ترتيبية داخل نوع الاقتراح وليست احتمال شراء.</p>
                </section>
              ) : null}
              </>
            ) : (
              <div className="widget-empty error-copy">
                <span>لا يوجد اقتراح متاح بعد فلترة المخزون وأقسام السلة.</span>
                <button type="button" onClick={() => setRecommendationRefreshToken((token) => token + 1)}>إعادة المحاولة</button>
              </div>
            )}
          </div>
        </section>
      ) : (
        <button className="widget-toggle" type="button" onClick={() => setWidgetOpen(true)}>
          <span>✦</span> اقتراح ذكي {recommendations.length ? <b>{recommendations.length}</b> : null}
        </button>
      )}
    </div>
  );
}
