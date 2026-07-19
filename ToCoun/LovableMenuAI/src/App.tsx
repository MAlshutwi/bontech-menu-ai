import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";
import {
  getRecommendationModels,
  getRestaurantItemAvailability,
  getRestaurantMenu,
  getRestaurants,
  sendRecommendationEvent,
} from "./lib/menuApi";
import type {
  CartLine,
  MenuItem,
  MenuItemAvailabilityResponse,
  MenuSize,
  Restaurant,
  RestaurantMenuResponse,
  RecommendationEventType,
  WidgetRecommendationItem,
  WidgetRecommendationModelGroup,
} from "./types/menu";

const DEFAULT_RESTAURANT_ID = 192;
const QUICK_RESTAURANT_IDS = [260, 277];
type RecommendationModelKey = WidgetRecommendationModelGroup["model_key"];

interface RecommendationTrace {
  requestId: string;
  modelVersion: string;
  restaurantId: number;
  cartItemKey: string;
}

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
      time_popularity: "الأكثر طلبًا في هذه الفترة",
      restaurant_time_popularity: "الأكثر طلبًا في هذه الفترة",
      item2vec: "مشابه لاختيارك",
      pooled_fbt: "مناسب لسلتك",
      global_common: "اقتراح شائع",
      live_menu_fallback: "اختيار متاح من المنيو",
    }[source] || "اختيار ذكي"
  );
}

const MODEL_LABELS_AR: Record<string, string> = {
  ensemble: "المزيج الذكي",
  full_cart: "مودل السلة كاملة",
  cart: "مودل السلة كاملة",
  last_item: "مودل آخر صنف",
  similarity: "مودل التشابه",
  popularity: "مودل الأكثر مبيعًا",
  global_popularity: "مودل الأكثر مبيعًا",
  time_context: "مودل الفترة الزمنية",
  time_popularity: "مودل الأكثر مبيعًا حسب الوقت",
  time_aware_popularity: "الأكثر مبيعًا حسب الفترة الزمنية",
  restaurant_popularity: "الأكثر مبيعًا في المطعم",
  fbt_confidence: "ارتباط السلة حسب الثقة",
  fbt_hybrid: "ارتباط السلة الهجين",
  fbt_paircount: "ارتباط السلة حسب التكرار",
  fbt_lift: "ارتباط السلة حسب الرفع",
  item2vec: "مودل تشابه الأصناف",
  pooled_fbt: "ارتباط السلة العام",
  live_menu_fallback: "اختيار متاح من المنيو",
  personalized: "مودل المستخدم",
};

const TIME_PERIODS_AR: Record<string, string> = {
  morning: "الصباح",
  breakfast: "الصباح",
  noon: "الظهر",
  lunch: "الظهر",
  afternoon: "العصر",
  evening: "المساء",
  night: "الليل",
  dinner: "الليل",
  late_night: "آخر الليل",
};

