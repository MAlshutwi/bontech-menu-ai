export interface Restaurant {
  restaurant_id: number;
  name: string;
  name_ar: string;
  total_item_count: number;
  active_item_count: number;
}

export interface MenuSize {
  item_size_id: number;
  title_ar: string;
  title_en: string;
  code: string;
  price: number | null;
  takeaway_price: number | null;
  is_deleted: boolean;
  availability_mode: string | null;
  availability_configured: boolean;
  remaining_quantity: number | null;
  is_available: boolean;
  availability_reason: string;
}

export interface MenuItem {
  item_id: number;
  restaurant_id: number;
  title_ar: string;
  title_en: string;
  category_id: number | null;
  category_ar: string;
  category_en: string;
  is_published: boolean;
  is_deleted: boolean;
  is_combo: boolean;
  calories: number | null;
  is_available: boolean;
  availability_reason: string;
  available_size_count: number;
  sizes: MenuSize[];
}

export interface MenuCategory {
  category_id: number | null;
  title_ar: string;
  title_en: string;
  count: number;
}

export interface RestaurantMenuResponse {
  restaurant: Restaurant;
  items: MenuItem[];
  categories: MenuCategory[];
  count: number;
  source: "live_database";
  include_inactive: boolean;
}

export interface MenuItemAvailabilitySize {
  item_size_id: number;
  title_ar: string;
  title_en: string;
  code: string;
  price: number | null;
  takeaway_price: number | null;
  availability_mode: string | null;
  availability_configured: boolean;
  remaining_quantity: number | null;
  is_available: boolean;
  availability_reason: string;
}

export interface MenuItemAvailabilityResponse {
  restaurant_id: number;
  item_id: number;
  category_id: number | null;
  is_available: boolean;
  availability_reason: string;
  available_size_count: number;
  sizes: MenuItemAvailabilitySize[];
  checked_at: string;
  source: "live_database";
}

export interface CartLine {
  key: string;
  item_id: number;
  item_size_id: number | null;
  title: string;
  size_title: string;
  price: number | null;
  quantity: number;
  /** Live stock ceiling for this exact size when the database exposes one. */
  remaining_quantity: number | null;
}

export type RecommendationContributingModel =
  | string
  | {
      model_key?: string;
      model_label_ar?: string;
      label_ar?: string;
      source?: string;
      source_label_ar?: string;
      context_key?: string;
      role?: "selected" | "supporting" | string;
      validated?: boolean;
      validation_metric?: string | null;
      validation_percent?: number | null;
      validation_trials?: number | null;
      validation_scope?: string | null;
      validation_source?: string | null;
      evaluation_version?: string | null;
      time_period_key?: string | null;
      time_period_ar?: string | null;
      unavailable_reason?: string | null;
      weight?: number;
      score?: number;
    };

export interface RecommendationAccuracyMetric {
  name?: string;
  key?: string;
  label_ar?: string;
  /** Object-shaped metrics must explicitly declare that they were validated. */
  validated?: boolean;
  evaluation_scope?: string;
}

export interface WidgetRecommendationItem {
  item_id: number;
  title_ar: string;
  title_en: string;
  score: number;
  source: string;
  recommendation_type: string;
  reason: string;
  addable: boolean;
  disabled_reason: string | null;
  category_id: number | null;
  recommendation_context:
    | "popular"
    | "time_context"
    | "based_on_cart"
    | "based_on_last_item"
    | "similar_alternatives"
    | "current_trend"
    | "user";
  type_label_ar: string;
  model_key: string;
  model_label_ar: string;
  score_label_ar: string;
  rank: number;
  compatibility_percent: number | null;
  probability_percent: number | null;
  confidence_band_ar: string;
  model_agreement_count: number;
  meets_threshold: boolean;
  is_available: boolean;
  availability_reason: string | null;
  /** Exact model contributors and Arabic rationale labels supplied by the API. */
  selected_model?: RecommendationContributingModel | null;
  supporting_models?: RecommendationContributingModel[];
  contributing_models?: RecommendationContributingModel[];
  source_labels_ar?: string[] | string;
  time_period_key?: string | null;
  time_period_ar?: string | null;
  /** Offline model metric; never interchangeable with compatibility_percent. */
  model_accuracy_percent?: number | null;
  accuracy_metric?: string | RecommendationAccuracyMetric | null;
  model_accuracy_metric?: string | null;
  accuracy_validated?: boolean;
}

export type RecommendationPathContext =
  | "full_cart"
  | "last_item"
  | "popularity"
  | "current_trend"
  | "user";

export type RecommendationPathStatus = "available" | "unavailable" | "fallback" | "stale";

export interface WidgetRecommendationModelGroup {
  model_key: string;
  context_key?: RecommendationPathContext | string;
  label_ar: string;
  description_ar: string;
  why_ar?: string;
  available: boolean;
  status?: RecommendationPathStatus | string;
  unavailable_reason?: string | null;
  latest_order_at?: string | null;
  data_freshness?: string | null;
  data_as_of?: string | null;
  freshness_status?: "fresh" | "stale" | "no_data" | string | null;
  future_ready?: boolean;
  selected_model?: RecommendationContributingModel | null;
  selected?: boolean;
  validated?: boolean;
  validation_metric?: string | null;
  validation_percent?: number | null;
  validation_trials?: number | null;
  validation_scope?: string | null;
  evaluation_version?: string | null;
  threshold_fallback_used: boolean;
  suggestions: WidgetRecommendationItem[];
}

export interface WidgetRecommendationResponse {
  request_id: string;
  model_version: string;
  restaurant_id: number;
  cart_item_ids: number[];
  last_added_item_id: number | null;
  fallback_used: boolean;
  latency_ms: number;
  sections: {
    based_on_last_item: WidgetRecommendationItem[];
    based_on_cart: WidgetRecommendationItem[];
    similar_alternatives: WidgetRecommendationItem[];
    popular: WidgetRecommendationItem[];
    time_context: WidgetRecommendationItem[];
    current_trend?: WidgetRecommendationItem[];
  };
  top_recommendations: WidgetRecommendationItem[];
  models: WidgetRecommendationModelGroup[];
  default_model_key: WidgetRecommendationModelGroup["model_key"];
  default_context_key?: RecommendationPathContext;
  available_model_keys: WidgetRecommendationModelGroup["model_key"][];
  available_context_keys?: RecommendationPathContext[];
  warnings: string[];
  disabled_reason: string | null;
  threshold_percent: number;
  threshold_fallback_used: boolean;
  selected_model?: RecommendationContributingModel | null;
  supporting_models?: RecommendationContributingModel[];
  selection_policy?: string | null;
  time_period_key?: string | null;
  time_period_ar?: string | null;
  latest_order_at?: string | null;
  data_freshness?: string | null;
  unavailable_models?: RecommendationContributingModel[];
}

export type RecommendationEventType = "shown" | "clicked" | "added_to_cart" | "dismissed";

export interface RecommendationEventPayload {
  event_type: RecommendationEventType;
  restaurant_id: number;
  recommended_item_id: number;
  source: string;
  request_id?: string;
  session_id?: string;
  cart_item_ids: number[];
  recommendation_type?: string;
  surface: "cart";
  rank?: number;
  score?: number;
  model_version?: string;
}
