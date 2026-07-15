import type { Restaurant, RestaurantMenuResponse } from "../types/menu";

const DEFAULT_API_URL = "https://bontech-menu-ai.onrender.com";
const configuredBaseUrl = import.meta.env.VITE_MENU_API_URL?.trim() || DEFAULT_API_URL;
const baseUrl = configuredBaseUrl.replace(/\/$/, "");

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `تعذر تحميل البيانات (${response.status})`);
  return body as T;
}

export async function getRestaurants(): Promise<Restaurant[]> {
  const response = await getJson<{ restaurants: Restaurant[] }>("/api/menu/restaurants");
  return response.restaurants;
}

export function getRestaurantMenu(restaurantId: number, includeInactive: boolean): Promise<RestaurantMenuResponse> {
  return getJson<RestaurantMenuResponse>(
    `/api/menu/restaurants/${restaurantId}/items?include_inactive=${includeInactive}`,
  );
}
