import type {
  MenuItemAvailabilityResponse,
  Restaurant,
  RestaurantMenuResponse,
  WidgetRecommendationResponse,
} from "../types/menu";

const DEPLOYED_API_URL = "https://bontech-menu-ai.onrender.com";
const host = window.location.hostname;
const isApiHost = host === "127.0.0.1" || host === "localhost" || host.endsWith(".onrender.com");
const DEFAULT_API_URL = isApiHost ? window.location.origin : DEPLOYED_API_URL;
const configuredBaseUrl = import.meta.env.VITE_MENU_API_URL?.trim() || DEFAULT_API_URL;
const baseUrl = configuredBaseUrl.replace(/\/$/, "");

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, init);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `تعذر تحميل البيانات (${response.status})`);
  return body as T;
}

async function postJson<T>(path: string, payload: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `تعذر تحميل الاقتراح (${response.status})`);
  return body as T;
}

export async function getRestaurants(): Promise<Restaurant[]> {
  const response = await getJson<{ restaurants: Restaurant[] }>("/api/menu/restaurants");
  return response.restaurants;
}

export function getRestaurantMenu(
  restaurantId: number,
  includeInactive: boolean,
  fresh = false,
): Promise<RestaurantMenuResponse> {
  const freshness = fresh ? "&fresh=true" : "";
  return getJson<RestaurantMenuResponse>(
    `/api/menu/restaurants/${restaurantId}/items?include_inactive=${includeInactive}${freshness}`,
    fresh ? { cache: "no-store" } : undefined,
  );
}

export function getRestaurantItemAvailability(
  restaurantId: number,
  itemId: number,
): Promise<MenuItemAvailabilityResponse> {
  return getJson<MenuItemAvailabilityResponse>(
    `/api/menu/restaurants/${restaurantId}/items/${itemId}/availability`,
    { cache: "no-store" },
  );
}

export function getRecommendationModels(
  restaurantId: number,
  cartItemIds: number[],
  lastAddedItemId: number | null,
  signal?: AbortSignal,
): Promise<WidgetRecommendationResponse> {
  return postJson<WidgetRecommendationResponse>(
    "/api/recommendations",
    {
      restaurant_id: restaurantId,
      cart_item_ids: [...new Set(cartItemIds)],
      last_added_item_id: lastAddedItemId || undefined,
      // Fetch enough candidates for independent model rails after live
      // stock/category filtering. Each visible rail still shows at most five.
      limit: 30,
      context: { source: "lovable_menu_widget", channel: "web", locale: "ar" },
    },
    signal,
  );
}
