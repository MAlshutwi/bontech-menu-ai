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
  RecommendationContributingModel,
  RecommendationEventType,
  RecommendationPathContext,
  RecommendationPathStatus,
  WidgetRecommendationItem,
  WidgetRecommendationModelGroup,
  WidgetRecommendationResponse,
} from "./types/menu";

const DEFAULT_RESTAURANT_ID = 192;
const QUICK_RESTAURANT_IDS = [260, 277];

interface RecommendationPathGroup extends WidgetRecommendationModelGroup {
  context_key: RecommendationPathContext;
  status: RecommendationPathStatus;
  why_ar: string;
  unavailable_reason: string | null;
  selected_model?: RecommendationContributingModel | null;
}

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
  current_trend_momentum: "مودل الترند الحالي",
  restaurant_popularity: "الأكثر مبيعًا في المطعم",
  fbt_confidence: "ارتباط السلة حسب الثقة",
  fbt_hybrid: "ارتباط السلة الهجين",
  fbt_paircount: "ارتباط السلة حسب التكرار",
  fbt_lift: "ارتباط السلة حسب الرفع",
  item2vec: "مودل تشابه الأصناف",
  pooled_fbt: "ارتباط السلة العام",
  live_menu_fallback: "اختيار متاح من المنيو",
  personalized: "مودل المستخدم",
  user_affinity: "مودل تفضيلات المستخدم",
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

const RECOMMENDATION_PATHS: Array<{
  context_key: RecommendationPathContext;
  label_ar: string;
  description_ar: string;
  fallback_model_key: string;
}> = [
  {
    context_key: "full_cart",
    label_ar: "حسب السلة كاملة",
    description_ar: "يربط جميع أصناف السلة بالأصناف التي تُطلب معها.",
    fallback_model_key: "full_cart",
  },
  {
    context_key: "last_item",
    label_ar: "ارتباط آخر عنصر",
    description_ar: "يركّز على آخر صنف تمت إضافته إلى السلة.",
    fallback_model_key: "last_item",
  },
  {
    context_key: "popularity",
    label_ar: "الأكثر طلبًا",
    description_ar: "يعتمد على ترتيب الطلبات الفعلية داخل المطعم.",
    fallback_model_key: "restaurant_popularity",
  },
  {
    context_key: "current_trend",
    label_ar: "الترند الحالي",
    description_ar: "يرصد ارتفاع الطلب خلال آخر 7 أيام مقارنة بالـ28 يومًا السابقة.",
    fallback_model_key: "current_trend_momentum",
  },
  {
    context_key: "user",
    label_ar: "حسب المستخدم",
    description_ar: "يستخدم سجل المستخدم المصرّح به عندما تتوفر بيانات كافية.",
    fallback_model_key: "personalized",
  },
];

function normalizePathContext(value: string | null | undefined): RecommendationPathContext | null {
  return ({
    full_cart: "full_cart",
    based_on_cart: "full_cart",
    last_item: "last_item",
    based_on_last_item: "last_item",
    popularity: "popularity",
    popular: "popularity",
    restaurant_popularity: "popularity",
    current_trend: "current_trend",
    current_trend_momentum: "current_trend",
    user: "user",
    personalized: "user",
  } as Record<string, RecommendationPathContext>)[value || ""] || null;
}

function contributorObject(value: RecommendationContributingModel | null | undefined) {
  return value && typeof value === "object" ? value : null;
}

