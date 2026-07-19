(function (global) {
  "use strict";

  class SmartSuggestionsWidget {
    constructor(options) {
      this.options = Object.assign({
        apiBaseUrl: "",
        endpoint: "/api/v1/recommendations",
        eventsEndpoint: "/api/v1/recommendation-events",
        restaurantId: null,
        locale: "ar",
        limit: 2,
        maxVisibleItems: 2,
        autoOpen: true,
        onAddRecommendation: function () {}
      }, options || {});
      this.cartItemIds = [];
      this.lastAddedItemId = null;
      this.closedByUser = false;
      this.lastResponse = null;
      this.dragState = null;
      this.root = null;
      this.toggle = null;
    }

    init() {
      if (this.root) return this;
      this.injectMarkup();
      this.bindEvents();
      return this;
    }

    onCartChanged(payload) {
      this.cartItemIds = this.uniqueIds(payload && payload.cartItemIds || []);
      this.lastAddedItemId = payload && payload.lastAddedItemId ? Number(payload.lastAddedItemId) : null;
      if (!this.options.restaurantId) {
        this.renderError("Restaurant is required");
        return;
      }
      if (this.options.autoOpen && !this.closedByUser) {
        this.open();
      } else {
        this.updateToggle();
      }
      this.renderLoading();
      this.fetchSuggestions();
    }

    open() {
      this.closedByUser = false;
      this.root.classList.add("btrw-open");
      this.root.setAttribute("aria-hidden", "false");
      this.updateToggle();
    }

    close() {
      this.closedByUser = true;
      this.root.classList.remove("btrw-open");
      this.root.setAttribute("aria-hidden", "true");
      this.updateToggle();
      this.recordDismissed();
    }

    uniqueIds(values) {
      const out = [];
      const seen = new Set();
      for (const value of values) {
        const id = Number(value);
        if (!Number.isInteger(id) || id < 1 || seen.has(id)) continue;
        seen.add(id);
        out.push(id);
      }
      return out;
    }

    apiUrl(path) {
      return String(this.options.apiBaseUrl || "").replace(/\/$/, "") + path;
    }

    async fetchSuggestions() {
      const body = {
        restaurant_id: Number(this.options.restaurantId),
        cart_item_ids: this.cartItemIds,
        last_added_item_id: this.lastAddedItemId || undefined,
        limit: Number(this.options.limit || 2),
        context: {
          source: "pos_widget",
          channel: "pos",
          locale: this.options.locale || "ar"
        }
      };
      try {
        const res = await fetch(this.apiUrl(this.options.endpoint), {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(body)
        });
        const payload = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(payload.detail || ("Service error " + res.status));
        this.lastResponse = payload;
        this.renderSuggestions(payload);
        this.recordShown(payload);
      } catch (error) {
        this.renderError(error.message || String(error));
      }
    }

    renderLoading() {
      this.root.querySelector("[data-btrw-added]").textContent = this.lastAddedItemId
        ? "آخر صنف أضيف: item_id " + this.lastAddedItemId
        : "السلة محدثة";
      this.root.querySelector("[data-btrw-list]").innerHTML =
        '<div class="btrw-empty">جاري تحميل الاقتراحات...</div>';
    }

    renderError(message) {
      this.open();
      this.root.querySelector("[data-btrw-list]").innerHTML =
        '<div class="btrw-error">' + this.escape(message) + "</div>";
    }

    renderSuggestions(payload) {
      const items = this.pickItems(payload);
      this.root.querySelector("[data-btrw-added]").textContent = this.lastAddedItemId
        ? "آخر صنف أضيف: item_id " + this.lastAddedItemId
        : "اقتراحات حسب السلة";
      if (!items.length) {
        this.root.querySelector("[data-btrw-list]").innerHTML =
          '<div class="btrw-empty">لا توجد توصية مناسبة حاليًا</div>';
        return;
      }
      this.root.querySelector("[data-btrw-list]").innerHTML = items.map((entry, index) => {
        const item = entry.item;
        const title = item.title_ar || item.title_en || ("item_" + item.item_id);
        const scoreLabel = this.formatScorePercent(item.score);
        const typeLabel = this.typeLabel(entry.section, item.recommendation_type);
        return [
          '<div class="btrw-item" data-item-id="' + item.item_id + '" data-section="' + entry.section + '" data-index="' + index + '">',
          '<div class="btrw-item-main">',
          '<strong>' + this.escape(title) + '</strong>',
          '<span>item_id ' + item.item_id + '</span>',
          '<span class="btrw-score">نسبة الأخذ ' + scoreLabel + '</span>',
          '<div class="btrw-badges">',
          '<span>النوع: ' + this.escape(typeLabel) + '</span>',
          '<span>المصدر: ' + this.escape(this.sourceLabel(item.source)) + '</span>',
          '</div>',
          '</div>',
          item.addable === false
            ? '<button type="button" disabled title="' + this.escape(item.disabled_reason || "not_addable") + '">+</button>'
            : '<button type="button" data-btrw-add="' + item.item_id + '">+</button>',
          '</div>'
        ].join("");
      }).join("");
    }

    pickItems(payload) {
      const out = [];
      const seen = new Set();
      const sections = payload && payload.sections || {};
      for (const sectionName of ["based_on_last_item", "based_on_cart", "popular", "similar_alternatives"]) {
        const items = sections[sectionName] || [];
        for (const item of items) {
          const id = Number(item.item_id);
          if (seen.has(id) || item.addable === false) continue;
          seen.add(id);
          out.push({section: sectionName, item: item});
          break;
        }
        if (out.length >= Number(this.options.maxVisibleItems || 2)) return out;
      }
      for (const sectionName of ["based_on_last_item", "based_on_cart", "popular", "similar_alternatives"]) {
        const items = sections[sectionName] || [];
        for (const item of items) {
          const id = Number(item.item_id);
          if (seen.has(id) || item.addable === false) continue;
          seen.add(id);
          out.push({section: sectionName, item: item});
          if (out.length >= Number(this.options.maxVisibleItems || 2)) return out;
        }
      }
      return out;
    }

    async addRecommendation(itemId) {
      const item = this.findItem(itemId);
      if (!item || item.addable === false) return;
      this.options.onAddRecommendation(item);
      if (!this.cartItemIds.includes(Number(item.item_id))) {
        this.cartItemIds.push(Number(item.item_id));
      }
      await this.recordEvent("added_to_cart", item);
      this.onCartChanged({
        cartItemIds: this.cartItemIds,
        lastAddedItemId: Number(item.item_id)
      });
    }

    findItem(itemId) {
      const id = Number(itemId);
      const sections = this.lastResponse && this.lastResponse.sections || {};
      for (const sectionName of Object.keys(sections)) {
        const found = (sections[sectionName] || []).find((item) => Number(item.item_id) === id);
        if (found) return found;
      }
      return null;
    }

    recordShown(payload) {
      const first = this.pickItems(payload)[0];
      if (!first) return;
      this.recordEvent("shown", first.item);
    }

    recordDismissed() {
      if (!this.lastResponse) return;
      const first = this.pickItems(this.lastResponse)[0];
      if (!first) return;
      this.recordEvent("dismissed", first.item);
    }

    async recordEvent(eventType, item) {
      try {
        await fetch(this.apiUrl(this.options.eventsEndpoint), {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            event_type: eventType,
            restaurant_id: Number(this.options.restaurantId),
            recommended_item_id: Number(item.item_id),
            source: item.source || "unknown",
            recommendation_type: item.recommendation_type || "unknown",
            request_id: this.lastResponse && this.lastResponse.request_id,
            cart_item_ids: this.cartItemIds,
            surface: "smart_suggestions_widget",
            score: item.score,
            model_version: this.lastResponse && this.lastResponse.model_version,
            pos_id: "SMART-SUGGESTIONS-WIDGET",
            variant: this.options.locale || "ar",
            timestamp: new Date().toISOString()
          })
        });
      } catch (_error) {
        // Event logging must not block the POS add-to-cart flow.
      }
    }

    injectMarkup() {
      this.root = document.createElement("section");
      this.root.className = "btrw-widget";
      this.root.setAttribute("aria-hidden", "true");
      this.root.innerHTML = [
        '<div class="btrw-head" data-btrw-drag>',
        '<strong><span class="btrw-dots">⋮⋮</span> اقتراحات ذكية</strong>',
        '<button type="button" data-btrw-close>Close</button>',
        '</div>',
        '<div class="btrw-body">',
        '<div class="btrw-added" data-btrw-added>السلة محدثة</div>',
        '<div class="btrw-list" data-btrw-list><div class="btrw-empty">أضف صنفًا لعرض الاقتراحات</div></div>',
        '</div>'
      ].join("");
      this.toggle = document.createElement("button");
      this.toggle.type = "button";
      this.toggle.className = "btrw-toggle";
      this.toggle.innerHTML = 'Smart Suggestions <span data-btrw-count>0</span>';
      document.body.appendChild(this.root);
      document.body.appendChild(this.toggle);
    }

    bindEvents() {
      this.root.addEventListener("click", (event) => {
        const close = event.target.closest("[data-btrw-close]");
        if (close) {
          event.preventDefault();
          this.close();
          return;
        }
        const add = event.target.closest("[data-btrw-add]");
        if (add) {
          event.preventDefault();
          this.addRecommendation(add.getAttribute("data-btrw-add"));
        }
      });
      this.toggle.addEventListener("click", () => this.open());
      const dragHandle = this.root.querySelector("[data-btrw-drag]");
      dragHandle.addEventListener("pointerdown", (event) => this.beginDrag(event));
      dragHandle.addEventListener("pointermove", (event) => this.moveDrag(event));
      dragHandle.addEventListener("pointerup", (event) => this.endDrag(event));
      dragHandle.addEventListener("pointercancel", (event) => this.endDrag(event));
    }

    beginDrag(event) {
      const rect = this.root.getBoundingClientRect();
      this.dragState = {
        pointerId: event.pointerId,
        offsetX: event.clientX - rect.left,
        offsetY: event.clientY - rect.top
      };
      this.root.classList.add("btrw-dragging");
      event.currentTarget.setPointerCapture(event.pointerId);
    }

    moveDrag(event) {
      if (!this.dragState || this.dragState.pointerId !== event.pointerId) return;
      const width = this.root.offsetWidth;
      const height = this.root.offsetHeight;
      const margin = 8;
      const left = Math.max(margin, Math.min(window.innerWidth - width - margin, event.clientX - this.dragState.offsetX));
      const top = Math.max(margin, Math.min(window.innerHeight - height - margin, event.clientY - this.dragState.offsetY));
      this.root.style.left = left + "px";
      this.root.style.top = top + "px";
    }

    endDrag(event) {
      if (!this.dragState || this.dragState.pointerId !== event.pointerId) return;
      this.dragState = null;
      this.root.classList.remove("btrw-dragging");
    }

    updateToggle() {
      const count = this.cartItemIds.length;
      this.toggle.querySelector("[data-btrw-count]").textContent = String(count);
      this.toggle.classList.toggle("btrw-show", this.closedByUser);
    }

    escape(value) {
      return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
        return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"}[ch];
      });
    }

    formatScorePercent(value) {
      const raw = Number(value);
      if (!Number.isFinite(raw)) return "--%";
      const percent = raw <= 1 ? raw * 100 : raw;
      const clamped = Math.max(0, Math.min(100, percent));
      return clamped.toFixed(2).replace(/\.00$/, "") + "%";
    }

    sourceLabel(source) {
      return {
        restaurant_fbt: "FBT داخل المطعم",
        restaurant_popularity: "الأكثر طلبًا داخل المطعم",
        item2vec: "تشابه الأصناف",
        pooled_fbt: "سلوك طلبات مشابه",
        global_common: "Fallback عام"
      }[source] || source || "غير محدد";
    }

    typeLabel(type, fallbackType) {
      const key = type || fallbackType;
      return {
        cross_sell: "مقترح مع السلة",
        popular: "الأكثر شيوعًا",
        similar_alternative: "بديل مشابه",
        based_on_last_item: "مقترح مع آخر صنف",
        based_on_cart: "مقترح مع السلة",
        top_recommendations: "أفضل النتائج"
      }[key] || key || "اقتراح";
    }
  }

  global.SmartSuggestionsWidget = SmartSuggestionsWidget;
})(window);
