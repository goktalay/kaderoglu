/* Sosyal katman: aile sayfasına ve Duvar'a eklenen ortak bileşenler.
   Aile sayfasında (#viewer varsa) kendini kurar: üst köşede bildirim zili + kullanıcı menüsü, sekmelere "Duvar",
   tam ekran görüntüleyiciye yorum/beğeni paneli. Tüm metinler textContent ile yazılır (XSS yok). */
(() => {
  "use strict";
  if (window.SX) return;
  const SX = (window.SX = {});
  const self = document.currentScript;
  if (!document.querySelector('link[href*="/social/assets/social.css"]')) {
    const v = self && /[?&]v=(\d+)/.exec(self.src);
    document.head.append(Object.assign(document.createElement("link"), { rel: "stylesheet", href: "/social/assets/social.css" + (v ? "?v=" + v[1] : "") }));
  }

  /* ============================================================ yardımcılar */
  const ICON = {
    heart: '<path d="M12 20.5s-7.5-4.6-9.3-9.2C1.4 8 3.3 4.5 6.8 4.5c2.1 0 3.6 1.2 4.4 2.6.8-1.4 2.3-2.6 4.4-2.6 3.5 0 5.4 3.5 4.1 6.8-1.8 4.6-8.7 9.2-8.7 9.2z"/>',
    comment: '<path d="M20.7 16.7A9 9 0 1 0 17 20.4L21.5 21.5z"/>',
    bell: '<path d="M6 16.5V11a6 6 0 1 1 12 0v5.5l1.5 2h-15z"/><path d="M10 21a2.2 2.2 0 0 0 4 0"/>',
    wall: '<rect x="3.5" y="3.5" width="17" height="17" rx="4.5"/><circle cx="12" cy="12" r="4"/><circle cx="17.2" cy="6.8" r="1" fill="currentColor" stroke="none"/>',
    tree: '<circle cx="12" cy="5" r="2.5"/><circle cx="5" cy="19" r="2.5"/><circle cx="19" cy="19" r="2.5"/><path d="M12 7.5V12M5 16.5V12h14v4.5"/>',
    plus: '<rect x="3.5" y="3.5" width="17" height="17" rx="4.5"/><path d="M12 8v8M8 12h8"/>',
    close: '<path d="M6 6l12 12M18 6L6 18"/>',
    key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3M15 8l2 2"/>',
    out: '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 17l5-5-5-5M15 12H4"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4.5 20a7.5 7.5 0 0 1 15 0"/>',
    face: '<path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/><circle cx="12" cy="11" r="3"/><path d="M7.5 17a5 5 0 0 1 9 0"/>',
    check: '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.7 2.7L16 10"/>',
    warn: '<path d="M12 3l9.5 17h-19z"/><path d="M12 10v4M12 17.2v.3"/>',
    more: '<circle cx="5" cy="12" r="1.4" fill="currentColor"/><circle cx="12" cy="12" r="1.4" fill="currentColor"/><circle cx="19" cy="12" r="1.4" fill="currentColor"/>',
  };
  const icon = (name, cls = "sx-icon") =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[name]}</svg>`;
  SX.icon = icon;

  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "html") n.innerHTML = v; // sadece sabit simgeler için
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const k of kids.flat()) if (k != null && k !== false) n.append(k);
    return n;
  }
  SX.el = el;

  function api(url, body, method) {
    const isFile = body instanceof Blob;
    return fetch(url, {
      method: method || (body !== undefined ? "POST" : "GET"),
      credentials: "same-origin",
      headers: { "X-Requested-With": "uzakpanel", ...(body !== undefined && !isFile ? { "Content-Type": "application/json" } : {}) },
      body: body === undefined ? undefined : isFile ? body : JSON.stringify(body),
    }).then(async (r) => {
      const d = await r.json().catch(() => ({}));
      if (r.status === 401 && !url.includes("/login")) {
        location.href = "/giris?next=" + encodeURIComponent(location.pathname + location.search + location.hash);
      }
      if (!r.ok) throw Object.assign(new Error(d.detail || `Hata ${r.status}`), { status: r.status });
      return d;
    });
  }
  SX.api = api;

  let toastT;
  SX.toast = (msg, bad) => {
    document.querySelectorAll(".sx-toast").forEach((t) => t.remove());
    const t = el("div", { class: "sx-root sx-toast" + (bad ? " sx-bad" : ""), role: "status", text: msg });
    document.body.append(t);
    clearTimeout(toastT);
    toastT = setTimeout(() => t.remove(), 3200);
  };

  SX.ago = (ts, long) => {
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 60) return "şimdi";
    if (s < 3600) return Math.floor(s / 60) + (long ? " dakika önce" : " dk");
    if (s < 86400) return Math.floor(s / 3600) + (long ? " saat önce" : " sa");
    if (s < 7 * 86400) return Math.floor(s / 86400) + (long ? " gün önce" : " g");
    if (s < 30 * 86400) return Math.floor(s / 604800) + (long ? " hafta önce" : " hf");
    return new Date(ts * 1000).toLocaleDateString("tr-TR", { day: "numeric", month: "long", year: "numeric" });
  };

  SX.avatar = (u, size) => {
    const st = size ? `width:${size}px;height:${size}px;font-size:${Math.round(size * 0.38)}px` : null;
    if (u && u.avatar) return el("img", { class: "sx-av", src: u.avatar, alt: "", loading: "lazy", style: st });
    const ini = ((u && u.name) || "?").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toLocaleUpperCase("tr");
    return el("span", { class: "sx-av sx-ini", style: st, text: ini });
  };
  SX.userLink = (u) => el("a", { class: "sx-name", href: "/duvar?u=" + encodeURIComponent(u ? u.username : ""), text: u ? u.name : "Silinmiş kullanıcı" });

  /** "@ayse.kaderoglu çok güzel" -> metin + bağlantı düğümleri (innerHTML kullanılmaz) */
  SX.richText = (text) => {
    const frag = document.createDocumentFragment();
    const re = /@([a-z0-9]+(?:\.[a-z0-9]+)*)/gi;
    let last = 0, m;
    const known = new Set((SX.users || []).map((u) => u.username));
    while ((m = re.exec(text))) {
      if (!known.has(m[1].toLowerCase())) continue;
      frag.append(text.slice(last, m.index));
      frag.append(el("a", { class: "sx-mention", href: "/duvar?u=" + encodeURIComponent(m[1].toLowerCase()), text: m[0] }));
      last = m.index + m[0].length;
    }
    frag.append(text.slice(last));
    return frag;
  };

  /* ============================================================ oturum */
  SX.me = null;
  SX.users = [];
  SX.ready = api("/social/api/me")
    .then((me) => {
      SX.me = me;
      if (me.must_change) location.href = "/giris";
      return api("/social/api/users");
    })
    .then((d) => (SX.users = d.users))
    .catch(() => {});

  SX.logout = async () => {
    try { await api("/social/api/logout", {}); } catch { /* yine de çık */ }
    location.href = "/giris";
  };

  /* ============================================================ pencere / açılır kutu */
  let openPop = null;
  function closePop() {
    if (openPop) { openPop.remove(); openPop = null; }
  }
  SX.closePop = closePop;
  function popover(anchor, node) {
    closePop();
    node.classList.add("sx-root", "sx-pop");
    document.body.append(node);
    const r = anchor.getBoundingClientRect();
    const w = node.offsetWidth;
    node.style.top = Math.round(r.bottom + 8) + "px";
    node.style.left = Math.round(Math.max(8, Math.min(window.innerWidth - w - 8, r.right - w + 12))) + "px";
    openPop = node;
    return node;
  }
  document.addEventListener("pointerdown", (e) => {
    if (openPop && !openPop.contains(e.target) && !e.target.closest("[data-sx-pop]")) closePop();
  });

  SX.modal = (content, { onClose, dark } = {}) => {
    closePop();
    const m = el("div", { class: "sx-root sx-modal" });
    const close = () => {
      m.remove();
      document.removeEventListener("keydown", onKey, true);
      if (onClose) onClose();
    };
    const onKey = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); close(); }
    };
    m.addEventListener("click", (e) => { if (e.target === m) close(); });
    if (dark !== false) m.append(el("button", { class: "sx-x", "aria-label": "Kapat", html: icon("close"), onclick: close }));
    m.append(content);
    document.addEventListener("keydown", onKey, true);
    document.body.append(m);
    return { el: m, close };
  };

  /* ============================================================ şifre değiştir */
  SX.changePassword = () => {
    const err = el("div", { class: "sx-err", role: "alert" });
    const f = el("form", {},
      el("label", {}, "Mevcut şifre", el("input", { class: "sx-field", type: "password", name: "current", autocomplete: "current-password", required: true })),
      el("label", {}, "Yeni şifre (en az 8 karakter)", el("input", { class: "sx-field", type: "password", name: "new", autocomplete: "new-password", minlength: 8, required: true })),
      el("label", {}, "Yeni şifre (tekrar)", el("input", { class: "sx-field", type: "password", name: "again", autocomplete: "new-password", required: true })),
      err,
      el("div", { class: "sx-row" }, el("button", { type: "button", class: "sx-btn sx-ghost", text: "Vazgeç", onclick: () => m.close() }),
        el("button", { class: "sx-btn", text: "Kaydet" })));
    const m = SX.modal(el("div", { class: "sx-dialog" }, el("h2", { text: "Şifre değiştir" }), f));
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      const d = Object.fromEntries(new FormData(f));
      if (d.new !== d.again) return (err.textContent = "Yeni şifreler aynı değil");
      try {
        await api("/social/api/password", { current: d.current, new: d.new });
        m.close();
        SX.toast("Şifreniz değiştirildi");
      } catch (ex) { err.textContent = ex.message; }
    });
    f.querySelector("input").focus();
  };

  /* ============================================================ kullanıcı menüsü */
  SX.userButton = () => {
    const b = el("button", { class: "sx-me", "data-sx-pop": "", "aria-label": "Hesabım", title: "Hesabım" });
    SX.ready.then(() => b.append(SX.avatar(SX.me, 28)));
    b.addEventListener("click", () => {
      if (openPop && openPop.classList.contains("sx-menu")) return closePop();
      const me = SX.me || {};
      popover(b, el("div", { class: "sx-menu" },
        el("div", { class: "sx-menu-head" }, SX.avatar(me, 40), el("div", {}, el("b", { text: me.name || "" }), el("small", { text: "@" + (me.username || "") }))),
        el("a", { href: "/duvar?u=" + encodeURIComponent(me.username || ""), html: icon("user") + "<span>Paylaşımlarım</span>" }),
        el("button", { html: icon("key") + "<span>Şifre değiştir</span>", onclick: SX.changePassword }),
        el("button", { html: icon("out") + "<span>Çıkış yap</span>", onclick: SX.logout })));
    });
    return b;
  };

  /* ============================================================ bildirimler */
  const N_TEXT = {
    comment: ["paylaşımına yorum yaptı", true],
    mention: ["senden bahsetti", true],
    tagged: ["bulunduğun bir fotoğrafa yorum yaptı", true],
    like: ["paylaşımını beğendi", false],
    face: ["seni bir fotoğrafta paylaştı", false],
  };
  function notifNode(n) {
    const p = el("p");
    if (n.actor) {
      const [txt, quote] = N_TEXT[n.type] || ["bir şey yaptı", false];
      p.append(el("b", { text: n.actor.name }), " " + txt);
      if (quote && n.body) p.append(": ", el("q", { text: n.body.length > 90 ? n.body.slice(0, 90) + "…" : n.body }));
      if (n.type === "face") p.append(el("span", { class: "sx-muted", text: " · yüzün tanındı, fotoğraf arşivine eklendi" }));
    } else if (n.type === "ready") {
      p.append("Paylaşımın yayında.");
      const names = (n.body || "").split(",").filter(Boolean).map((pid) => (SX.users.find((u) => u.person === pid) || {}).name).filter(Boolean);
      if (names.length) p.append(" Tanınan kişiler: ", el("b", { text: names.join(", ") }), " — onlara haber verildi.");
    } else if (n.type === "held") p.append("Paylaşımın yönetici onayı bekliyor (şimdilik sadece sen görüyorsun).");
    else if (n.type === "failed") p.append("Yüklediğin dosya işlenemedi.");
    p.append(" ", el("span", { class: "sx-time", text: SX.ago(n.created) }));
    const lead = n.actor ? SX.avatar(n.actor, 44) : el("span", { class: "sx-n-sys", html: icon(n.type === "ready" ? "check" : n.type === "face" ? "face" : "warn") });
    return el("div", {
      class: "sx-n" + (n.read ? "" : " sx-unread"), role: "button", tabindex: 0,
      onclick: () => { closePop(); SX.openNotification(n); },
    }, lead, p, n.thumb ? el("img", { class: "sx-n-th", src: n.thumb, alt: "", loading: "lazy" }) : null);
  }
  SX.openNotification = (n) => {
    if (SX.onNotification && SX.onNotification(n)) return;
    if (n.item) SX.openItem(n.item);
  };

  SX.bell = () => {
    const badge = el("span", { class: "sx-badge", hidden: true });
    const b = el("button", { class: "sx-ibtn", "data-sx-pop": "", "aria-label": "Bildirimler", title: "Bildirimler", html: icon("bell") }, badge);
    const setCount = (n) => {
      badge.hidden = !n;
      badge.textContent = n > 99 ? "99+" : n;
      document.title = document.title.replace(/^\(\d+\+?\) /, "") ;
      if (n) document.title = `(${n > 99 ? "99+" : n}) ` + document.title;
    };
    const poll = () => {
      if (document.hidden) return;
      api("/social/api/notifications/count").then((d) => setCount(d.unread)).catch(() => {});
    };
    SX.ready.then(poll);
    SX.pollBell = poll;
    setInterval(poll, 45000);
    document.addEventListener("visibilitychange", poll);
    b.addEventListener("click", async () => {
      if (openPop && openPop.classList.contains("sx-notifs")) return closePop();
      const box = popover(b, el("div", { class: "sx-notifs" }, el("h3", { text: "Bildirimler" }), el("div", { class: "sx-empty" }, el("div", { class: "sx-spin", style: "margin:auto" }))));
      try {
        const d = await api("/social/api/notifications?limit=40");
        box.lastChild.remove();
        if (!d.items.length) {
          box.append(el("div", { class: "sx-empty", html: icon("heart") }, el("p", { text: "Henüz bildirimin yok. Biri seni bir fotoğrafta etiketlediğinde, yorumda andığında ya da paylaşımını beğendiğinde burada görünür." })));
        } else {
          const day = 86400, now = Date.now() / 1000;
          const groups = [["Yeni", (n) => !n.read], ["Bugün", (n) => now - n.created < day], ["Bu hafta", (n) => now - n.created < 7 * day], ["Daha önce", () => true]];
          const used = new Set();
          for (const [title, test] of groups) {
            const list = d.items.filter((n) => !used.has(n.id) && test(n));
            if (!list.length) continue;
            list.forEach((n) => used.add(n.id));
            box.append(el("div", { class: "sx-n-sec", text: title }), ...list.map(notifNode));
          }
        }
        if (d.unread) {
          api("/social/api/notifications/read", { all: true }).catch(() => {});
          setCount(0);
        }
      } catch (ex) {
        box.lastChild.textContent = ex.message;
      }
    });
    return b;
  };

  /* ============================================================ @ ile kişi önerisi */
  function attachMentions(ta, wrap) {
    let box = null, list = [], sel = 0, start = -1;
    const norm = (s) => s.toLocaleLowerCase("tr").normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/ı/g, "i");
    const hide = () => { if (box) box.remove(); box = null; };
    const pick = (u) => {
      const v = ta.value, caret = ta.selectionStart;
      ta.value = v.slice(0, start) + "@" + u.username + " " + v.slice(caret);
      const pos = start + u.username.length + 2;
      ta.setSelectionRange(pos, pos);
      hide();
      ta.dispatchEvent(new Event("input"));
      ta.focus();
    };
    const render = () => {
      hide();
      if (!list.length) return;
      box = el("div", { class: "sx-suggest" }, list.map((u, i) =>
        el("button", { type: "button", class: i === sel ? "sx-on" : "", onmousedown: (e) => { e.preventDefault(); pick(u); } },
          SX.avatar(u, 32), el("span", {}, el("b", { text: u.username }), el("small", { text: u.name })))));
      wrap.append(box);
    };
    ta.addEventListener("input", () => {
      const before = ta.value.slice(0, ta.selectionStart);
      const m = /(^|\s)@([\p{L}0-9.]*)$/u.exec(before);
      if (!m) { list = []; return hide(); }
      start = before.length - m[2].length - 1;
      const q = norm(m[2]);
      list = SX.users.filter((u) => u.username.includes(q) || norm(u.name).includes(q)).slice(0, 6);
      sel = 0;
      render();
    });
    ta.addEventListener("keydown", (e) => {
      if (!box) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        sel = (sel + (e.key === "ArrowDown" ? 1 : list.length - 1)) % list.length;
        render();
      } else if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        e.stopImmediatePropagation();
        pick(list[sel]);
      } else if (e.key === "Escape") { e.stopPropagation(); hide(); }
    });
    ta.addEventListener("blur", () => setTimeout(hide, 150));
  }
  SX.attachMentions = attachMentions;

  function autosize(ta) {
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 96) + "px";
  }

  /* ============================================================ yorumlar + beğeni */
  /** Bir fotoğrafın beğeni/yorum bölümü. opts.caption: gönderi açıklaması {user, text, created} */
  SX.comments = (item, opts = {}) => {
    const root = el("div", { class: "sx-cm" });
    const list = el("div", { class: "sx-cm-list" }, el("div", { class: "sx-spin", style: "margin:20px auto" }));
    const heart = el("button", { class: "sx-ibtn sx-heart", "aria-label": "Beğen", html: icon("heart") });
    const likes = el("div", { class: "sx-likes" });
    const ta = el("textarea", { rows: 1, placeholder: "Yorum ekle…", maxlength: 1000, "aria-label": "Yorum" });
    const send = el("button", { class: "sx-link", text: "Paylaş", disabled: true });
    const form = el("form", { class: "sx-form" }, ta, send);
    const bar = el("div", { class: "sx-cm-bar" },
      el("div", { class: "sx-acts" }, heart, el("button", { class: "sx-ibtn", "aria-label": "Yorum yap", html: icon("comment"), type: "button", onclick: () => ta.focus() }), el("span", { class: "sx-sp" })),
      likes, opts.created ? el("div", { class: "sx-time", style: "padding:0 2px", text: SX.ago(opts.created, true).toLocaleUpperCase("tr") }) : null);
    root.append(list, bar, form);
    attachMentions(ta, form);
    let state = null;

    const renderLikes = () => {
      heart.classList.toggle("sx-on", state.liked);
      heart.setAttribute("aria-pressed", state.liked);
      likes.textContent = "";
      if (!state.likes) likes.append(el("small", { text: "İlk beğenen sen ol" }));
      else {
        const names = (state.likers || []).filter(Boolean).map((u) => u.name.split(" ")[0]);
        likes.append(`${state.likes} beğenme`);
        if (names.length) likes.append(el("small", { text: " · " + names.slice(0, 3).join(", ") + (state.likes > 3 ? " ve diğerleri" : "") }));
      }
    };
    const commentNode = (c) => {
      const mine = SX.me && c.user && c.user.id === SX.me.id;
      const del = (mine || (SX.me && SX.me.admin)) && c.id ? el("button", {
        type: "button", text: "Sil", onclick: async () => {
          if (!confirm("Yorum silinsin mi?")) return;
          try {
            await api(`/social/api/comments/${c.id}`, undefined, "DELETE");
            node.remove();
            state.comment_count--;
            if (opts.onChange) opts.onChange(state);
          } catch (ex) { SX.toast(ex.message, true); }
        },
      }) : null;
      const node = el("div", { class: "sx-c" + (c.caption ? " sx-caption" : "") }, SX.avatar(c.user, 32),
        el("div", { class: "sx-c-body" },
          el("p", { class: "sx-c-text" }, SX.userLink(c.user), " ", SX.richText(c.body)),
          el("div", { class: "sx-c-meta" }, el("span", { text: SX.ago(c.created) }), del)));
      return node;
    };
    const render = () => {
      list.textContent = "";
      if (opts.caption && opts.caption.text) list.append(commentNode({ ...opts.caption, body: opts.caption.text, caption: true }));
      if (state.people && state.people.length) {
        list.append(el("div", { class: "sx-cm-people" }, state.people.map((p) =>
          el("a", { class: "sx-chip", href: p.user ? "/duvar?u=" + encodeURIComponent(p.user.username) : "#" }, SX.avatar(p.user || { name: p.name }, 22), p.name))));
      }
      if (!state.comments.length) list.append(el("p", { class: "sx-muted", style: "text-align:center;margin:24px 0", text: "Henüz yorum yok. İlk yorumu sen yaz." }));
      state.comments.forEach((c) => list.append(commentNode(c)));
      renderLikes();
    };
    const load = async () => {
      await SX.ready;
      state = await api(`/social/api/items/${encodeURIComponent(item)}`);
      render();
      if (opts.onLoad) opts.onLoad(state);
    };
    load().catch((ex) => { list.textContent = ex.message; });

    heart.addEventListener("click", () => SX.like(item).then((d) => {
      Object.assign(state, d);
      if (SX.me) {
        state.likers = (state.likers || []).filter((u) => u && u.id !== SX.me.id);
        if (d.liked) state.likers.unshift(SX.me);
      }
      renderLikes();
      if (opts.onChange) opts.onChange(state);
    }));
    ta.addEventListener("input", () => { send.disabled = !ta.value.trim(); autosize(ta); });
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = ta.value.trim();
      if (!body) return;
      send.disabled = true;
      try {
        const d = await api(`/social/api/items/${encodeURIComponent(item)}/comments`, { body });
        if (!state.comments.length) render();
        state.comments.push(d.comment);
        state.comment_count = d.comment_count;
        list.querySelectorAll("p.sx-muted").forEach((p) => p.remove());
        list.append(commentNode(d.comment));
        list.scrollTop = list.scrollHeight;
        ta.value = "";
        autosize(ta);
        if (opts.onChange) opts.onChange(state);
      } catch (ex) {
        SX.toast(ex.message, true);
      } finally {
        send.disabled = !ta.value.trim();
      }
    });
    root.focusInput = () => ta.focus();
    root.reload = load;
    return root;
  };

  SX.like = (item) => api(`/social/api/items/${encodeURIComponent(item)}/like`, {}).catch((ex) => { SX.toast(ex.message, true); throw ex; });

  /* ============================================================ tek fotoğraf penceresi */
  SX.faceTags = (holder, faces, people) => {
    if (!faces || !faces.length) return;
    const tags = el("div", { class: "sx-tags", hidden: true });
    const byPerson = Object.fromEntries((people || []).map((p) => [p.person, p]));
    for (const [x, y, w, h, pid] of faces) {
      const p = byPerson[pid] || { name: (SX.users.find((u) => u.person === pid) || {}).name || pid };
      tags.append(el("a", {
        class: "sx-tag", style: `left:${(x + w / 2) * 100}%;top:${(y + h) * 100}%`,
        href: p.user ? "/duvar?u=" + encodeURIComponent(p.user.username) : "#", text: p.name,
      }));
    }
    const btn = el("button", { class: "sx-tagbtn", type: "button", "aria-label": "Etiketleri göster", html: icon("user"), onclick: (e) => { e.stopPropagation(); tags.hidden = !tags.hidden; } });
    holder.append(tags, btn);
    holder.addEventListener("click", (e) => { if (e.target.tagName === "IMG") tags.hidden = !tags.hidden; });
  };

  SX.openItem = async (item, extra = {}) => {
    let info;
    try { info = await api(`/social/api/items/${encodeURIComponent(item)}`); }
    catch (ex) { return SX.toast(ex.message, true); }
    const media = el("div", { class: "sx-post-media" });
    if (info.kind === "video") media.append(el("video", { src: info.video, poster: info.full, controls: true, playsinline: true, autoplay: true }));
    else {
      const wrap = el("div", { style: "position:relative;display:inline-block;max-width:100%" }, el("img", { src: info.full, alt: "" }));
      media.append(wrap);
      SX.faceTags(wrap, info.faces, info.people);
    }
    const owner = info.owner;
    const head = el("div", { class: "sx-post-head" }, owner ? SX.avatar(owner, 32) : el("span", { class: "sx-n-sys", style: "width:32px;height:32px", html: icon("tree") }),
      el("div", {}, owner ? SX.userLink(owner) : el("b", { text: "Aile arşivi" }),
        el("small", { text: info.taken ? new Date(info.taken * 1000).toLocaleDateString("tr-TR", { day: "numeric", month: "long", year: "numeric" }) : "" })));
    const cm = SX.comments(item, {
      caption: owner && info.caption ? { user: owner, text: info.caption, created: info.created } : null,
      onChange: extra.onChange,
    });
    const side = el("div", { class: "sx-post-side" }, head, cm);
    const m = SX.modal(el("div", { class: "sx-post" }, media, side), { onClose: extra.onClose });
    if (extra.focus) setTimeout(() => cm.focusInput(), 50);
    return m;
  };

  /* ============================================================ aile sayfası entegrasyonu */
  function itemFromUrl(u) {
    const m = /\/(?:social\/)?media\/(?:full|video|thumb)\/([0-9a-f]{20})\./.exec(u || "");
    return m ? m[1] : null;
  }

  function installFamily() {
    const viewer = document.getElementById("viewer");
    // sekmelere Duvar
    const views = document.querySelector(".views");
    if (views && !views.querySelector(".sx-wall-tab")) {
      views.append(el("a", { class: "view-btn sx-wall-tab", href: "/duvar", html: icon("wall") + " Duvar", style: "text-decoration:none" }));
      const st = views.querySelector(".sx-wall-tab svg");
      st.style.cssText = "width:17px;height:17px";
    }
    // sağ üst köşe
    const corner = el("div", { class: "sx-root sx-corner" },
      el("a", { class: "sx-ibtn", href: "/duvar", "aria-label": "Duvar", title: "Duvar", html: icon("wall") }),
      SX.bell(), SX.userButton());
    document.body.append(corner);
    if (!viewer) return;

    // görüntüleyici: yorum paneli
    const KEY = "sx-panel-open";
    let open = false;
    try { open = localStorage.getItem(KEY) === "1"; } catch { /* özel pencere */ }
    const counts = el("span");
    const vbtn = el("button", { class: "sx-root sx-vbtn", type: "button", "aria-label": "Beğeniler ve yorumlar" }, counts);
    const panel = el("aside", { class: "sx-root sx-vpanel", hidden: true });
    viewer.append(vbtn, panel);
    // panel içindeki tuşlar/dokunmalar görüntüleyiciye (ok tuşları, kaydırma) gitmesin
    for (const t of ["keydown", "touchstart", "touchend", "click"]) panel.addEventListener(t, (e) => { if (t !== "keydown" || e.key !== "Escape") e.stopPropagation(); });

    let cur = null, seq = 0;
    const setCounts = (s) => {
      counts.innerHTML = "";
      counts.append(el("span", { html: icon("heart", "sx-icon" + (s && s.liked ? " sx-heart-mini" : "")) }));
      counts.append(" " + (s ? s.likes : "·") + " ");
      counts.append(el("span", { class: "sx-sep", text: "·" }), " ", el("span", { html: icon("comment") }), " " + (s ? s.comment_count : "·"));
      counts.style.cssText = "display:inline-flex;align-items:center;gap:5px";
    };
    const layout = () => {
      viewer.classList.toggle("sx-with-panel", open && !viewer.hidden && !!cur);
      const h = !open || viewer.hidden || !cur;
      if (panel.hidden !== h) panel.hidden = h;
      window.dispatchEvent(new Event("resize")); // family.js isim etiketlerini yeniden konumlasın
    };
    const renderPanel = () => {
      panel.textContent = "";
      if (!open || !cur) return layout();
      const cm = SX.comments(cur, { onLoad: setCounts, onChange: setCounts });
      panel.append(el("header", {}, el("b", { text: "Yorumlar" }),
        el("button", { class: "sx-ibtn", "aria-label": "Paneli kapat", html: icon("close"), onclick: () => toggle(false) })), cm);
      layout();
    };
    const toggle = (v) => {
      open = v;
      try { localStorage.setItem(KEY, v ? "1" : "0"); } catch { /* önemli değil */ }
      renderPanel();
    };
    vbtn.addEventListener("click", (e) => { e.stopPropagation(); toggle(!open); });

    const sync = () => {
      const img = document.getElementById("vImg"), vid = document.getElementById("vVid");
      const src = vid && !vid.hidden ? vid.getAttribute("poster") || vid.getAttribute("src") : img && img.getAttribute("src");
      const item = viewer.hidden ? null : itemFromUrl(src);
      if (vbtn.hidden !== !item) vbtn.hidden = !item;
      if (item === cur) return layout();
      cur = item;
      if (!item) return layout();
      const my = ++seq;
      setCounts(null);
      if (open) renderPanel();
      else {
        layout();
        api(`/social/api/items/${item}`).then((s) => { if (my === seq) setCounts(s); }).catch(() => {});
      }
    };
    let t = null;
    const schedule = () => { clearTimeout(t); t = setTimeout(sync, 60); };
    // kendi düğmemiz/panelimizdeki değişiklikler yeniden tetiklemesin
    const mo = new MutationObserver((recs) => {
      if (recs.some((r) => !panel.contains(r.target) && !vbtn.contains(r.target))) schedule();
    });
    mo.observe(viewer, { attributes: true, attributeFilter: ["hidden"], subtree: false });
    mo.observe(viewer, { attributes: true, attributeFilter: ["src", "poster", "hidden"], subtree: true });
    sync();
  }

  if (document.getElementById("viewer") || document.querySelector(".views")) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", installFamily);
    else installFamily();
  }
})();
