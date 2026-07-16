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
  recommendation_context: "popular" | "based_on_cart" | "based_on_last_item" | "similar_alternatives";
  type_label_ar: string;
  model_key: "full_cart" | "last_item" | "similarity" | "popularity";
  model_label_ar: string;
  score_label_ar: string;
  rank: number;
  compatibility_percent: number;
  probability_percent: number | null;
  confidence_band_ar: string;
  model_agreement_count: number;
  meets_threshold: boolean;
  is_available: boolean;
  availability_reason: string | null;
}

export interface WidgetRecommendationModelGroup {
  model_key: "ensemble" | "full_cart" | "last_item" | "similarity" | "popularity";
  label_ar: string;
  description_ar: string;
  available: boolean;
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
  };
  top_recommendations: WidgetRecommendationItem[];
  models: WidgetRecommendationModelGroup[];
  default_model_key: WidgetRecommendationModelGroup["model_key"];
  available_model_keys: WidgetRecommendationModelGroup["model_key"][];
  warnings: string[];
  disabled_reason: string | null;
  threshold_percent: number;
  threshold_fallback_used: boolean;
}
