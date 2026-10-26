"use strict";
/* Google Fotoğraflar benzeri zaman çizelgesi:
   en yeniden eskiye ay başlıkları, sağda sürüklenebilir tarih çubuğu, aşağı indikçe yükleme. */
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const UNDATED = "tarihsiz";

  function monthKey(t) {
    if (!t) return UNDATED;
    const d = new Date(t * 1000);
    return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
  }
  function monthLabel(key, withYear = true) {
    if (key === UNDATED) return "Tarihsiz";
    const [y, m] = key.split("-").map(Number);
    return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString("tr-TR", { month: "long", ...(withYear ? { year: "numeric" } : {}), timeZone: "UTC" });
  }
  const fmtDur = (s) => (s ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}` : "");
  window.fmtDur = fmtDur;
  const dayLabel = (t) => (t ? new Date(t * 1000).toLocaleDateString("tr-TR", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }) : "");

  class Timeline {
    constructor(root, { onOpen, pageSize = 10 } = {}) {
      this.root = root;
      this.onOpen = onOpen;
      this.pageSize = pageSize;
      root.classList.add("tl");
      root.innerHTML = `
        <div class="tl-scroll">
          <div class="tl-body"></div>
          <div class="tl-status"></div>
          <div class="tl-sentinel"></div>
        </div>
        <div class="tl-scrub" hidden>
          <div class="tl-marks"></div>
          <div class="tl-thumb"><span></span></div>
          <div class="tl-hover"><span></span></div>
        </div>`;
      this.scroll = root.querySelector(".tl-scroll");
      this.body = root.querySelector(".tl-body");
      this.status = root.querySelector(".tl-status");
      this.scrub = root.querySelector(".tl-scrub");
      this.marks = root.querySelector(".tl-marks");
      this.thumb = root.querySelector(".tl-thumb");
      this.hover = root.querySelector(".tl-hover");
      this.token = 0;
      this.reset();

      // aşağı inildikçe sıradaki fotoğraflar: görünür alanın 1200 px altına gelince yükle
      new IntersectionObserver((e) => {
        if (e.some((x) => x.isIntersecting)) this.fill();
      }, { root: this.scroll, rootMargin: "1200px 0px" }).observe(root.querySelector(".tl-sentinel"));

      this.scroll.addEventListener("scroll", () => this.onScroll(), { passive: true });
      this.body.addEventListener("click", (e) => {
        const t = e.target.closest(".tl-tile");
        if (t && this.onOpen) this.onOpen(this.list, +t.dataset.i);
      });
      this.bindScrubber();
      new ResizeObserver(() => this.renderMarks()).observe(this.scrub);
    }

    reset() {
      this.token++;
      this.people = null;
      this.items = [];
      this.total = 0;
      this.months = [];
      this.sections = new Map();
      this.loading = null;
      this.body.innerHTML = "";
      this.status.textContent = "";
      this.marks.innerHTML = "";
      this.scrub.hidden = true;
      this.scroll.scrollTop = 0;
      const self = this;
      this.list = { get items() { return self.items; }, total: () => self.total, more: () => self.more() };
    }

    /* ---------------------------------------------------------- veri */
    async load(people, { emptyText = "Fotoğraf yok." } = {}) {
      this.reset();
      this.people = [...people].sort();
      this.emptyText = emptyText;
      const token = this.token;
      this.status.innerHTML = '<span class="tl-spin"></span>';
      const r = await fetch(`/api/timeline?people=${encodeURIComponent(this.people.join(","))}`);
      if (token !== this.token) return;
      if (!r.ok) { this.status.textContent = "Yüklenemedi."; return; }
      const data = await r.json();
      this.total = data.total;
      this.months = data.months;
      this.status.textContent = "";
      if (!this.total) {
        this.status.textContent = emptyText;
        return;
      }
      this.scrub.hidden = this.months.length < 2;
      this.renderMarks();
      await this.more();
      this.fill();
    }

    // tek seferde bir sayfa; aynı anda tek istek
    more(count = this.pageSize) {
      if (this.loading) return this.loading;
      if (!this.people || this.items.length >= this.total) return Promise.resolve();
      const token = this.token;
      const offset = this.items.length;
      const limit = Math.min(Math.max(count, 1), 1000);
      this.status.innerHTML = '<span class="tl-spin"></span>';
      this.loading = fetch(`/api/photos?people=${encodeURIComponent(this.people.join(","))}&offset=${offset}&limit=${limit}`)
        .then((r) => r.json())
        .then((data) => {
          if (token !== this.token) return;
          this.append(data.items);
        })
        .catch(() => { if (token === this.token) this.status.textContent = "Fotoğraflar yüklenemedi."; })
        .finally(() => {
          if (token !== this.token) return;
          this.loading = null;
          if (!this.status.textContent.startsWith("Fotoğraflar yüklenemedi"))
            this.status.textContent = this.items.length >= this.total ? `${this.total.toLocaleString("tr-TR")} fotoğrafın hepsi` : "";
        });
      return this.loading;
    }

    // görünür alan dolana kadar yüklemeye devam et
    async fill() {
      const sentinel = this.root.querySelector(".tl-sentinel");
      while (this.people && this.items.length < this.total) {
        const gap = sentinel.getBoundingClientRect().top - this.scroll.getBoundingClientRect().bottom;
        if (gap > 1200) break;
        const before = this.items.length;
        await this.more();
        if (this.items.length === before) break;
      }
    }

    async ensure(n) {
      while (this.items.length < Math.min(n, this.total)) {
        const before = this.items.length;
        await this.more(n - this.items.length);
        if (this.items.length === before) break;
      }
    }

    append(items) {
      for (const it of items) {
        const i = this.items.push(it) - 1;
        const key = monthKey(it.taken);
        let sec = this.sections.get(key);
        if (!sec) {
          const m = this.months.find((x) => x.key === key);
          const el = document.createElement("section");
          el.className = "tl-section";
          el.dataset.key = key;
          el.innerHTML = `<h4 class="tl-head"><span>${esc(monthLabel(key))}</span><small>${m ? m.count.toLocaleString("tr-TR") + " fotoğraf" : ""}</small></h4><div class="tl-grid"></div>`;
          this.body.appendChild(el);
          sec = { el, grid: el.querySelector(".tl-grid") };
          this.sections.set(key, sec);
        }
        const b = document.createElement("button");
        b.className = "tl-tile";
        b.dataset.i = i;
        b.title = dayLabel(it.taken);
        const play = it.type === "video"
          ? `<span class="play"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 4v16l13-8z"/></svg>${esc(fmtDur(it.dur))}</span>` : "";
        b.innerHTML = `<img src="${esc(it.thumb)}" alt="" loading="lazy" decoding="async">${play}<span class="d">${esc(dayLabel(it.taken))}</span>`;
        sec.grid.appendChild(b);
      }
      this.onScroll();
    }

    /* ---------------------------------------------------------- tarih çubuğu */
    fracOf(month) { return this.total ? month.offset / this.total : 0; }

    renderMarks() {
      if (!this.months.length || this.scrub.hidden) return;
      const h = this.scrub.clientHeight || 1;
      let lastLabelY = -99, lastYear = null;
      const html = [];
      for (const m of this.months) {
        const y = this.fracOf(m) * h;
        const year = m.key === UNDATED ? "—" : m.key.slice(0, 4);
        if (year !== lastYear && y - lastLabelY >= 18) {
          html.push(`<span class="tl-year" style="top:${y}px">${year}</span>`);
          lastLabelY = y;
        } else {
          html.push(`<i class="tl-dot" style="top:${y}px"></i>`);
        }
        lastYear = year;
      }
      this.marks.innerHTML = html.join("");
      this.onScroll();
    }

    monthAt(frac) {
      const target = frac * this.total;
      let best = this.months[0];
      for (const m of this.months) {
        if (m.offset <= target) best = m;
        else break;
      }
      return best;
    }

    currentMonth() {
      const top = this.scroll.getBoundingClientRect().top + 8;
      let cur = null;
      for (const sec of this.sections.values()) {
        if (sec.el.getBoundingClientRect().top <= top) cur = sec.el.dataset.key;
        else break;
      }
      return cur || this.sections.keys().next().value;
    }

    onScroll() {
      if (this.scrub.hidden || !this.months.length) return;
      const key = this.currentMonth();
      const m = this.months.find((x) => x.key === key);
      if (!m) return;
      this.thumb.style.top = this.fracOf(m) * this.scrub.clientHeight + "px";
      this.thumb.firstElementChild.textContent = monthLabel(key);
      this.root.classList.add("tl-scrolling");
      clearTimeout(this._st);
      this._st = setTimeout(() => this.root.classList.remove("tl-scrolling"), 900);
    }

    async jump(month) {
      if (!month) return;
      await this.ensure(month.offset + this.pageSize);
      const sec = this.sections.get(month.key);
      if (sec) this.scroll.scrollTop = sec.el.offsetTop - 4;
      this.fill();
    }

    bindScrubber() {
      const frac = (e) => {
        const r = this.scrub.getBoundingClientRect();
        return Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
      };
      const showHover = (e) => {
        const f = frac(e);
        const m = this.monthAt(f);
        this.hover.style.top = f * this.scrub.clientHeight + "px";
        this.hover.firstElementChild.textContent = m ? monthLabel(m.key) : "";
        return m;
      };
      let dragging = false, pending = null;
      this.scrub.addEventListener("pointermove", (e) => {
        this.scrub.classList.add("hovering");
        const m = showHover(e);
        if (dragging && m && m !== pending) {
          pending = m;
          // sürüklerken başlığa anında git (yüklüyse), bırakınca kesin yere atla
          const sec = this.sections.get(m.key);
          if (sec) this.scroll.scrollTop = sec.el.offsetTop - 4;
        }
      });
      this.scrub.addEventListener("pointerleave", () => { if (!dragging) this.scrub.classList.remove("hovering"); });
      this.scrub.addEventListener("pointerdown", (e) => {
        dragging = true;
        this.scrub.setPointerCapture(e.pointerId);
        pending = showHover(e);
        e.preventDefault();
      });
      const end = (e) => {
        if (!dragging) return;
        dragging = false;
        this.scrub.classList.remove("hovering");
        this.jump(this.monthAt(frac(e)));
      };
      this.scrub.addEventListener("pointerup", end);
      this.scrub.addEventListener("pointercancel", () => { dragging = false; this.scrub.classList.remove("hovering"); });
    }
  }

  window.Timeline = Timeline;
})();
