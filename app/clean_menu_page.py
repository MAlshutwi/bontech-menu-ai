"""Clean base trial UI for the recommendation engine."""

CLEAN_MENU_APP_HTML = """<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>تجربة التوصيات</title>
<style>
  :root{
    --bg:#f6f7f9;
    --panel:#ffffff;
    --ink:#17201c;
    --muted:#66736d;
    --line:#dfe5e2;
    --green:#0f6b4f;
    --green-soft:#e8f5ef;
    --blue:#1f5ea8;
    --blue-soft:#eaf2ff;
    --coral:#d85f4a;
    --coral-soft:#fff0ed;
    --warn:#8a6100;
    --warn-soft:#fff7dc;
    --shadow:0 10px 28px rgba(23,32,28,.08);
  }
  *{box-sizing:border-box}
  html,body{min-height:100%}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:Segoe UI,Tahoma,Arial,sans-serif;letter-spacing:0}
  button,input,select{font:inherit}
  button{border:0;border-radius:8px;background:var(--green);color:#fff;padding:10px 13px;font-weight:800;cursor:pointer;min-height:40px}
  button.secondary{background:var(--blue)}
  button.ghost{background:#fff;color:var(--ink);border:1px solid var(--line)}
  button.danger{background:var(--coral)}
  button.icon{width:40px;padding:0;display:grid;place-items:center}
  button:disabled{opacity:.55;cursor:not-allowed}
  input,select{border:1px solid var(--line);border-radius:8px;background:#fff;color:var(--ink);padding:10px 12px;min-height:40px;min-width:0}
  .shell{min-height:100vh;display:flex;flex-direction:column}
  .topbar{background:#fff;border-bottom:1px solid var(--line);padding:14px 18px;display:flex;align-items:center;justify-content:space-between;gap:14px;position:sticky;top:0;z-index:20}
  .brand{display:flex;align-items:center;gap:12px;min-width:220px}
  .brand-mark{width:42px;height:42px;border-radius:8px;background:var(--green);display:grid;place-items:center;color:#fff;font-weight:950;font-size:20px}
  .brand h1{font-size:20px;line-height:1.2;margin:0}
  .brand p{font-size:12px;color:var(--muted);margin:3px 0 0}
  .top-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .pill{display:inline-flex;align-items:center;gap:6px;border-radius:999px;border:1px solid var(--line);background:#fff;padding:7px 10px;font-size:12px;font-weight:800;color:var(--muted);white-space:nowrap}
  .pill.ok{background:var(--green-soft);border-color:#b8dfcf;color:var(--green)}
  .pill.warn{background:var(--warn-soft);border-color:#ead28d;color:var(--warn)}
  .workspace{width:100%;max-width:1680px;margin:0 auto;padding:16px;display:grid;grid-template-columns:320px minmax(0,1fr) 360px;gap:14px;align-items:start}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;box-shadow:var(--shadow);min-width:0}
  .panel-head{padding:14px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:10px}
  .panel-head h2{font-size:16px;margin:0}
  .panel-body{padding:14px}
  .cart-list,.suggestion-list{display:flex;flex-direction:column;gap:9px}
  .cart-row,.suggestion-row{border:1px solid var(--line);border-radius:8px;background:#fff;padding:10px;display:flex;gap:10px;justify-content:space-between;align-items:flex-start}
  .row-title{font-weight:900;font-size:13px;line-height:1.35;overflow-wrap:anywhere}
  .row-meta{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
  .badge{display:inline-flex;align-items:center;border:1px solid var(--line);border-radius:999px;background:#f8faf9;color:var(--muted);font-size:11px;padding:4px 8px;white-space:nowrap}
  .badge.green{background:var(--green-soft);border-color:#b8dfcf;color:var(--green)}
  .badge.blue{background:var(--blue-soft);border-color:#c4d9f7;color:var(--blue)}
  .badge.coral{background:var(--coral-soft);border-color:#f1c1b8;color:var(--coral)}
  .empty{border:1px dashed var(--line);border-radius:8px;padding:18px;text-align:center;color:var(--muted);font-size:13px;line-height:1.6;background:#fbfcfc}
  .cart-actions{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}
  .menu-panel{min-height:calc(100vh - 104px)}
  .menu-tools{padding:14px;border-bottom:1px solid var(--line);display:grid;grid-template-columns:minmax(220px,1fr) minmax(200px,1fr) 150px 120px;gap:9px}
  .category-rail{padding:10px 14px;border-bottom:1px solid var(--line);display:flex;gap:8px;overflow:auto}
  .cat{background:#fff;color:var(--ink);border:1px solid var(--line);border-radius:999px;min-height:34px;padding:7px 11px;white-space:nowrap}
  .cat.active{background:var(--green-soft);border-color:#b8dfcf;color:var(--green)}
  .menu-grid{padding:14px;display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}
  .item-card{border:1px solid var(--line);border-radius:8px;background:#fff;min-height:176px;display:flex;flex-direction:column;overflow:hidden}
  .item-top{height:58px;background:#eef4f1;display:flex;align-items:center;justify-content:space-between;padding:10px}
  .initial{width:38px;height:38px;border-radius:8px;background:#fff;display:grid;place-items:center;color:var(--green);font-weight:950;border:1px solid var(--line)}
  .item-body{padding:11px;display:flex;flex-direction:column;gap:9px;flex:1}
  .item-title{font-size:14px;font-weight:900;line-height:1.35;min-height:38px;overflow-wrap:anywhere}
  .item-actions{margin-top:auto;display:flex;justify-content:space-between;align-items:center;gap:8px}
  .status{font-size:12px;color:var(--muted);line-height:1.5}
  .status.error{color:var(--coral)}
  .status.ok{color:var(--green)}
  .suggestions-panel{position:sticky;top:86px}
  .suggestion-row{align-items:center}
  .suggestion-main{min-width:0}
  .score{font-variant-numeric:tabular-nums}
  .score.percent{background:var(--coral-soft);border-color:#f1c1b8;color:var(--coral);font-weight:900}
  .loading{opacity:.68}
  @media (max-width:1180px){
    .workspace{grid-template-columns:300px minmax(0,1fr)}
    .suggestions-panel{grid-column:1 / -1;position:static}
  }
  @media (max-width:760px){
    .topbar{align-items:flex-start;flex-direction:column}
    .top-actions{width:100%}
    .workspace{grid-template-columns:1fr;padding:10px}
    .menu-tools{grid-template-columns:1fr}
    .cart-actions{grid-template-columns:1fr}
  }
</style>
</head>
<body>
<div class="shell">
  <header class="topbar">
    <div class="brand">
      <div class="brand-mark">AI</div>
      <div>
        <h1 id="restaurantName">تجربة التوصيات</h1>
        <p id="restaurantMeta">تحميل المنيو...</p>
      </div>
    </div>
    <div class="top-actions">
      <span id="healthPill" class="pill">الخدمة</span>
      <button id="refreshBtn" class="ghost">تحديث</button>
      <button id="runBtn" class="secondary">تشغيل الاقتراحات</button>
    </div>
  </header>

  <main class="workspace">
    <aside class="panel">
      <div class="panel-head">
        <h2>السلة</h2>
        <span id="cartCount" class="pill">0</span>
      </div>
      <div class="panel-body">
        <div id="cartList" class="cart-list"></div>
        <div class="cart-actions">
          <button id="starterBtn">صنف البداية</button>
          <button id="clearBtn" class="danger">مسح</button>
        </div>
        <p id="cartStatus" class="status">-</p>
      </div>
    </aside>

    <section class="panel menu-panel">
      <div class="menu-tools">
        <select id="restaurantSelect" aria-label="اختر المطعم"><option>تحميل المطاعم...</option></select>
        <input id="searchInput" placeholder="بحث بالاسم أو item_id"/>
        <select id="signalFilter">
          <option value="all">كل الأصناف</option>
        </select>
        <select id="limitSelect">
          <option value="80">80</option>
          <option value="160">160</option>
          <option value="9999">الكل</option>
        </select>
      </div>
      <div id="categoryRail" class="category-rail"></div>
      <div id="menuGrid" class="menu-grid"></div>
    </section>

    <aside class="panel suggestions-panel">
      <div class="panel-head">
        <h2>الاقتراحات</h2>
        <span id="recoState" class="pill">جاهز</span>
      </div>
      <div class="panel-body">
        <div id="suggestions" class="suggestion-list"></div>
        <p id="recoStatus" class="status">-</p>
      </div>
    </aside>
  </main>
</div>

<script>
let restaurantId = null;
const $ = (id) => document.getElementById(id);

let menuPayload = null;
let items = [];
let itemById = new Map();
let cart = [];
let selectedCategory = "all";
let starterItemId = null;
let lastResponse = null;
let isLoadingRecommendations = false;

function escapeHtml(value){
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"
  }[ch]));
}

function scorePercent(value){
  const n = Number(value);
  if(!Number.isFinite(n)) return null;
  return Math.max(0, Math.min(100, n >= 1 ? 100 : n * 100));
}

function formatScorePercent(value){
  const pct = scorePercent(value);
  if(pct == null) return "--%";
  return pct.toFixed(0) + "%";
}

function formatRawScore(value){
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(3) : "-";
}

function titleOf(item){
  if(!item) return "";
  return item.title_ar || item.title_en || `item_${item.item_id}`;
}

function initialFor(item){
  return String(titleOf(item)).trim().slice(0,1).toUpperCase() || "?";
}

function uniqueCartIds(){
  return [...new Set(cart.map(Number))];
}

function quantityOf(itemId){
  itemId = Number(itemId);
  return cart.filter((id) => Number(id) === itemId).length;
}

function setText(id, value){
  $(id).textContent = value;
}

async function refreshHealth(){
  try{
    const res = await fetch("/health");
    const payload = await res.json();
    $("healthPill").className = payload.kill_switch_active ? "pill warn" : "pill ok";
    $("healthPill").textContent = payload.kill_switch_active ? "متوقف" : "متصل";
  }catch(_err){
    $("healthPill").className = "pill warn";
    $("healthPill").textContent = "غير متصل";
  }
}

async function loadMenu(){
  if(!restaurantId) return;
  $("menuGrid").innerHTML = `<div class="empty">تحميل...</div>`;
  const res = await fetch(`/demo/restaurants/${restaurantId}/live-menu?include_inactive=true`);
  if(!res.ok) throw new Error(`menu ${res.status}`);
  menuPayload = await res.json();
  items = menuPayload.items || [];
  itemById = new Map(items.map((item) => [Number(item.item_id), item]));
  starterItemId = Number(menuPayload.default_cart_item_ids?.[0] || 0) || null;
  if(starterItemId && !itemById.has(starterItemId)){
    starterItemId = items.find((item) => item.has_cross_sell)?.item_id || items[0]?.item_id || null;
  }
  setText("restaurantName", menuPayload.restaurant_name || `Restaurant ${restaurantId}`);
  setText("restaurantMeta", `${items.length} صنف من قاعدة البيانات · تشمل غير المنشورة والمحذوفة`);
  renderCategories();
  renderMenu();
  if(!cart.length && starterItemId){
    addToCart(starterItemId, {skipRecommendations:true});
  }
  renderCart();
}

function renderCategories(){
  const categories = menuPayload?.categories || [];
  const all = `<button class="cat ${selectedCategory === "all" ? "active" : ""}" data-category="all">الكل · ${items.length}</button>`;
  const buttons = categories.map((cat) => {
    const key = String(cat.category_id);
    return `<button class="cat ${selectedCategory === key ? "active" : ""}" data-category="${key}">${escapeHtml(cat.category || key)} · ${cat.count}</button>`;
  });
  $("categoryRail").innerHTML = [all, ...buttons].join("");
  $("categoryRail").querySelectorAll(".cat").forEach((btn) => {
    btn.addEventListener("click", () => {
      selectedCategory = btn.dataset.category || "all";
      renderCategories();
      renderMenu();
    });
  });
}

function filteredItems(){
  const q = $("searchInput").value.trim().toLowerCase();
  const signal = $("signalFilter").value;
  const limit = Number($("limitSelect").value);
  return items.filter((item) => {
    if(selectedCategory !== "all" && String(item.category_id) !== selectedCategory) return false;
    if(signal === "cross" && !item.has_cross_sell) return false;
    if(signal === "similar" && !item.has_similar_alternatives) return false;
    if(signal === "popular" && item.popularity_rank == null) return false;
    if(!q) return true;
    return `${item.item_id} ${item.title_ar || ""} ${item.title_en || ""}`.toLowerCase().includes(q);
  }).slice(0, limit);
}

function renderMenu(){
  const visible = filteredItems();
  if(!visible.length){
    $("menuGrid").innerHTML = `<div class="empty">لا توجد نتائج مطابقة</div>`;
    return;
  }
  $("menuGrid").innerHTML = visible.map((item) => {
    const qty = quantityOf(item.item_id);
    const tags = [
      `<span class="badge">#${item.item_id}</span>`,
      item.popularity_rank == null ? "" : `<span class="badge coral">الأكثر طلبًا ${item.popularity_rank}</span>`,
      item.has_cross_sell ? `<span class="badge blue">cross-sell</span>` : "",
      item.has_similar_alternatives ? `<span class="badge green">بدائل</span>` : "",
    ].join("");
    return `<article class="item-card">
      <div class="item-top">
        <span class="initial">${escapeHtml(initialFor(item))}</span>
        ${qty ? `<span class="badge green">في السلة ${qty}</span>` : `<span class="badge">متاح</span>`}
      </div>
      <div class="item-body">
        <div class="item-title">${escapeHtml(titleOf(item))}</div>
        <div class="row-meta">${tags}</div>
        <div class="item-actions">
          <button onclick="addToCart(${Number(item.item_id)})">${qty ? "زيادة" : "أضف"}</button>
        </div>
      </div>
    </article>`;
  }).join("");
}

function renderCart(){
  $("cartCount").textContent = `${cart.length}`;
  if(!cart.length){
    $("cartList").innerHTML = `<div class="empty">السلة فارغة</div>`;
    $("cartStatus").textContent = "أضف صنفًا لعرض اقتراحات مرتبطة.";
    return;
  }
  $("cartList").innerHTML = uniqueCartIds().map((id) => {
    const item = itemById.get(Number(id));
    const qty = quantityOf(id);
    return `<div class="cart-row">
      <div>
        <div class="row-title">${escapeHtml(titleOf(item))}</div>
        <div class="row-meta"><span class="badge">#${id}</span><span class="badge green">qty ${qty}</span></div>
      </div>
      <button class="icon ghost" title="حذف" onclick="removeFromCart(${id})">×</button>
    </div>`;
  }).join("");
  $("cartStatus").textContent = `${uniqueCartIds().length} صنف فريد`;
}

function addToCart(itemId, options={}){
  itemId = Number(itemId);
  if(!itemById.has(itemId)){
    $("cartStatus").textContent = `الصنف ${itemId} غير موجود في منيو المطعم.`;
    return;
  }
  cart.push(itemId);
  renderCart();
  renderMenu();
  if(!options.skipRecommendations){
    runRecommendations();
  }
}

function removeFromCart(itemId){
  cart = cart.filter((id) => Number(id) !== Number(itemId));
  renderCart();
  renderMenu();
  runRecommendations();
}

function clearCart(){
  cart = [];
  lastResponse = null;
  renderCart();
  renderMenu();
  renderSuggestions();
}

function addStarter(){
  if(starterItemId) addToCart(starterItemId);
}

async function runRecommendations(){
  if(isLoadingRecommendations) return;
  isLoadingRecommendations = true;
  $("recoState").className = "pill";
  $("recoState").textContent = "تحميل";
  $("suggestions").classList.add("loading");
  try{
    const body = {
      restaurant_id: restaurantId,
      cart_item_ids: uniqueCartIds(),
      top_k: 8,
      include_types: ["cross_sell","similar_alternative","popular"],
      context: {pos_id:"BASE-TRIAL", order_type:"dine_in", timestamp:new Date().toISOString()}
    };
    const res = await fetch("/recommendations", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(body)
    });
    const payload = await res.json().catch(() => ({}));
    if(!res.ok) throw new Error(payload.detail || `recommendations ${res.status}`);
    lastResponse = payload;
    renderSuggestions();
  }catch(err){
    $("suggestions").innerHTML = `<div class="empty">تعذر تحميل الاقتراحات</div>`;
    $("recoStatus").className = "status error";
    $("recoStatus").textContent = String(err.message || err);
  }finally{
    isLoadingRecommendations = false;
    $("suggestions").classList.remove("loading");
  }
}

function groupLabel(type){
  return {
    cross_sell:"مقترح مع السلة",
    similar_alternative:"بدائل مشابهة",
    popular:"الأكثر طلبًا"
  }[type] || type;
}

function sourceLabel(source){
  return {
    restaurant_fbt:"يُطلب مع السلة",
    restaurant_popularity:"شائع",
    global_common:"شائع عام",
    item2vec:"مشابه",
    pooled_fbt:"سلوك مشابه"
  }[source] || source || "source";
}

function collectSuggestions(){
  const out = [];
  const seen = new Set(uniqueCartIds());
  const groups = lastResponse?.recommendation_groups || [];
  for(const type of ["cross_sell","similar_alternative","popular"]){
    const group = groups.find((entry) => entry.type === type);
    for(const item of group?.items || []){
      const id = Number(item.item_id);
      if(seen.has(id) || !itemById.has(id)) continue;
      seen.add(id);
      out.push({type, item});
      if(out.length >= 10) return out;
    }
  }
  return out;
}

function renderSuggestions(){
  if(!lastResponse){
    $("suggestions").innerHTML = `<div class="empty">لا توجد اقتراحات بعد</div>`;
    $("recoStatus").className = "status";
    $("recoStatus").textContent = "-";
    $("recoState").className = "pill";
    $("recoState").textContent = "جاهز";
    return;
  }
  const suggestions = collectSuggestions();
  $("recoState").className = lastResponse.fallback_used ? "pill warn" : "pill ok";
  $("recoState").textContent = lastResponse.fallback_used ? "Fallback" : "نشط";
  $("recoStatus").className = "status ok";
  $("recoStatus").textContent = `request_id ${lastResponse.request_id || "-"}`;
  if(!suggestions.length){
    $("suggestions").innerHTML = `<div class="empty">لا توجد اقتراحات قابلة للإضافة لهذه السلة</div>`;
    return;
  }
  $("suggestions").innerHTML = suggestions.map(({type, item}) => `
    <div class="suggestion-row">
      <div class="suggestion-main">
        <div class="row-title">${escapeHtml(titleOf(item))}</div>
        <div class="row-meta">
          <span class="badge green">${escapeHtml(groupLabel(type))}</span>
          <span class="badge blue">${escapeHtml(sourceLabel(item.source))}</span>
          <span class="badge score percent">نسبة الأخذ ${formatScorePercent(item.score)}</span>
          <span class="badge score">score ${formatRawScore(item.score)}</span>
        </div>
      </div>
      <button class="icon" title="أضف" onclick="addToCart(${Number(item.item_id)})">+</button>
    </div>
  `).join("");
}

async function init(){
  try{
    await refreshHealth();
    await loadRestaurants();
    await loadMenu();
    await runRecommendations();
  }catch(err){
    $("menuGrid").innerHTML = `<div class="empty">تعذر تحميل التجربة</div>`;
    $("cartStatus").textContent = String(err.message || err);
  }
}

async function loadRestaurants(){
  const res = await fetch("/demo/restaurants");
  if(!res.ok) throw new Error(`restaurants ${res.status}`);
  const payload = await res.json();
  const restaurants = payload.restaurants || [];
  if(!restaurants.length) throw new Error("لا توجد مطاعم في قاعدة البيانات");
  $("restaurantSelect").innerHTML = restaurants.map((restaurant) => {
    const id = Number(restaurant.restaurant_id);
    const name = restaurant.name_ar || restaurant.name || `Restaurant ${id}`;
    return `<option value="${id}">${escapeHtml(name)} · #${id}</option>`;
  }).join("");
  restaurantId = Number(restaurants[0].restaurant_id);
  $("restaurantSelect").value = String(restaurantId);
}

$("refreshBtn").addEventListener("click", async () => {
  await loadMenu();
  await runRecommendations();
});
$("runBtn").addEventListener("click", runRecommendations);
$("starterBtn").addEventListener("click", addStarter);
$("clearBtn").addEventListener("click", clearCart);
$("searchInput").addEventListener("input", renderMenu);
$("signalFilter").addEventListener("change", renderMenu);
$("limitSelect").addEventListener("change", renderMenu);
$("restaurantSelect").addEventListener("change", async (event) => {
  restaurantId = Number(event.target.value);
  cart = [];
  lastResponse = null;
  selectedCategory = "all";
  await loadMenu();
  await runRecommendations();
});

init();
</script>
</body>
</html>
"""