function uniqueArabicLabels(values: string[]) {
  const seen = new Set<string>();
  return values.map((value) => value.trim()).filter((normalized) => {
    if (!normalized) return false;
    const key = normalized.toLocaleLowerCase("ar");
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function stringLabels(value: string[] | string | null | undefined) {
  if (Array.isArray(value)) return uniqueArabicLabels(value.filter((label) => typeof label === "string"));
  if (typeof value !== "string") return [];
  return uniqueArabicLabels(value.split(/\s*\+\s*/));
}

function modelKeyLabel(value: string) {
  const normalized = value.trim();
  return MODEL_LABELS_AR[normalized] || normalized;
}

function recommendationProvenance(item: WidgetRecommendationItem) {
  const contributors = uniqueArabicLabels(
    (item.contributing_models || []).map((contributor) => {
      if (typeof contributor === "string") return modelKeyLabel(contributor);
      const exactLabel = contributor.model_label_ar
        || contributor.label_ar
        || contributor.source_label_ar
        || contributor.model_key
        || contributor.source
        || "";
      return modelKeyLabel(exactLabel);
    }),
  );
  if (!contributors.length && item.model_label_ar) contributors.push(item.model_label_ar);

  const labels = stringLabels(item.source_labels_ar);
  if (!labels.length) labels.push(sourceLabel(item.source));

  const timePeriod = item.time_period_ar?.trim();
  if (timePeriod) {
    const translatedPeriod = TIME_PERIODS_AR[timePeriod] || timePeriod;
    if (!labels.some((label) => label.includes(translatedPeriod) || label.includes("الفترة"))) {
      labels.push(`مناسب لفترة ${translatedPeriod}`);
    }
  }

  if (
    item.recommendation_context === "based_on_cart"
    && !labels.some((label) => label.includes("سلت"))
  ) {
    labels.push("حسب السلة");
  }

  return {
    models: uniqueArabicLabels(contributors),
    sources: uniqueArabicLabels(labels),
  };
}

function validatedModelAccuracy(item: WidgetRecommendationItem) {
  const percent = item.model_accuracy_percent;
  if (typeof percent !== "number" || !Number.isFinite(percent) || percent < 0 || percent > 100) return null;

  const rawMetric = item.accuracy_metric ?? item.model_accuracy_metric;
  if (!rawMetric) return null;
  if (typeof rawMetric === "object" && rawMetric.validated !== true) return null;

  const metricName = typeof rawMetric === "string"
    ? rawMetric.trim()
    : (rawMetric.label_ar || rawMetric.name || rawMetric.key || "").trim();
  if (!metricName) return null;

  const normalizedMetric = metricName.toLocaleLowerCase("en");
  const recallMetric = normalizedMetric.match(/recall@(\d+)/);
  const label = recallMetric
    ? `Recall@${recallMetric[1]} الموثّق`
    : normalizedMetric.includes("accuracy")
      ? "دقة المودل الموثّقة"
      : "نتيجة المودل الموثّقة";
  return { percent: Math.round(percent * 10) / 10, metricName, label };
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

function getOrCreateSessionId() {
  const storageKey = "bontech-menu-session";
  try {
    const existing = window.sessionStorage.getItem(storageKey);
    if (existing) return existing;
    const created = window.crypto.randomUUID();
    window.sessionStorage.setItem(storageKey, created);
    return created;
  } catch {
    return window.crypto.randomUUID();
  }
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
  const [activeModelKey, setActiveModelKey] = useState<RecommendationModelKey>("popularity");
  const [selectedRecommendationId, setSelectedRecommendationId] = useState<number | null>(null);
  const [recommendationError, setRecommendationError] = useState("");
  const [loadingRestaurants, setLoadingRestaurants] = useState(true);
  const [loadingMenu, setLoadingMenu] = useState(false);
  const [loadingRecommendation, setLoadingRecommendation] = useState(false);
  const [addingRecommendation, setAddingRecommendation] = useState(false);
  const [menuRefreshToken, setMenuRefreshToken] = useState(0);
  const [menuRevision, setMenuRevision] = useState(0);
  const [recommendationRefreshToken, setRecommendationRefreshToken] = useState(0);
  const [recommendationTrace, setRecommendationTrace] = useState<RecommendationTrace | null>(null);
  const [busyCartKeys, setBusyCartKeys] = useState<Set<string>>(new Set());
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [sessionId] = useState(getOrCreateSessionId);

  const widgetRef = useRef<HTMLElement>(null);
  const dragRef = useRef<{ pointerId: number; offsetX: number; offsetY: number } | null>(null);
  const activeRestaurantIdRef = useRef<number | null>(null);
  const activeCartSignatureRef = useRef("");
  const activeCartItemKeyRef = useRef("");
  const lastVisibleRecommendationIdRef = useRef<number | null>(null);
  const cartRef = useRef<CartLine[]>([]);
  const lastAddedHistoryRef = useRef<number[]>([]);
  const forceFreshMenuRef = useRef(false);
  const recommendationRequestRef = useRef(0);
  const cartMutationEpochRef = useRef(0);
  const busyCartKeysRef = useRef(new Set<string>());
  const shownEventsRef = useRef(new Set<string>());

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
    const fresh = forceFreshMenuRef.current;
    forceFreshMenuRef.current = false;
    setLoadingMenu(true);
    setError("");
    getRestaurantMenu(restaurantId, false, fresh)
      .then((payload) => {
        if (cancelled) return;
        reconcileCartWithMenu(payload);
        setMenu(payload);
        setMenuRevision((revision) => revision + 1);
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
  const cartItemKey = useMemo(() => [...cartItemIds].sort((a, b) => a - b).join(","), [cartItemIds]);
  const cartSignature = useMemo(
    () => cart.map((line) => `${line.key}:${line.quantity}`).sort().join("|"),
    [cart],
  );
  cartRef.current = cart;
  activeCartSignatureRef.current = cartSignature;
  activeCartItemKeyRef.current = cartItemKey;

  useEffect(() => {
    if (!restaurantId || menu?.restaurant.restaurant_id !== restaurantId) {
      setModelGroups([]);
      setRecommendationTrace(null);
      setRecommendationError("");
      setLoadingRecommendation(false);
      return;
    }

    const requestNumber = ++recommendationRequestRef.current;
    const requestRestaurantId = restaurantId;
    const requestCartItemKey = cartItemKey;
    const requestMenu = menu;
    setModelGroups([]);
    setRecommendationTrace(null);
    setSelectedRecommendationId(null);
    setLoadingRecommendation(true);
    setRecommendationError("");
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      const previousTopItemId = lastVisibleRecommendationIdRef.current;
      getRecommendationModels(
        requestRestaurantId,
        cartItemIds,
        lastAddedItemId,
        previousTopItemId,
        controller.signal,
      )
        .then((response) => {
          if (
            controller.signal.aborted
            || recommendationRequestRef.current !== requestNumber
            || activeRestaurantIdRef.current !== requestRestaurantId
            || activeCartItemKeyRef.current !== requestCartItemKey
          ) return;
          const liveItems = new Map(requestMenu.items.map((item) => [item.item_id, item]));
          const cartIds = new Set(cartItemIds);
          const responseGroups = response.models?.length
            ? response.models
            : [{
                model_key: cartIds.size ? "ensemble" as const : "popularity" as const,
                label_ar: cartIds.size ? "المزيج الذكي" : "الأكثر طلبًا",
                description_ar: "أفضل الاقتراحات المتاحة",
                available: Boolean(response.top_recommendations?.length),
                threshold_fallback_used: Boolean(response.threshold_fallback_used),
                suggestions: response.top_recommendations || [],
              }];
          const scopedGroups = responseGroups;
          const validGroups = scopedGroups.map((group) => {
            const seen = new Set<number>();
            const suggestions = (group.suggestions || []).filter((item) => {
              const liveItem = liveItems.get(item.item_id);
              if (
                seen.has(item.item_id)
                || item.item_id === previousTopItemId
                || !liveItem
                || !liveItem.is_available
                || item.addable === false
                || cartIds.has(item.item_id)
              ) return false;
              if (
                cartIds.size
                && (item.model_key === "popularity" || item.recommendation_context === "popular")
              ) return false;
              seen.add(item.item_id);
              return true;
            });
            return { ...group, available: suggestions.length > 0, suggestions };
          });
          setModelGroups(validGroups);
          setRecommendationTrace({
            requestId: response.request_id,
            modelVersion: response.model_version,
            restaurantId: requestRestaurantId,
            cartItemKey: requestCartItemKey,
          });
          setActiveModelKey(() => {
            const preferred = validGroups.find(
              (group) => group.model_key === response.default_model_key && group.available,
            );
            return preferred?.model_key
              || validGroups.find((group) => group.available)?.model_key
              || (cartIds.size ? "fbt_confidence" : "restaurant_popularity");
          });
          setSelectedRecommendationId(null);
        })
        .catch((cause: unknown) => {
          if (cause instanceof DOMException && cause.name === "AbortError") return;
          if (
            recommendationRequestRef.current !== requestNumber
            || activeRestaurantIdRef.current !== requestRestaurantId
            || activeCartItemKeyRef.current !== requestCartItemKey
          ) return;
          setModelGroups([]);
          setRecommendationTrace(null);
          setSelectedRecommendationId(null);
          setRecommendationError(cause instanceof Error ? cause.message : "تعذر تحميل الاقتراح");
        })
        .finally(() => {
          if (!controller.signal.aborted && recommendationRequestRef.current === requestNumber) {
            setLoadingRecommendation(false);
          }
        });
    }, 100);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [
    cartItemKey,
    menuRevision,
    recommendationRefreshToken,
    restaurantId,
  ]);

  const selectedRestaurant = useMemo(
    () => restaurants.find((restaurant) => restaurant.restaurant_id === restaurantId) || menu?.restaurant || null,
    [menu?.restaurant, restaurantId, restaurants],
  );

  const quickRestaurants = useMemo(
    () => {
      const preferred = QUICK_RESTAURANT_IDS
        .map((id) => restaurants.find((restaurant) => restaurant.restaurant_id === id))
        .filter((restaurant): restaurant is Restaurant => Boolean(restaurant?.active_item_count));
      const fallback = restaurants.filter(
        (restaurant) =>
          restaurant.restaurant_id !== DEFAULT_RESTAURANT_ID
          && restaurant.active_item_count > 0
          && !preferred.some((candidate) => candidate.restaurant_id === restaurant.restaurant_id),
      );
      return [...preferred, ...fallback].slice(0, 2);
    },
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

  const cartKnownTotal = useMemo(
    () => cart.reduce((total, line) => total + (line.price ?? 0) * line.quantity, 0),
    [cart],
  );
  const unknownPriceQuantity = useMemo(
    () => cart.reduce((total, line) => total + (line.price == null ? line.quantity : 0), 0),
    [cart],
  );
  const cartQuantity = useMemo(() => cart.reduce((total, line) => total + line.quantity, 0), [cart]);
  const visibleModelGroups = modelGroups;

  const activeModelGroup = useMemo(
    () =>
      visibleModelGroups.find((group) => group.model_key === activeModelKey && group.available)
      || visibleModelGroups.find((group) => group.model_key === "ensemble" && group.available)
      || visibleModelGroups.find((group) => group.available)
      || null,
    [activeModelKey, visibleModelGroups],
  );
  const recommendations = activeModelGroup?.suggestions || [];
  const recommendation = useMemo(
    () => recommendations.find((item) => item.item_id === selectedRecommendationId) || recommendations[0] || null,
    [recommendations, selectedRecommendationId],
  );
  useEffect(() => {
    if (recommendation?.item_id != null) {
      lastVisibleRecommendationIdRef.current = recommendation.item_id;
    }
  }, [recommendation?.item_id]);
  const recommendationMenuItem = useMemo(
    () => menu?.items.find((item) => item.item_id === recommendation?.item_id) || null,
    [menu?.items, recommendation?.item_id],
  );
  const recommendationSize =
    recommendationMenuItem?.sizes.find((size) => !size.is_deleted && size.is_available);
  const recommendationRequiresSize = Boolean(
    recommendationMenuItem?.sizes.some((size) => !size.is_deleted),
  );
  const recommendationCanAdd = Boolean(
    recommendationMenuItem?.is_available && (!recommendationRequiresSize || recommendationSize),
  );
  const recommendationScore = Math.round(recommendation?.compatibility_percent || 0);
  const recommendationSourceDetails = recommendation ? recommendationProvenance(recommendation) : null;
  const recommendationAccuracy = recommendation ? validatedModelAccuracy(recommendation) : null;

  function emitRecommendationEvent(
    eventType: RecommendationEventType,
    item: WidgetRecommendationItem | null = recommendation,
  ) {
    if (
      !item
      || !recommendationTrace
      || recommendationTrace.restaurantId !== restaurantId
      || recommendationTrace.cartItemKey !== cartItemKey
    ) return;
    sendRecommendationEvent({
      event_type: eventType,
      restaurant_id: recommendationTrace.restaurantId,
      recommended_item_id: item.item_id,
      source: item.source,
      request_id: recommendationTrace.requestId,
      session_id: sessionId,
      cart_item_ids: cartItemIds,
      recommendation_type: item.recommendation_type,
      surface: "cart",
      rank: item.rank,
      score: item.score,
      model_version: recommendationTrace.modelVersion,
    });
  }

  useEffect(() => {
    if (!recommendation || !recommendationTrace) return;
    if (
      recommendationTrace.restaurantId !== restaurantId
      || recommendationTrace.cartItemKey !== cartItemKey
    ) return;
    const eventKey = `${recommendationTrace.requestId}:${recommendation.model_key}:${recommendation.item_id}`;
    if (shownEventsRef.current.has(eventKey)) return;
    shownEventsRef.current.add(eventKey);
    emitRecommendationEvent("shown", recommendation);
  }, [recommendation?.item_id, recommendation?.model_key, recommendationTrace?.requestId]);

  function changeRestaurant(nextId: number) {
    setRestaurantPickerOpen(false);
    if (!nextId || nextId === restaurantId) return;
    const hadCart = cart.length > 0;
    cartMutationEpochRef.current += 1;
    cartRef.current = [];
    setCart([]);
    lastAddedHistoryRef.current = [];
    setLastAddedItemId(null);
    setModelGroups([]);
    setRecommendationTrace(null);
    setActiveModelKey("popularity");
    setSelectedRecommendationId(null);
    setDetailsOpen(false);
    setRecommendationError("");
    setSearch("");
    setCategoryId("all");
    setMenu(null);
    lastVisibleRecommendationIdRef.current = null;
    activeRestaurantIdRef.current = nextId;
    setRestaurantId(nextId);
    setNotice(hadCart ? "تم تغيير المطعم وتصفير السلة." : "تم تغيير المطعم.");
  }

  function updateMenuItem(liveItem: MenuItem, expectedRestaurantId: number) {
    setMenu((current) => {
      if (!current || current.restaurant.restaurant_id !== expectedRestaurantId) return current;
      return {
        ...current,
        items: current.items.map((item) => (item.item_id === liveItem.item_id ? liveItem : item)),
      };
    });
  }

  function syncLastAddedWithCart(next: CartLine[]) {
    const remainingIds = new Set(next.map((line) => line.item_id));
    lastAddedHistoryRef.current = lastAddedHistoryRef.current.filter((itemId) => remainingIds.has(itemId));
    if (!lastAddedHistoryRef.current.length && next.length) {
      lastAddedHistoryRef.current = [next[next.length - 1].item_id];
    }
    setLastAddedItemId(lastAddedHistoryRef.current.at(-1) ?? null);
  }

  function recordLastAdded(itemId: number) {
    lastAddedHistoryRef.current = [...lastAddedHistoryRef.current, itemId].slice(-200);
    setLastAddedItemId(itemId);
  }

  function replaceCart(next: CartLine[]) {
    cartRef.current = next;
    setCart(next);
  }

  function reconcileCartWithMenu(payload: RestaurantMenuResponse) {
    const current = cartRef.current;
    if (!current.length) return;
    const liveItems = new Map(payload.items.map((item) => [item.item_id, item]));
    let removed = 0;
    let adjusted = 0;
    let pricesUpdated = 0;
    const next: CartLine[] = [];
    for (const line of current) {
      const item = liveItems.get(line.item_id);
      if (!item?.is_available) {
        removed += line.quantity;
        continue;
      }
      if (line.item_size_id != null) {
        const size = item.sizes.find(
          (candidate) => candidate.item_size_id === line.item_size_id && !candidate.is_deleted,
        );
        if (!size?.is_available) {
          removed += line.quantity;
          continue;
        }
        const quantity = size.remaining_quantity == null
          ? line.quantity
          : Math.min(line.quantity, Math.max(0, size.remaining_quantity));
        if (quantity <= 0) {
          removed += line.quantity;
          continue;
        }
        if (quantity !== line.quantity) adjusted += line.quantity - quantity;
        if (line.price !== size.price) pricesUpdated += quantity;
        next.push({
          ...line,
          title: itemName(item),
          size_title: sizeName(size),
          price: size.price,
          quantity,
          remaining_quantity: size.remaining_quantity,
        });
      } else {
        const nowRequiresSize = item.sizes.some((size) => !size.is_deleted);
        if (nowRequiresSize) {
          removed += line.quantity;
          continue;
        }
        next.push({
          ...line,
          title: itemName(item),
          price: null,
          remaining_quantity: null,
        });
      }
    }
    const changed = removed > 0
      || adjusted > 0
      || next.some((line, index) => {
        const old = current[index];
        return !old || old.price !== line.price || old.title !== line.title || old.size_title !== line.size_title;
      });
    if (!changed) return;
    replaceCart(next);
    syncLastAddedWithCart(next);
    if (removed || adjusted || pricesUpdated) {
      const parts = [
        removed ? `إزالة ${removed.toLocaleString("ar-SA")} غير متاح` : "",
        adjusted ? `تخفيض ${adjusted.toLocaleString("ar-SA")} حسب المخزون` : "",
        pricesUpdated ? `تحديث سعر ${pricesUpdated.toLocaleString("ar-SA")} عنصر` : "",
      ].filter(Boolean);
      setNotice(`تمت مزامنة السلة مع المنيو: ${parts.join("، ")}.`);
    }
  }

  function setCartKeyBusy(key: string, busy: boolean) {
    if (busy) busyCartKeysRef.current.add(key);
    else busyCartKeysRef.current.delete(key);
    setBusyCartKeys(new Set(busyCartKeysRef.current));
  }

  function commitLiveCartAddition(item: MenuItem, size?: MenuSize): boolean {
    const key = `${item.item_id}:${size?.item_size_id || 0}`;
    const current = cartRef.current;
    const existing = current.find((line) => line.key === key);
    const stockLimit = size?.remaining_quantity ?? null;
    if (stockLimit != null && (existing?.quantity || 0) >= stockLimit) {
      setRecommendationError(`الكمية المتاحة من ${itemName(item)} هي ${stockLimit.toLocaleString("ar-SA")} فقط.`);
      setWidgetOpen(true);
      return false;
    }
    const next = existing
      ? current.map((line) => (line.key === key ? {
          ...line,
          title: itemName(item),
          size_title: sizeName(size),
          price: size?.price ?? null,
          remaining_quantity: stockLimit,
          quantity: line.quantity + 1,
        } : line))
      : [...current, {
          key,
          item_id: item.item_id,
          item_size_id: size?.item_size_id || null,
          title: itemName(item),
          size_title: sizeName(size),
          price: size?.price ?? null,
          quantity: 1,
          remaining_quantity: stockLimit,
        }];
    replaceCart(next);
    recordLastAdded(item.item_id);
    setWidgetOpen(true);
    setNotice("");
    return true;
  }

  async function addToCart(item: MenuItem, size?: MenuSize): Promise<boolean> {
    if (!restaurantId || item.restaurant_id !== restaurantId) return false;
    if (!item.is_available || (size && !size.is_available)) {
      setRecommendationError(availabilityLabel(size?.availability_reason || item.availability_reason));
      setWidgetOpen(true);
      return false;
    }
    const key = `${item.item_id}:${size?.item_size_id || 0}`;
    if (busyCartKeysRef.current.has(key)) return false;
    const requestRestaurantId = restaurantId;
    const requestCartEpoch = cartMutationEpochRef.current;
    const requestedSizeId = size?.item_size_id ?? null;
    const displayedPrice = size?.price ?? null;
    setCartKeyBusy(key, true);
    setRecommendationError("");
    try {
      const availability = await getRestaurantItemAvailability(requestRestaurantId, item.item_id);
      if (
        activeRestaurantIdRef.current !== requestRestaurantId
        || cartMutationEpochRef.current !== requestCartEpoch
      ) return false;
      const liveItem = mergeItemAvailability(item, availability);
      updateMenuItem(liveItem, requestRestaurantId);
      const liveSize = requestedSizeId == null
        ? undefined
        : liveItem.sizes.find((candidate) => candidate.item_size_id === requestedSizeId);
      const requiresSize = liveItem.sizes.some((candidate) => !candidate.is_deleted);
      if (!liveItem.is_available || (requestedSizeId != null && !liveSize?.is_available) || (requestedSizeId == null && requiresSize)) {
        setRecommendationError(requestedSizeId != null
          ? `الحجم المختار من ${itemName(item)} لم يعد متاحًا.`
          : availabilityLabel(liveItem.availability_reason));
        setWidgetOpen(true);
        return false;
      }
      if (liveSize && liveSize.price !== displayedPrice) {
        setRecommendationError(`تغيّر سعر ${itemName(item)} إلى ${formatPrice(liveSize.price)}؛ راجع السعر ثم أضفه مجددًا.`);
        setWidgetOpen(true);
        return false;
      }
      return commitLiveCartAddition(liveItem, liveSize);
    } catch (cause: unknown) {
      if (
        activeRestaurantIdRef.current !== requestRestaurantId
        || cartMutationEpochRef.current !== requestCartEpoch
      ) return false;
      setRecommendationError(cause instanceof Error ? cause.message : "تعذر التحقق من المخزون");
      setWidgetOpen(true);
      return false;
    } finally {
      setCartKeyBusy(key, false);
    }
  }

  function changeQuantity(key: string, delta: number) {
    const line = cartRef.current.find((candidate) => candidate.key === key);
    if (!line) return;
    if (delta > 0) {
      const item = menu?.items.find((candidate) => candidate.item_id === line.item_id);
      const size = item?.sizes.find((candidate) => candidate.item_size_id === line.item_size_id);
      if (item) void addToCart(item, size);
      return;
    }
    cartMutationEpochRef.current += 1;
    const next = cartRef.current
      .map((candidate) => (candidate.key === key ? { ...candidate, quantity: candidate.quantity - 1 } : candidate))
      .filter((candidate) => candidate.quantity > 0);
    replaceCart(next);
    if (!next.some((candidate) => candidate.item_id === line.item_id)) syncLastAddedWithCart(next);
  }

  function removeCartLine(key: string) {
    cartMutationEpochRef.current += 1;
    const next = cartRef.current.filter((line) => line.key !== key);
    replaceCart(next);
    syncLastAddedWithCart(next);
  }

  function clearCart() {
    cartMutationEpochRef.current += 1;
    cartRef.current = [];
    setCart([]);
    lastAddedHistoryRef.current = [];
    setLastAddedItemId(null);
    setModelGroups([]);
    setRecommendationTrace(null);
    setActiveModelKey("popularity");
    setSelectedRecommendationId(null);
    setDetailsOpen(false);
    setRecommendationError("");
  }

  function removeModelSuggestions(shouldRemove: (item: WidgetRecommendationItem) => boolean) {
    setModelGroups((current) => {
      const next = current.map((group) => {
        const suggestions = group.suggestions.filter((item) => !shouldRemove(item));
        return { ...group, suggestions, available: suggestions.length > 0 };
      });
      const nextVisibleGroup =
        next.find((group) => group.model_key === activeModelKey && group.available)
        || next.find((group) => group.model_key === "ensemble" && group.available)
        || next.find((group) => group.available);
      const nextVisibleItemId = nextVisibleGroup?.suggestions[0]?.item_id;
      if (nextVisibleItemId != null) {
        lastVisibleRecommendationIdRef.current = nextVisibleItemId;
      }
      return next;
    });
    setActiveModelKey("ensemble");
    setSelectedRecommendationId(null);
  }

  async function addRecommendationToCart() {
    if (!restaurantId || !recommendation || !recommendationMenuItem) return;
    const requestRestaurantId = restaurantId;
    const requestRecommendationId = recommendation.item_id;
    const requestMenuItem = recommendationMenuItem;
    const preferredSizeId = recommendationSize?.item_size_id;
    const displayedPrice = recommendationSize?.price ?? null;
    const requestCartSignature = cartSignature;
    const requestCartEpoch = cartMutationEpochRef.current;
    const requestRecommendation = recommendation;
    emitRecommendationEvent("clicked", requestRecommendation);
    setAddingRecommendation(true);
    setRecommendationError("");
    try {
      const availability = await getRestaurantItemAvailability(
        requestRestaurantId,
        requestRecommendationId,
      );
      if (
        activeRestaurantIdRef.current !== requestRestaurantId
        || activeCartSignatureRef.current !== requestCartSignature
        || cartMutationEpochRef.current !== requestCartEpoch
      ) return;
      const liveItem = mergeItemAvailability(requestMenuItem, availability);
      updateMenuItem(liveItem, requestRestaurantId);
      const liveSize = preferredSizeId == null
        ? undefined
        : liveItem.sizes.find((size) => size.item_size_id === preferredSizeId);
      const requiresSize = liveItem.sizes.some((size) => !size.is_deleted);
      if (!liveItem.is_available || (requiresSize && !liveSize?.is_available)) {
        removeModelSuggestions((item) => item.item_id === requestRecommendationId);
        setRecommendationError(
          requiresSize
            ? "الحجم المعروض لهذا الاقتراح لم يعد متاحًا؛ لن نستبدله بحجم أو سعر مختلف."
            : "هذا الاقتراح نفد من المخزون، جاري اختيار بديل متاح.",
        );
        setRecommendationRefreshToken((token) => token + 1);
        return;
      }
      if (liveSize && liveSize.price !== displayedPrice) {
        setRecommendationError(`تغيّر السعر إلى ${formatPrice(liveSize.price)}؛ راجع السعر الجديد ثم اضغط إضافة مجددًا.`);
        return;
      }
      if (!commitLiveCartAddition(liveItem, liveSize)) return;
      emitRecommendationEvent("added_to_cart", requestRecommendation);
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
        || cartMutationEpochRef.current !== requestCartEpoch
      ) return;
      setRecommendationError(cause instanceof Error ? cause.message : "تعذر التحقق من توفر الاقتراح");
    } finally {
      setAddingRecommendation(false);
    }
  }

  function refreshMenu() {
    if (!restaurantId || loadingMenu) return;
    forceFreshMenuRef.current = true;
    recommendationRequestRef.current += 1;
    setModelGroups([]);
    setRecommendationTrace(null);
    setSelectedRecommendationId(null);
    setRecommendationError("");
    setLoadingRecommendation(false);
    setMenuRefreshToken((token) => token + 1);
  }

  function dismissWidget() {
    emitRecommendationEvent("dismissed");
    setWidgetOpen(false);
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

  useEffect(() => {
    if (!widgetOpen || !widgetRef.current || !widgetPosition) return;
    const frame = window.requestAnimationFrame(() => {
      if (!widgetRef.current) return;
      const margin = 10;
      const maxLeft = Math.max(margin, window.innerWidth - widgetRef.current.offsetWidth - margin);
      const maxTop = Math.max(margin, window.innerHeight - widgetRef.current.offsetHeight - margin);
      setWidgetPosition((current) => current ? {
        left: Math.max(margin, Math.min(maxLeft, current.left)),
        top: Math.max(margin, Math.min(maxTop, current.top)),
      } : current);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [detailsOpen, modelGroups, recommendationError, widgetOpen]);

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
        <button className="refresh-button" type="button" onClick={refreshMenu} disabled={!restaurantId || loadingMenu}>
          {loadingMenu ? "جارٍ التحديث…" : "↻ تحديث المنيو"}
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
              <span className="sr-only">البحث في أصناف المنيو</span>
              <span aria-hidden="true">⌕</span>
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="ابحث عن صنف"
                aria-label="البحث في أصناف المنيو"
              />
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
                          onClick={() => void addToCart(item, size)}
                          aria-label={`أضف ${itemName(item)} ${sizeName(size)}`}
                          disabled={!size.is_available || busyCartKeys.has(`${item.item_id}:${size.item_size_id}`)}
                        >
                          {busyCartKeys.has(`${item.item_id}:${size.item_size_id}`) ? "…" : size.is_available ? "+" : "×"}
                        </button>
                      </div>
                    ))
                  ) : (
                    <div className={item.is_available ? "size-row" : "size-row unavailable"}>
                      <span>
                        <b>عادي</b>
                        <small>{item.is_available ? "السعر عند الطلب" : availabilityLabel(item.availability_reason)}</small>
                      </span>
                      <button
                        type="button"
                        onClick={() => void addToCart(item)}
                        disabled={!item.is_available || busyCartKeys.has(`${item.item_id}:0`)}
                      >
                        {busyCartKeys.has(`${item.item_id}:0`) ? "…" : item.is_available ? "+" : "×"}
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
                      <button
                        type="button"
                        onClick={() => changeQuantity(line.key, 1)}
                        disabled={
                          busyCartKeys.has(line.key)
                          || (line.remaining_quantity != null && line.quantity >= line.remaining_quantity)
                        }
                        aria-label={`زيادة كمية ${line.title}`}
                      >
                        {busyCartKeys.has(line.key) ? "…" : "+"}
                      </button>
                      <b>{line.quantity}</b>
                      <button type="button" onClick={() => changeQuantity(line.key, -1)} aria-label={`تقليل كمية ${line.title}`}>−</button>
                    </div>
                    <button className="remove-line" type="button" onClick={() => removeCartLine(line.key)} aria-label="حذف الصنف">×</button>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="cart-total">
            <span>
              الإجمالي
              {unknownPriceQuantity ? (
                <small>لا يشمل {unknownPriceQuantity.toLocaleString("ar-SA")} بسعر عند الطلب</small>
              ) : null}
            </span>
            <strong>{formatPrice(cartKnownTotal)}</strong>
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
               <strong>أفضل اقتراح موثّق</strong>
               <small>
                 {cartItemIds.length
                   ? "المودل الأعلى في الاختبار لهذا المطعم"
                   : "الأكثر طلبًا في الفترة الحالية عند توفر بياناتها"}
               </small>
             </div>
            <button type="button" onClick={dismissWidget} aria-label="إغلاق الويدجت">×</button>
          </header>
           <div className="widget-body">
             {recommendationError ? <div className="widget-inline-error">{recommendationError}</div> : null}
             {loadingRecommendation && !(recommendation && recommendationMenuItem) ? (
               <div className="widget-empty"><span className="spinner" /> نجهّز اقتراحك…</div>
             ) : recommendation && recommendationMenuItem ? (
               <>
               <article className={recommendations.length > 1 ? "recommendation-card has-stack" : "recommendation-card"}>
                 <span className="ai-badge">AI</span>
                 <div className="recommendation-copy">
                   <h3>{itemName(recommendationMenuItem)}</h3>
                   <div className="recommendation-meta">
                     <span className="type-badge">
                       المودل: {recommendationSourceDetails?.models.join(" + ") || recommendation.model_label_ar}
                     </span>
                     <span className={recommendation.meets_threshold ? "match-badge strong" : "match-badge"}>
                       {recommendation.confidence_band_ar}
                     </span>
                     {recommendationAccuracy ? (
                       <span className="accuracy-badge" title={`مقياس التقييم: ${recommendationAccuracy.metricName}`}>
                         {recommendationAccuracy.label} {recommendationAccuracy.percent.toLocaleString("ar-SA")}%
                       </span>
                     ) : null}
                   </div>
                   <p className="source-summary">
                     <b>مصادر الاقتراح:</b> {recommendationSourceDetails?.sources.join(" + ") || sourceLabel(recommendation.source)}
                   </p>
                   {recommendation.reason ? <p className="recommendation-reason">{recommendation.reason}</p> : null}
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
                     aria-valuemax={100}
                     aria-valuenow={recommendationScore}
                     aria-label={`${recommendation.score_label_ar} ${recommendationScore}%`}
                   >
                     <strong>{recommendationScore.toLocaleString("ar-SA")}%</strong>
                     <small>{recommendation.score_label_ar}</small>
                   </div>
                   <button
                     type="button"
                     onClick={addRecommendationToCart}
                     disabled={addingRecommendation || !recommendationCanAdd}
                     title={recommendationCanAdd ? "إضافة الاقتراح" : "لا يوجد الحجم المعروض متاحًا"}
                   >
                     {addingRecommendation ? "جاري التحقق…" : "+ أضف"}
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
                    const provenance = recommendationProvenance(item);
                    const accuracy = validatedModelAccuracy(item);
                    return (
                       <button
                         type="button"
                         className={item.item_id === recommendation.item_id ? "recommendation-list-row active" : "recommendation-list-row"}
                         key={`${activeModelGroup?.model_key}:${item.model_key}:${item.item_id}`}
                         onClick={() => {
                           emitRecommendationEvent("clicked", item);
                           setSelectedRecommendationId(item.item_id);
                         }}
                       >
                        <span className="rank-number">{index + 1}</span>
                         <span className="list-copy">
                           <strong>{itemName(liveItem)}</strong>
                           <small className="list-model">المودل: {provenance.models.join(" + ") || item.model_label_ar}</small>
                           <small className="list-sources">{provenance.sources.join(" + ")}</small>
                           {accuracy ? (
                             <small className="list-accuracy" title={`مقياس التقييم: ${accuracy.metricName}`}>
                               {accuracy.label}: {accuracy.percent.toLocaleString("ar-SA")}%
                             </small>
                           ) : null}
                           <i><b style={{ width: `${item.compatibility_percent}%` }} /></i>
                         </span>
                         <span
                           className="list-percent"
                           aria-label={`درجة الملاءمة ${Math.round(item.compatibility_percent)}%`}
                           title="درجة الملاءمة للاقتراح"
                         >
                           {Math.round(item.compatibility_percent).toLocaleString("ar-SA")}%
                         </span>
                       </button>
                    );
                  })}
                   <p className="compatibility-note">
                     النسبة بجانب كل صنف هي درجة ملاءمته للسلة، وليست دقة المودل. دقة المودل لا تظهر إلا عند توفر مقياس تقييم موثّق من الخادم.
                   </p>
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
