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
