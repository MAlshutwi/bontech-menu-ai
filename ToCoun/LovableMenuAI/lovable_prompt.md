# Prompt for Lovable

Build this project as an Arabic RTL restaurant menu browser.

- Keep the supplied React files and use `src/lib/menuApi.ts` as the only menu data source.
- The frontend must never receive database credentials. It calls the BonTech read-only API instead.
- On load call `GET /api/menu/restaurants`, show all restaurants in a searchable selector, and on selection call `GET /api/menu/restaurants/{restaurant_id}/items`.
- Render every item with Arabic/English names, category, publication/deletion state, and all configured sizes with dine-in and takeaway prices.
- Keep the “عرض غير المنشور والمحذوف” toggle; it passes `include_inactive=true` to the API.
- Preserve the clean green RTL visual design and responsive mobile layout.
- Later add an AI recommendation panel using a separate API URL. Do not add an AI key or database connection string to the frontend.
