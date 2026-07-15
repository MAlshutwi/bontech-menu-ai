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
  warnings: string[];
  disabled_reason: string | null;
}
