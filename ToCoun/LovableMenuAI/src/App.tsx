import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";
import {
  getRecommendationModels,
  getRestaurantItemAvailability,
  getRestaurantMenu,
  getRestaurants,
} from "./lib/menuApi";
import type {
  CartLine,
  MenuItem,
  MenuItemAvailabilityResponse,
  MenuSize,
  Restaurant,
  RestaurantMenuResponse,
  WidgetRecommendationItem,
  WidgetRecommendationModelGroup,
} from "./types/menu";

const DEFAULT_RESTAURANT_ID = 277;
const QUICK_RESTAURANT_IDS = [260, 192];
type RecommendationModelKey = WidgetRecommendationModelGroup["model_key"];

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

function mergeItemAvailability(
  item: MenuItem,
  availability: MenuItemAvailabilityResponse,
): MenuItem {
  const liveSizes = new Map(availability.sizes.map((size) => [size.item_size_id, size]));
  const mergedSizes = item.sizes.map((size) => {
    const liveSize = liveSizes.get(size.item_size_id);
    if (!liveSize) {
      return {
        ...size,
        is_available: false,
        availability_reason: "size_not_in_live_menu",
      };
    }
    liveSizes.delete(size.item_size_id);
    return { ...size, ...liveSize, is_deleted: size.is_deleted };
  });
  for (const liveSize of liveSizes.values()) {
    mergedSizes.push({ ...liveSize, is_deleted: false });
  }
  return {
    ...item,
    category_id: availability.category_id,
    is_available: availability.is_available,
    availability_reason: availability.availability_reason,
    available_size_count: availability.available_size_count,
    sizes: mergedSizes,
  };
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
  const [modelGroups, setModelGroups] = useState<WidgetRecommendationModelGroup[]>([]);
  const [activeModelKey, setActiveModelKey] = useState<RecommendationModelKey>("ensemble");
  const [selectedRecommendationId, setSelectedRecommendationId] = useState<number | null>(null);
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
  const activeCartSignatureRef = useRef("");

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
  const cartSignature = useMemo(
    () => cart.map((line) => `${line.key}:${line.quantity}`).sort().join("|"),
    [cart],
  );
  activeCartSignatureRef.current = cartSignature;

  useEffect(() => {
    if (!restaurantId || menu?.restaurant.restaurant_id !== restaurantId) {
      setModelGroups([]);
      setRecommendationError("");
      setLoadingRecommendation(false);
      return;
    }

    setLoadingRecommendation(true);
    setRecommendationError("");
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      getRecommendationModels(restaurantId, cartItemIds, lastAddedItemId, controller.signal)
        .then((response) => {
          if (controller.signal.aborted) return;
          const liveItems = new Map(menu.items.map((item) => [item.item_id, item]));
          const cartIds = new Set(cartItemIds);
          const cartCategories = new Set(
            cartItemIds
              .map((itemId) => liveItems.get(itemId)?.category_id)
              .filter((value): value is number => value != null),
          );
          const responseGroups = response.models?.length
            ? response.models
            : [{
                model_key: "ensemble" as const,
                label_ar: "المزيج الذكي",
                description_ar: "أفضل الاقتراحات المتاحة",
                available: Boolean(response.top_recommendations?.length),
                threshold_fallback_used: Boolean(response.threshold_fallback_used),
                suggestions: response.top_recommendations || [],
              }];
          const validGroups = responseGroups.map((group) => {
            const seen = new Set<number>();
            const suggestions = (group.suggestions || []).filter((item) => {
              const liveItem = liveItems.get(item.item_id);
              if (
                seen.has(item.item_id)
                || !liveItem
                || !liveItem.is_available
                || item.addable === false
                || cartIds.has(item.item_id)
              ) return false;
              if (liveItem.category_id != null && cartCategories.has(liveItem.category_id)) return false;
              seen.add(item.item_id);
              return true;
            });
            return { ...group, available: suggestions.length > 0, suggestions };
          });
          setModelGroups(validGroups);
          setActiveModelKey((current) => {
            if (validGroups.some((group) => group.model_key === current && group.available)) return current;
            const preferred = validGroups.find(
              (group) => group.model_key === response.default_model_key && group.available,
            );
            return preferred?.model_key || validGroups.find((group) => group.available)?.model_key || "ensemble";
          });
          setSelectedRecommendationId((current) =>
            current != null
            && validGroups.some((group) => group.suggestions.some((item) => item.item_id === current))
              ? current
              : null,
          );
        })
        .catch((cause: unknown) => {
          if (cause instanceof DOMException && cause.name === "AbortError") return;
          setRecommendationError(cause instanceof Error ? cause.message : "تعذر تحميل الاقتراح");
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoadingRecommendation(false);
        });
    }, 100);

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

  const activeModelGroup = useMemo(
    () =>
      modelGroups.find((group) => group.model_key === activeModelKey && group.available)
      || modelGroups.find((group) => group.model_key === "ensemble" && group.available)
      || modelGroups.find((group) => group.available)
      || null,
    [activeModelKey, modelGroups],
  );
  const recommendations = activeModelGroup?.suggestions || [];
  const availableModelCount = modelGroups.filter(
    (group) => group.available && group.model_key !== "ensemble",
  ).length;
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
  const recommendationScore = Math.round(recommendation?.compatibility_percent || 0);

  function changeRestaurant(nextId: number) {
    setRestaurantPickerOpen(false);
    if (!nextId || nextId === restaurantId) return;
    const hadCart = cart.length > 0;
    setCart([]);
    setLastAddedItemId(null);
    setModelGroups([]);
    setActiveModelKey("ensemble");
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
    setModelGroups([]);
    setActiveModelKey("ensemble");
    setSelectedRecommendationId(null);
    setDetailsOpen(false);
    setRecommendationError("");
  }

  function removeModelSuggestions(shouldRemove: (item: WidgetRecommendationItem) => boolean) {
    setModelGroups((current) =>
      current.map((group) => {
        const suggestions = group.suggestions.filter((item) => !shouldRemove(item));
        return { ...group, suggestions, available: suggestions.length > 0 };
      }),
    );
    setActiveModelKey("ensemble");
    setSelectedRecommendationId(null);
  }

  async function addRecommendationToCart() {
    if (!restaurantId || !recommendation || !recommendationMenuItem) return;
    const requestRestaurantId = restaurantId;
    const requestRecommendationId = recommendation.item_id;
    const requestMenuItem = recommendationMenuItem;
    const preferredSizeId = recommendationSize?.item_size_id;
    const requestCartSignature = cartSignature;
    setLoadingRecommendation(true);
    setRecommendationError("");
    try {
      const availability = await getRestaurantItemAvailability(
        requestRestaurantId,
        requestRecommendationId,
      );
      if (
        activeRestaurantIdRef.current !== requestRestaurantId
        || activeCartSignatureRef.current !== requestCartSignature
      ) return;
      const liveItem = mergeItemAvailability(requestMenuItem, availability);
      setMenu((current) => {
        if (!current || current.restaurant.restaurant_id !== requestRestaurantId) return current;
        return {
          ...current,
          items: current.items.map((item) => (
            item.item_id === requestRecommendationId ? liveItem : item
          )),
        };
      });
      const liveSize =
        liveItem.sizes.find((size) => size.item_size_id === preferredSizeId && size.is_available)
        || liveItem.sizes.find((size) => size.is_available);
      if (!liveItem.is_available || (liveItem.sizes.length > 0 && !liveSize)) {
        removeModelSuggestions((item) => item.item_id === requestRecommendationId);
        setRecommendationError("هذا الاقتراح نفد من المخزون، جاري اختيار بديل متاح.");
        setRecommendationRefreshToken((token) => token + 1);
        return;
      }
      addToCart(liveItem, liveSize);
      removeModelSuggestions(
        (item) =>
          item.item_id === liveItem.item_id
          || (liveItem.category_id != null && item.category_id === liveItem.category_id),
      );
      setDetailsOpen(false);
    } catch (cause: unknown) {
      if (
        activeRestaurantIdRef.current !== requestRestaurantId
        || activeCartSignatureRef.current !== requestCartSignature
      ) return;
      setRecommendationError(cause instanceof Error ? cause.message : "تعذر التحقق من توفر الاقتراح");
    } finally {
      if (
        activeRestaurantIdRef.current === requestRestaurantId
        && activeCartSignatureRef.current === requestCartSignature
      ) {
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
            <p className="muted">منيو حي مع عدة محركات اقتراح قابلة للفرز داخل ويدجت.</p>
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
        <section className="ai-widget" ref={widgetRef} style={widgetStyle} aria-label="محركات الاقتراح الذكية">
          <header
            className="widget-head"
            onPointerDown={beginWidgetDrag}
            onPointerMove={moveWidget}
            onPointerUp={endWidgetDrag}
            onPointerCancel={endWidgetDrag}
          >
             <div>
               <span aria-hidden="true">⋮⋮</span>
               <strong>محركات الاقتراح</strong>
               <small>
                 {cartItemIds.length
                   ? `${availableModelCount.toLocaleString("ar-SA")} محركات تحلل السلة`
                   : "الأكثر طلبًا إلى أن تضيف للسلة"}
               </small>
             </div>
            <button type="button" onClick={() => setWidgetOpen(false)} aria-label="إغلاق الويدجت">×</button>
          </header>
           <div className="widget-body">
             {recommendationError ? <div className="widget-inline-error">{recommendationError}</div> : null}
             {modelGroups.length ? (
               <nav className="model-filters" aria-label="فرز الاقتراحات حسب المحرك">
                 {modelGroups.map((group) => (
                   <button
                     type="button"
                     key={group.model_key}
                     className={activeModelGroup?.model_key === group.model_key ? "active" : ""}
                     disabled={!group.available}
                     aria-pressed={activeModelGroup?.model_key === group.model_key}
                     onClick={() => {
                       setActiveModelKey(group.model_key);
                       setSelectedRecommendationId(null);
                     }}
                     title={group.description_ar}
                   >
                     <span>{group.label_ar}</span>
                     <b>{group.suggestions.length.toLocaleString("ar-SA")}</b>
                   </button>
                 ))}
               </nav>
             ) : null}
             {loadingRecommendation && !(recommendation && recommendationMenuItem) ? (
               <div className="widget-empty"><span className="spinner" /> نجهّز اقتراحك…</div>
             ) : recommendation && recommendationMenuItem ? (
               <>
               <article className={recommendations.length > 1 ? "recommendation-card has-stack" : "recommendation-card"}>
                 <span className="ai-badge">AI</span>
                 <div className="recommendation-copy">
                   <h3>{itemName(recommendationMenuItem)}</h3>
                   <div className="recommendation-meta">
                     <span className="type-badge">{recommendation.model_label_ar}</span>
                     <span className={recommendation.meets_threshold ? "match-badge strong" : "match-badge"}>
                       {recommendation.confidence_band_ar}
                     </span>
                   </div>
                   <p>{recommendation.reason || sourceLabel(recommendation.source)}</p>
                   {activeModelGroup?.model_key === "ensemble" && recommendation.model_agreement_count > 1 ? (
                     <span className="agreement-badge">
                       متفق عليه من {recommendation.model_agreement_count.toLocaleString("ar-SA")} محركات
                     </span>
                   ) : null}
                   <small>{sizeName(recommendationSize)} · {formatPrice(recommendationSize?.price ?? null)}</small>
                 </div>
                 <div className="recommendation-actions">
                   <div
                     className={recommendation.meets_threshold ? "confidence-ring strong" : "confidence-ring"}
                     style={{ "--score": recommendationScore } as CSSProperties}
                     role="meter"
                     aria-valuemin={0}
                     aria-valuemax={97}
                     aria-valuenow={recommendationScore}
                     aria-label={`${recommendation.score_label_ar} ${recommendationScore}%`}
                   >
                     <strong>{recommendationScore.toLocaleString("ar-SA")}%</strong>
                     <small>{recommendation.score_label_ar}</small>
                   </div>
                   <button type="button" onClick={addRecommendationToCart} disabled={loadingRecommendation}>
                     {loadingRecommendation ? "جاري التحديث…" : "+ أضف"}
                   </button>
                 </div>
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
                     <strong>{activeModelGroup?.label_ar || "أفضل الاقتراحات"}</strong>
                     <span>
                       {activeModelGroup?.threshold_fallback_used
                         ? "لا توجد نتائج فوق 70% لهذا المحرك؛ نعرض أفضل المتاح."
                         : activeModelGroup?.description_ar || "مرتبة بعد فحص المخزون والقسم"}
                     </span>
                   </div>
                   {recommendations.map((item, index) => {
                    const liveItem = menu?.items.find((candidate) => candidate.item_id === item.item_id);
                    if (!liveItem) return null;
                    return (
                       <button
                         type="button"
                         className={item.item_id === recommendation.item_id ? "recommendation-list-row active" : "recommendation-list-row"}
                         key={`${activeModelGroup?.model_key}:${item.model_key}:${item.item_id}`}
                         onClick={() => {
                           setSelectedRecommendationId(item.item_id);
                         }}
                       >
                        <span className="rank-number">{index + 1}</span>
                         <span className="list-copy">
                           <strong>{itemName(liveItem)}</strong>
                           <small>{item.model_label_ar} · {item.confidence_band_ar}</small>
                           <i><b style={{ width: `${item.compatibility_percent}%` }} /></i>
                         </span>
                         <span className="list-percent">{Math.round(item.compatibility_percent).toLocaleString("ar-SA")}%</span>
                       </button>
                    );
                  })}
                   <p className="compatibility-note">درجة الملاءمة محسوبة لكل محرك على حدة، وليست وعدًا بنسبة شراء مؤكدة.</p>
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