function unavailableReasonAr(
  reason: string | null | undefined,
  context: RecommendationPathContext,
  hasCart: boolean,
) {
  const normalized = (reason || "").trim();
  const knownReasons: Record<string, string> = {
    cart_required: "أضف صنفًا إلى السلة لتفعيل هذا المسار.",
    empty_cart: "أضف صنفًا إلى السلة لتفعيل هذا المسار.",
    last_item_required: "أضف صنفًا جديدًا حتى نعرف آخر عنصر في السلة.",
    insufficient_data: "لا توجد بيانات كافية وموثّقة لهذا المسار حاليًا.",
    insufficient_user_history: "لا يوجد سجل طلبات كافٍ لهذا المستخدم بعد.",
    user_identity_required: "يلزم معرّف مستخدم مصرح به لتفعيل التخصيص.",
    no_user_identity: "يلزم معرّف مستخدم مصرح به لتفعيل التخصيص.",
    model_not_validated: "المودل غير موثّق بالتقييم بعد، لذلك لن نعرض نتيجته.",
    no_eligible_live_recommendations: "لا يوجد اقتراح متاح بعد فحص المنيو والمخزون.",
    stale_order_history: "سجل الطلبات قديم ولا يمثل الترند الحالي.",
    no_order_history: "لا يوجد سجل طلبات يمكن استخدامه لرصد الترند.",
    no_recent_orders: "لا توجد طلبات حديثة خلال نافذة الرصد الحالية.",
    insufficient_same_period_observations: "لا توجد ملاحظات حديثة كافية للمقارنة في الفترة نفسها.",
    customer_identifier_not_provided: "لم يصل معرّف مستخدم مصرح به، لذلك مسار المستخدم غير متاح.",
    insufficient_validated_customer_order_linkage: "لا يوجد ربط موثّق وكافٍ بين المستخدم وطلباته لتشغيل التخصيص.",
  };
  if (knownReasons[normalized]) return knownReasons[normalized];
  if (normalized.startsWith("no_observed_growth")) {
    return "البيانات حديثة، لكن لم يُرصد ارتفاع طلب يتجاوز حد الدعم الأدنى حاليًا.";
  }
  if (normalized && /[\u0600-\u06ff]/.test(normalized)) return normalized;
  if (context === "user") return "التخصيص غير متاح حتى يصل معرّف المستخدم وسجل طلباته بشكل مصرح وآمن.";
  if ((context === "full_cart" || context === "last_item") && !hasCart) {
    return "أضف صنفًا إلى السلة لتفعيل هذا المسار.";
  }
  if (context === "current_trend") return "لا توجد بيانات زمنية كافية لهذه الفترة حاليًا.";
  return "لا يوجد اقتراح مؤهل من هذا المسار بعد فحص المنيو والمخزون.";
}

function pathStatusLabel(group: RecommendationPathGroup) {
  if (group.selected && (group.status === "available" || group.status === "fallback")) return "المختار";
  if (group.status === "fallback") return "احتياطي";
  if (group.status === "available") return "داعم";
  if (group.status === "stale") return "بيانات قديمة";
  return "غير متاح";
}

function formatOrderTimestamp(value: string | null | undefined) {
  if (!value) return "وقت غير معروف";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("ar-SA", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Riyadh",
  }).format(parsed);
}

function staleTrendReasonAr(reason: string | null | undefined, latestOrderAt: string | null | undefined) {
  const translated = unavailableReasonAr(reason || "stale_order_history", "current_trend", false);
  const lastOrder = latestOrderAt
    ? ` آخر طلب مسجل: ${formatOrderTimestamp(latestOrderAt)}.`
    : " تاريخ آخر طلب غير متاح.";
  return `${translated}${lastOrder} لن نعرض بيانات قديمة كترند حالي.`;
}

function validCompatibilityPercent(item: WidgetRecommendationItem | null | undefined) {
  const value = item?.compatibility_percent;
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 100) return null;
  return Math.round(value);
}

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

