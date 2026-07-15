"""app/demo_page.py - standalone Demo Lab HTML served by FastAPI on /ai-demo."""

DEMO_HTML = """<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>BonTech AI — Recommendation Demo Lab</title>
<style>
 body{font-family:system-ui,Segoe UI,Tahoma,sans-serif;margin:0;background:#0f1216;color:#e6e9ee}
 header{padding:14px 20px;background:#161b22;border-bottom:1px solid #283041}
 h1{font-size:18px;margin:0}
 .sub{color:#8b94a3;font-size:12px;margin-top:4px}
 .wrap{display:flex;gap:16px;padding:16px;flex-wrap:wrap}
 .panel{background:#161b22;border:1px solid #283041;border-radius:10px;padding:14px;flex:1;min-width:320px}
 .panel h2{font-size:14px;margin:0 0 10px;color:#c8d0db}
 label{font-size:12px;color:#8b94a3;display:block;margin:8px 0 3px}
 select,input{width:100%;background:#0f1216;color:#e6e9ee;border:1px solid #283041;border-radius:6px;padding:7px}
 button{background:#2d6cdf;color:#fff;border:0;border-radius:6px;padding:8px 12px;margin:4px 4px 0 0;cursor:pointer;font-size:13px}
 button.sec{background:#30384a}
 button:hover{filter:brightness(1.1)}
 .scn button{display:inline-block}
 pre{background:#0b0e12;border:1px solid #283041;border-radius:8px;padding:10px;overflow:auto;font-size:12px;max-height:360px}
 .pass{color:#3fb950;font-weight:700}.fail{color:#f85149;font-weight:700}.warn{color:#d29922;font-weight:700}
 .grp{border:1px solid #283041;border-radius:8px;margin:6px 0;padding:8px}
 .grp h3{margin:0 0 6px;font-size:13px;color:#79c0ff}
 .item{font-size:12px;padding:3px 0;border-bottom:1px dashed #222a36}
 .badge{display:inline-block;background:#21262d;border:1px solid #30363d;border-radius:10px;padding:1px 7px;font-size:11px;color:#8b94a3;margin-inline-start:6px}
 .row{display:flex;gap:10px;flex-wrap:wrap}.row>div{flex:1;min-width:120px}
 .kv{font-size:12px;color:#8b94a3}
</style>
</head>
<body>
<header>
  <h1>BonTech AI — Recommendation Demo Lab</h1>
  <div class="sub">محرّك توصيات backend-only • يخدم من artifacts مُسبقة • اختبار سيناريوهات حيّة مقابل الـ API</div>
</header>
<div class="wrap">
  <div class="panel" style="max-width:340px;flex:0 0 320px">
    <h2>الإعداد</h2>
    <label>المشروع</label>
    <select id="project"><option>BonTech Recommendation Engine</option></select>
    <label>مصدر البيانات</label>
    <select id="source">
      <option value="live">Live (precomputed artifacts / read-only origin)</option>
      <option value="mock">Mock (عرض ثابت بلا backend)</option>
      <option value="staging" disabled>Staging API (غير موصول في الديمو)</option>
      <option value="readonly_db" disabled>Read-only DB (offline training فقط)</option>
    </select>
    <label>X-API-Key (اختياري، لو require_api_key=true)</label>
    <input id="apikey" placeholder="(فارغ = بلا مصادقة)"/>
    <label>restaurant_id</label>
    <input id="rid" value="277"/>
    <label>cart_item_ids (مفصولة بفاصلة)</label>
    <input id="cart" value="6706"/>
    <h2 style="margin-top:14px">سيناريوهات</h2>
    <div class="scn">
      <button onclick="scNormal()">توصية عادية</button>
      <button onclick="scEmpty()">سلة فارغة</button>
      <button onclick="scUnknownRest()">مطعم غير معروف</button>
      <button onclick="scUnknownItem()">صنف غير معروف</button>
      <button onclick="scDupCheck()">فحص التكرار</button>
      <button onclick="scEvent()">تسجيل حدث</button>
    </div>
    <h2 style="margin-top:14px">لوحة الأمان/التشغيل</h2>
    <div class="scn">
      <button class="sec" onclick="health()">Health + Kill switch</button>
      <button class="sec" onclick="metrics()">Metrics</button>
    </div>
  </div>

  <div class="panel">
    <h2>النتيجة</h2>
    <div class="row">
      <div class="kv">السيناريو: <b id="oScn">—</b></div>
      <div class="kv">latency: <b id="oLat">—</b></div>
      <div class="kv">fallback: <b id="oFb">—</b></div>
      <div class="kv">النتيجة: <span id="oPass">—</span></div>
    </div>
    <div class="kv" id="oReason" style="margin:6px 0"></div>
    <div id="oGroups"></div>
    <h2 style="margin-top:12px">Raw</h2>
    <pre id="oRaw">جاهز…</pre>
  </div>
</div>

<script>
const $=id=>document.getElementById(id);
function rid(){return parseInt($('rid').value||'0',10)}
function cart(){return ($('cart').value||'').split(',').map(s=>s.trim()).filter(Boolean).map(Number)}
function hdrs(){const h={'Content-Type':'application/json'};const k=$('apikey').value.trim();if(k)h['X-API-Key']=k;return h}
function show(scn,res,latency,pass,reason){
  $('oScn').textContent=scn; $('oLat').textContent=(latency!=null?latency.toFixed(1)+' ms':'—');
  $('oFb').textContent=(res&&res.fallback_used!=null)?res.fallback_used:'—';
  $('oPass').innerHTML=pass==null?'—':(pass?'<span class=pass>PASS</span>':'<span class=fail>FAIL</span>');
  $('oReason').textContent=reason||'';
  const g=$('oGroups'); g.innerHTML='';
  if(res&&res.recommendation_groups){for(const grp of res.recommendation_groups){
    const d=document.createElement('div');d.className='grp';
    d.innerHTML='<h3>'+grp.type+' — '+(grp.title_ar||'')+'</h3>'+
      (grp.items.length?grp.items.map(it=>'<div class=item>'+(it.title_en||it.title_ar||it.item_id)+
        '<span class=badge>id '+it.item_id+'</span>'+'<span class=badge>'+it.source+'</span>'+
        (it.score!=null?'<span class=badge>score '+it.score+'</span>':'')+
        (it.reason?'<span class=badge>'+it.reason+'</span>':'')+'</div>').join(''):'<div class=item>—</div>');
    g.appendChild(d);
  }}
  $('oRaw').textContent=JSON.stringify(res,null,2);
}
async function call(scn, body, check){
  if($('source').value==='mock'){return show(scn,{fallback_used:false,recommendation_groups:[{type:'cross_sell',title_ar:'(mock)',items:[{title_en:'Mock Item A',source:'mock'},{title_en:'Mock Item B',source:'mock'}]}]},0.0,true,'وضع Mock — عرض ثابت بلا backend');}
  const t0=performance.now();
  try{
    const r=await fetch('/recommendations',{method:'POST',headers:hdrs(),body:JSON.stringify(body)});
    const lat=performance.now()-t0;
    if(r.status===503){return show(scn,{detail:'disabled'},lat,false,'Kill switch مفعّل (503)');}
    const j=await r.json();
    const [pass,reason]=check?check(j,r.status):[r.ok,''];
    show(scn,j,lat,pass,reason);
  }catch(e){show(scn,{error:String(e)},performance.now()-t0,false,'خطأ شبكة/استثناء');}
}
function scNormal(){call('توصية عادية',{restaurant_id:rid(),cart_item_ids:cart(),top_k:5,include_types:['cross_sell','similar_alternative','popular','time_based'],context:{pos_id:'DEMO',order_type:'dine_in',timestamp:new Date().toISOString()}},
  j=>{const cs=(j.recommendation_groups||[]).find(g=>g.type==='cross_sell');return [cs&&cs.items.length>0&&!j.fallback_used,'cross_sell غير فارغ وبلا fallback'];});}
function scEmpty(){call('سلة فارغة',{restaurant_id:rid(),cart_item_ids:[],top_k:5,include_types:['cross_sell','popular']},
  j=>[(j.recommendations||[]).length>0,'popularity fallback يعمل']);}
function scUnknownRest(){call('مطعم غير معروف',{restaurant_id:99999999,cart_item_ids:[],top_k:5},
  j=>[Array.isArray(j.recommendations)&&j.fallback_used===true,'لا انهيار + fallback_used=true']);}
function scUnknownItem(){call('صنف غير معروف',{restaurant_id:rid(),cart_item_ids:[999999999],top_k:5},
  j=>[Array.isArray(j.recommendations),'لا انهيار (fallback)']);}
function scDupCheck(){call('فحص التكرار',{restaurant_id:rid(),cart_item_ids:cart(),top_k:10,include_types:['cross_sell']},
  j=>{const cs=(j.recommendation_groups||[]).find(g=>g.type==='cross_sell')||{items:[]};const names=cs.items.map(i=>(i.title_en||'').toLowerCase().trim());const uniq=new Set(names);return [names.length===uniq.size,'لا أسماء مكرّرة في cross_sell ('+names.length+' عنصر)'];});}
async function scEvent(){
  const t0=performance.now();
  try{
    const r=await fetch('/recommendation-events',{method:'POST',headers:hdrs(),body:JSON.stringify(
      {event_type:'added_to_cart',restaurant_id:rid(),recommended_item_id:(cart()[0]||1),source:'restaurant_fbt',recommendation_type:'cross_sell',request_id:'demo-'+Date.now(),surface:'cart'})});
    const lat=performance.now()-t0;const j=await r.json();
    show('تسجيل حدث',j,lat,j&&j.stored===1,'الحدث سُجّل في JSONL');
  }catch(e){show('تسجيل حدث',{error:String(e)},performance.now()-t0,false,'خطأ');}
}
async function health(){const r=await fetch('/health');const j=await r.json();show('Health',j,null,!j.kill_switch_active,'kill_switch_active='+j.kill_switch_active);}
async function metrics(){const r=await fetch('/metrics');const j=await r.json();show('Metrics',j,null,null,'مؤشرات حيّة (تُصفَّر عند restart)');}
</script>
</body></html>
"""