function validatedModelAccuracy(
  item: WidgetRecommendationItem,
  group?: RecommendationPathGroup | null,
) {
  const descriptor = contributorObject(group?.selected_model) || contributorObject(item.selected_model);
  const validated = group?.validated === true
    || item.accuracy_validated === true
    || descriptor?.validated === true;
  if (!validated) return null;

  const percent = group?.validation_percent
    ?? item.model_accuracy_percent
    ?? descriptor?.validation_percent;
  if (typeof percent !== "number" || !Number.isFinite(percent) || percent < 0 || percent > 100) return null;

  const rawMetric = group?.validation_metric
    ?? item.accuracy_metric
    ?? item.model_accuracy_metric
    ?? descriptor?.validation_metric;
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

function pathModelLabel(group: RecommendationPathGroup) {
  const descriptor = contributorObject(group.selected_model)
    || contributorObject(group.suggestions[0]?.selected_model);
  return descriptor?.label_ar
    || group.suggestions[0]?.model_label_ar
    || MODEL_LABELS_AR[group.model_key]
    || group.model_key;
}

function sectionForPath(
  response: WidgetRecommendationResponse,
  context: RecommendationPathContext,
) {
  if (context === "full_cart") return response.sections.based_on_cart || [];
  if (context === "last_item") return response.sections.based_on_last_item || [];
  if (context === "popularity") return response.sections.popular || [];
  if (context === "current_trend") return response.sections.current_trend || [];
  return [];
}

function buildRecommendationPaths(
  response: WidgetRecommendationResponse,
  liveItems: Map<number, MenuItem>,
  cartIds: Set<number>,
  previousTopItemId: number | null,
): RecommendationPathGroup[] {
  const responseSelected = contributorObject(response.selected_model);
  const selectedContext = normalizePathContext(response.default_context_key)
    || normalizePathContext(responseSelected?.context_key)
    || normalizePathContext(response.top_recommendations?.[0]?.recommendation_context)
    || (cartIds.size ? "full_cart" : "popularity");
  const responseDescriptors = [
    response.selected_model,
    ...(response.supporting_models || []),
    ...(response.unavailable_models || []),
    ...((response.top_recommendations || []).flatMap((item) => [
      item.selected_model,
      ...(item.supporting_models || []),
    ])),
  ];

  return RECOMMENDATION_PATHS.map((definition) => {
    const explicitGroup = (response.models || []).find((group) => {
      const groupDescriptor = contributorObject(group.selected_model);
      const groupContext = normalizePathContext(group.context_key)
        || normalizePathContext(groupDescriptor?.context_key)
        || (group.selected ? selectedContext : null);
      return groupContext === definition.context_key;
    });
    const descriptor = contributorObject(explicitGroup?.selected_model)
      || responseDescriptors
        .map(contributorObject)
        .find((candidate) => normalizePathContext(candidate?.context_key) === definition.context_key)
      || null;
    const rawCandidates = explicitGroup
      ? explicitGroup.suggestions || []
      : sectionForPath(response, definition.context_key);
    const selectedCandidates = definition.context_key === selectedContext
      ? [...(response.top_recommendations || []), ...rawCandidates]
      : rawCandidates;
    const seen = new Set<number>();
    const rawStatus = explicitGroup?.status;
    const staleTrend = definition.context_key === "current_trend"
      && (
        rawStatus === "stale"
        || explicitGroup?.data_freshness === "stale"
        || explicitGroup?.freshness_status === "stale"
      );
    const noTrendData = definition.context_key === "current_trend"
      && explicitGroup?.freshness_status === "no_data";
    const suggestions = (staleTrend || noTrendData ? [] : selectedCandidates).filter((item) => {
      const liveItem = liveItems.get(item.item_id);
      if (
        seen.has(item.item_id)
        || item.item_id === previousTopItemId
        || !liveItem
        || !liveItem.is_available
        || item.addable === false
        || cartIds.has(item.item_id)
      ) return false;
      seen.add(item.item_id);
      return true;
    }).slice(0, 1);
    const status: RecommendationPathStatus = staleTrend
      ? "stale"
      : suggestions.length
      ? rawStatus === "fallback" || explicitGroup?.threshold_fallback_used
        ? "fallback"
        : "available"
      : "unavailable";
    // New API groups explicitly identify the single default rail. A model can
    // still be the selected model *inside* a supporting rail, which must not
    // make that whole rail look like the global choice in the widget.
    const selected = explicitGroup
      ? explicitGroup.selected === true
      : Boolean(
        contributorObject(descriptor)?.role === "selected"
        && definition.context_key === selectedContext
        && status !== "unavailable",
      );
    const description = explicitGroup?.description_ar || definition.description_ar;
    const why = explicitGroup?.why_ar
      || suggestions[0]?.reason
      || definition.description_ar;
    const unavailableReason = status === "stale"
      ? staleTrendReasonAr(
          explicitGroup?.unavailable_reason,
          explicitGroup?.latest_order_at || explicitGroup?.data_as_of || response.latest_order_at,
        )
      : status === "unavailable"
        ? unavailableReasonAr(
            explicitGroup?.unavailable_reason || descriptor?.unavailable_reason,
            definition.context_key,
            cartIds.size > 0,
          )
      : null;
    const modelKey = descriptor?.model_key
      || explicitGroup?.model_key
      || suggestions[0]?.model_key
      || definition.fallback_model_key;

    return {
      model_key: modelKey,
      context_key: definition.context_key,
      label_ar: definition.label_ar,
      description_ar: description,
      why_ar: why,
      available: status === "available" || status === "fallback",
      status,
      unavailable_reason: unavailableReason,
      selected_model: descriptor || explicitGroup?.selected_model || null,
      selected,
      validated: explicitGroup?.validated ?? descriptor?.validated ?? false,
      validation_metric: explicitGroup?.validation_metric ?? descriptor?.validation_metric ?? null,
      validation_percent: explicitGroup?.validation_percent ?? descriptor?.validation_percent ?? null,
      validation_trials: explicitGroup?.validation_trials ?? descriptor?.validation_trials ?? 0,
      validation_scope: explicitGroup?.validation_scope ?? descriptor?.validation_scope ?? null,
      evaluation_version: explicitGroup?.evaluation_version ?? descriptor?.evaluation_version ?? null,
      threshold_fallback_used: explicitGroup?.threshold_fallback_used ?? status === "fallback",
      suggestions,
    };
  });
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
  const [modelGroups, setModelGroups] = useState<RecommendationPathGroup[]>([]);
  const [activeModelKey, setActiveModelKey] = useState<RecommendationPathContext>("popularity");
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
          const validGroups = buildRecommendationPaths(
            response,
            liveItems,
            cartIds,
            previousTopItemId,
          );
          setModelGroups(validGroups);
          setRecommendationTrace({
            requestId: response.request_id,
            modelVersion: response.model_version,
            restaurantId: requestRestaurantId,
            cartItemKey: requestCartItemKey,
          });
          setActiveModelKey(() => {
            const preferred = validGroups.find((group) => group.selected && group.available)
              || validGroups.find(
                (group) => group.model_key === response.default_model_key && group.available,
              )
              || validGroups.find((group) => group.available);
            return preferred?.context_key || (cartIds.size ? "full_cart" : "popularity");
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
      visibleModelGroups.find((group) => group.context_key === activeModelKey)
      || visibleModelGroups.find((group) => group.selected && group.available)
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
  const recommendationScore = validCompatibilityPercent(recommendation);
  const recommendationSourceDetails = recommendation ? recommendationProvenance(recommendation) : null;
  const recommendationAccuracy = recommendation
    ? validatedModelAccuracy(recommendation, activeModelGroup)
    : null;

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
        return {
          ...group,
          suggestions,
          available: suggestions.length > 0,
          status: suggestions.length ? group.status : "unavailable" as const,
          unavailable_reason: suggestions.length
            ? group.unavailable_reason
            : "تمت إضافة هذا الاقتراح، ونحدّث المسار الآن.",
        };
      });
      const nextVisibleGroup =
        next.find((group) => group.context_key === activeModelKey && group.available)
        || next.find((group) => group.selected && group.available)
        || next.find((group) => group.available);
      const nextVisibleItemId = nextVisibleGroup?.suggestions[0]?.item_id;
      if (nextVisibleItemId != null) {
        lastVisibleRecommendationIdRef.current = nextVisibleItemId;
      }
      return next;
    });
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
               <strong>مسارات الاقتراح الخمسة</strong>
               <small>
                 {cartItemIds.length
                   ? "اختر المسار وشاهد المودل الفعلي وسبب الاقتراح"
                   : "المسار الأنسب يُختار تلقائيًا ويمكنك مقارنة البقية"}
               </small>
             </div>
            <button type="button" onClick={dismissWidget} aria-label="إغلاق الويدجت">×</button>
          </header>
           <div className="widget-body">
             {recommendationError ? <div className="widget-inline-error">{recommendationError}</div> : null}
             {visibleModelGroups.length ? (
               <nav className="model-filters path-filters" aria-label="اختيار مسار الاقتراح">
                 {visibleModelGroups.map((group) => (
                   <button
                     type="button"
                     key={group.context_key}
                     className={`${activeModelGroup?.context_key === group.context_key ? "active" : ""} status-${group.status}`.trim()}
                     aria-pressed={activeModelGroup?.context_key === group.context_key}
                     onClick={() => {
                       setActiveModelKey(group.context_key);
                       setSelectedRecommendationId(null);
                     }}
                     title={group.available ? group.description_ar : group.unavailable_reason || group.description_ar}
                   >
                     <span>{group.label_ar}</span>
                     <b>{pathStatusLabel(group)}</b>
                   </button>
                 ))}
               </nav>
             ) : null}
             {loadingRecommendation && !(recommendation && recommendationMenuItem) ? (
               <div className="widget-empty"><span className="spinner" /> نجهّز اقتراحك…</div>
             ) : recommendation && recommendationMenuItem ? (
               <>
               <article className="recommendation-card">
                 <span className="ai-badge">AI</span>
                 <div className="recommendation-copy">
                   <h3>{itemName(recommendationMenuItem)}</h3>
                   <div className="recommendation-meta">
                     <span className="type-badge">المسار: {activeModelGroup?.label_ar}</span>
                     {activeModelGroup ? (
                       <span className={`path-status-badge status-${activeModelGroup.status}`}>
                         {pathStatusLabel(activeModelGroup)}
                       </span>
                     ) : null}
                     {recommendationScore != null ? (
                       <span className={recommendation.meets_threshold ? "match-badge strong" : "match-badge"}>
                         {recommendation.confidence_band_ar}
                       </span>
                     ) : <span className="descriptive-signal-badge">رصد وصفي</span>}
                     {recommendationAccuracy ? (
                       <span className="accuracy-badge" title={`مقياس التقييم: ${recommendationAccuracy.metricName}`}>
                         {recommendationAccuracy.label} {recommendationAccuracy.percent.toLocaleString("ar-SA")}%
                       </span>
                     ) : null}
                   </div>
                   <div className="model-origin">
                     <b>المودل الفعلي:</b> {activeModelGroup ? pathModelLabel(activeModelGroup) : recommendation.model_label_ar}
                   </div>
                   <p className="source-summary">
                     <b>مصادر الاقتراح:</b> {recommendationSourceDetails?.sources.join(" + ") || sourceLabel(recommendation.source)}
                   </p>
                   <p className="recommendation-reason">
                     <b>لماذا؟</b> {activeModelGroup?.why_ar || recommendation.reason || activeModelGroup?.description_ar}
                   </p>
                   {recommendation.model_agreement_count > 1 ? (
                     <span className="agreement-badge">
                       مدعوم من {recommendation.model_agreement_count.toLocaleString("ar-SA")} مودلات موضّحة أعلاه
                     </span>
                   ) : null}
                   <small>{sizeName(recommendationSize)} · {formatPrice(recommendationSize?.price ?? null)}</small>
                 </div>
                 <div className="recommendation-actions">
                   {recommendationScore != null ? (
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
                   ) : (
                     <div className="descriptive-signal-card" aria-label="رصد وصفي دون نسبة ملاءمة">
                       <strong>رصد وصفي</strong>
                       <small>بلا نسبة</small>
                     </div>
                   )}
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
              {visibleModelGroups.length ? (
                <button
                  className="recommendation-list-toggle"
                  type="button"
                  aria-expanded={detailsOpen}
                  onClick={() => setDetailsOpen((open) => !open)}
                >
                  <span>{detailsOpen ? "إخفاء تفاصيل المسارات" : "عرض المسارات الخمسة ومقارنتها"}</span>
                  <b aria-hidden="true">{detailsOpen ? "⌃" : "⌄"}</b>
                </button>
              ) : null}
               {detailsOpen ? (
                 <section className="recommendation-list" aria-label="مقارنة مسارات الاقتراح الخمسة">
                   <div className="recommendation-list-head">
                     <strong>اقتراح رئيسي واحد لكل مسار</strong>
                     <span>المختار هو الأنسب للسياق الحالي، ويمكن فتح أي مسار متاح للمقارنة.</span>
                   </div>
                   {visibleModelGroups.map((group, index) => {
                    const item = group.suggestions[0] || null;
                    const liveItem = item
                      ? menu?.items.find((candidate) => candidate.item_id === item.item_id) || null
                      : null;
                    const provenance = item ? recommendationProvenance(item) : null;
                    const accuracy = item ? validatedModelAccuracy(item, group) : null;
                    const compatibility = validCompatibilityPercent(item);
                    return (
                       <button
                         type="button"
                         className={`recommendation-list-row path-row status-${group.status}${activeModelGroup?.context_key === group.context_key ? " active" : ""}`}
                         key={group.context_key}
                         onClick={() => {
                           setActiveModelKey(group.context_key);
                           setSelectedRecommendationId(null);
                           if (item) emitRecommendationEvent("clicked", item);
                         }}
                       >
                        <span className="rank-number">{index + 1}</span>
                         <span className="list-copy">
                           <strong>{group.label_ar} <em>{pathStatusLabel(group)}</em></strong>
                           {item && liveItem ? <span className="list-item-name">{itemName(liveItem)}</span> : null}
                           <small className="list-model">المودل: {pathModelLabel(group)}</small>
                           {provenance ? <small className="list-sources">{provenance.sources.join(" + ")}</small> : null}
                           <small className="list-why">
                             {group.available ? `لماذا؟ ${group.why_ar}` : group.unavailable_reason}
                           </small>
                           {accuracy ? (
                             <small className="list-accuracy" title={`مقياس التقييم: ${accuracy.metricName}`}>
                               {accuracy.label}: {accuracy.percent.toLocaleString("ar-SA")}%
                             </small>
                           ) : null}
                           {compatibility != null ? <i><b style={{ width: `${compatibility}%` }} /></i> : null}
                         </span>
                         {compatibility != null ? (
                           <span
                             className="list-percent"
                             aria-label={`درجة الملاءمة ${compatibility}%`}
                             title="درجة الملاءمة للاقتراح وليست احتمال شراء"
                           >
                             {compatibility.toLocaleString("ar-SA")}%
                           </span>
                         ) : item ? (
                           <span className="list-descriptive-signal" aria-label="رصد وصفي دون نسبة">رصد وصفي</span>
                         ) : <span className="path-unavailable-mark" aria-hidden="true">—</span>}
                       </button>
                    );
                  })}
                   <p className="compatibility-note">
                     النسبة — إن وُجدت — هي درجة ملاءمة وليست احتمال شراء. «رصد وصفي» لا يحمل نسبة، ودقة المودل لا تظهر إلا بمقياس موثّق.
                   </p>
                </section>
              ) : null}
              </>
            ) : (
              <div className={`widget-empty error-copy path-unavailable status-${activeModelGroup?.status || "unavailable"}`}>
                <strong>{activeModelGroup?.label_ar || "المسار"}: {activeModelGroup ? pathStatusLabel(activeModelGroup) : "غير متاح"}</strong>
                <span>{activeModelGroup?.unavailable_reason || "لا يوجد اقتراح متاح بعد فلترة المنيو والمخزون."}</span>
                {activeModelGroup ? <small>المودل: {pathModelLabel(activeModelGroup)}</small> : null}
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
