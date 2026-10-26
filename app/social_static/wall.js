/* Duvar: akış, paylaşım yükleme, beğeni/yorum. Ortak bileşenler social.js'te (window.SX). */
(() => {
  "use strict";
  const { el, api, icon } = SX;
  const $ = (s) => document.querySelector(s);
  const params = new URLSearchParams(location.search);
  const filterUser = params.get("u");
  const feed = $("#feed");
  let before = null, more = true, loading = false;

  /* ============================================================ üst bar + yan sütun */
  $("#nav").append(
    el("a", { class: "sx-ibtn", href: "/", title: "Aile ağacı", "aria-label": "Aile ağacı", html: icon("tree") }),
    el("a", { class: "sx-ibtn wl-active", href: "/duvar", title: "Duvar", "aria-label": "Duvar", html: icon("wall") }),
    el("button", { class: "sx-ibtn", title: "Paylaş", "aria-label": "Paylaş", html: icon("plus"), onclick: () => openUpload() }),
    SX.bell(), SX.userButton());
  $("#composer").addEventListener("click", () => openUpload());

  SX.ready.then(() => {
    const me = SX.me;
    if (!me) return;
    $("#composerAv").append(SX.avatar(me, 40));
    const side = $("#side");
    side.append(el("div", { class: "wl-me" }, SX.avatar(me, 52),
      el("div", {}, el("b", { text: me.username }), el("small", { text: me.name }))),
    el("h4", { text: "Aile" }),
    ...SX.users.filter((u) => u.id !== me.id).map((u) => el("a", { class: "wl-fam", href: "/duvar?u=" + encodeURIComponent(u.username) },
      SX.avatar(u, 36), el("div", {}, el("b", { text: u.name }), el("small", { text: u.role || "@" + u.username })))),
    el("p", { class: "wl-foot", text: "Kaderoğlu Ailesi · yalnızca aile üyeleri görebilir" }));
    if (filterUser) {
      const u = SX.users.find((x) => x.username === filterUser);
      const f = $("#filter");
      f.hidden = false;
      f.append(SX.avatar(u || { name: filterUser }, 72), el("div", {},
        el("h2", { text: u ? u.name : filterUser }), el("p", { text: "@" + filterUser + (u && u.role ? " · " + u.role : "") }),
        el("a", { class: "sx-link", href: "/duvar", text: "Tüm paylaşımlar" })));
      if (u && u.id !== me.id) $("#composer").hidden = true;
    }
  });

  /* ============================================================ gönderi kartı */
  const cards = new Map();

  function postCard(p) {
    const card = el("article", { class: "wl-post", "data-id": p.id });
    cards.set(p.id, card);
    fill(card, p);
    return card;
  }

  function fill(card, p) {
    card.textContent = "";
    const menu = (p.mine || (SX.me && SX.me.admin)) ? el("button", {
      class: "sx-ibtn", "aria-label": "Seçenekler", html: icon("more"), "data-sx-pop": "",
      onclick: async () => {
        if (!confirm("Bu paylaşım silinsin mi?")) return;
        try {
          await api(`/social/api/posts/${p.id}`, undefined, "DELETE");
          card.remove();
          SX.toast("Paylaşım silindi");
        } catch (ex) { SX.toast(ex.message, true); }
      },
    }) : null;
    card.append(el("header", { class: "wl-head" }, el("span", { class: "sx-ring" }, SX.avatar(p.user, 32)),
      el("div", {}, SX.userLink(p.user), el("small", { text: [p.user && p.user.role, SX.ago(p.created)].filter(Boolean).join(" · ") })), menu));

    if (p.status !== "ready") {
      card.append(el("div", { class: "wl-proc" }, p.status === "failed"
        ? [el("div", { html: icon("warn") }), el("b", { text: "Bu dosya işlenemedi" }), el("small", { text: p.error || "" })]
        : [el("div", { class: "sx-spin" }), el("b", { text: p.kind === "video" ? "Video hazırlanıyor…" : "Fotoğraf hazırlanıyor…" }),
          el("small", { text: "Yüzler tanınıyor; fotoğraftaki aile üyelerine haber verilecek" })]));
      if (p.status === "processing") setTimeout(() => refresh(p.id), 2500);
      return;
    }

    // medya
    const media = el("div", { class: "wl-media" });
    if (p.w && p.h) media.style.aspectRatio = `${p.w} / ${Math.min(p.h, p.w * 1.25)}`; // en fazla 4:5 (Instagram gibi)
    if (p.kind === "video") {
      media.append(el("video", { src: p.video, poster: p.full, controls: true, playsinline: true, preload: "metadata" }));
    } else {
      const img = el("img", { src: p.full, alt: p.caption || "", loading: "lazy", decoding: "async" });
      if (p.h > p.w * 1.25) img.style.objectFit = "cover";
      media.append(img);
      SX.faceTags(media, p.faces, p.people);
    }
    card.append(media);
    if (p.held) {
      const approve = SX.me && SX.me.admin ? el("button", {
        class: "sx-link", text: "Yayına al", style: "margin-left:8px", onclick: async () => {
          try { await api(`/social/api/posts/${p.id}/approve`, {}); refresh(p.id); SX.toast("Paylaşım yayına alındı"); }
          catch (ex) { SX.toast(ex.message, true); }
        },
      }) : null;
      card.append(el("div", { class: "wl-held" }, "Çıplaklık filtresi bu paylaşımı beklemeye aldı: şimdilik sadece siz ve yönetici görüyorsunuz.", approve));
    }

    // eylemler
    const heart = el("button", { class: "sx-ibtn sx-heart" + (p.liked ? " sx-on" : ""), "aria-label": "Beğen", html: icon("heart") });
    const likes = el("div", { class: "sx-likes" });
    const renderLikes = () => {
      heart.classList.toggle("sx-on", p.liked);
      likes.textContent = p.likes ? `${p.likes} beğenme` : "";
      likes.hidden = !p.likes;
    };
    const doLike = (force) => {
      if (force && p.liked) return;
      SX.like(p.item).then((d) => { p.liked = d.liked; p.likes = d.likes; renderLikes(); }).catch(() => {});
    };
    heart.addEventListener("click", () => doLike(false));
    let lastTap = 0;
    media.addEventListener("dblclick", (e) => { e.preventDefault(); burst(media); doLike(true); });
    media.addEventListener("touchend", () => {
      const now = Date.now();
      if (now - lastTap < 300) { burst(media); doLike(true); }
      lastTap = now;
    });
    const open = (focus) => SX.openItem(p.item, { focus, onChange: (s) => { p.likes = s.likes; p.liked = s.liked; p.comment_count = s.comment_count; renderLikes(); refresh(p.id); } });
    const body = el("div", { class: "wl-body-in" },
      el("div", { class: "sx-acts" }, heart, el("button", { class: "sx-ibtn", "aria-label": "Yorum yap", html: icon("comment"), onclick: () => open(true) })),
      likes);
    renderLikes();
    if (p.caption) {
      const cap = el("p", { class: "wl-cap" }, SX.userLink(p.user), " ", SX.richText(p.caption));
      body.append(cap);
      requestAnimationFrame(() => {
        cap.classList.add("wl-clamp");
        if (cap.scrollHeight > cap.clientHeight + 2) {
          const b = el("button", { class: "wl-morecap", text: "… devamı", onclick: () => { cap.classList.remove("wl-clamp"); b.remove(); } });
          cap.after(b);
        } else cap.classList.remove("wl-clamp");
      });
    }
    if (p.people.length) {
      body.append(el("div", { class: "wl-people" }, el("span", { html: icon("face", "sx-icon") , style: "display:inline-flex;width:16px;height:16px" }),
        "Fotoğrafta: ", ...p.people.map((x) => el("a", { class: "sx-chip", href: x.user ? "/duvar?u=" + encodeURIComponent(x.user.username) : "#" }, SX.avatar(x.user || { name: x.name }, 22), x.name))));
    }
    if (p.comment_count > p.comments.length) {
      body.append(el("button", { class: "wl-allc", text: `${p.comment_count} yorumun tümünü gör`, onclick: () => open(false) }));
    }
    for (const c of p.comments) body.append(el("p", { class: "wl-pc" }, SX.userLink(c.user), " ", SX.richText(c.body)));
    body.append(el("time", { class: "wl-when", datetime: new Date(p.created * 1000).toISOString(), text: SX.ago(p.created, true) }));
    card.append(body);

    // hızlı yorum
    const ta = el("textarea", { rows: 1, placeholder: "Yorum ekle…", maxlength: 1000, "aria-label": "Yorum ekle" });
    const send = el("button", { class: "sx-link", text: "Paylaş", disabled: true });
    const form = el("form", { class: "sx-form" }, ta, send);
    SX.attachMentions(ta, form);
    ta.addEventListener("input", () => { send.disabled = !ta.value.trim(); ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 96) + "px"; });
    ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const text = ta.value.trim();
      if (!text) return;
      send.disabled = true;
      try {
        await api(`/social/api/items/${p.item}/comments`, { body: text });
        ta.value = "";
        refresh(p.id);
      } catch (ex) { SX.toast(ex.message, true); send.disabled = false; }
    });
    card.append(form);
  }

  function burst(media) {
    const b = el("div", { class: "wl-burst", html: icon("heart") });
    media.append(b);
    setTimeout(() => b.remove(), 900);
  }

  async function refresh(id) {
    try {
      const p = await api(`/social/api/posts/${id}`);
      p.comments = p.comments.slice(-2);
      const card = cards.get(id);
      if (card && card.isConnected) fill(card, p);
      if (p.status !== "processing" && SX.pollBell) SX.pollBell();
    } catch { /* silinmiş olabilir */ }
  }

  /* ============================================================ akış */
  async function loadMore() {
    if (loading || !more) return;
    loading = true;
    try {
      await SX.ready;
      const q = new URLSearchParams({ limit: 6 });
      if (before) q.set("before", before);
      if (filterUser) q.set("user", filterUser);
      const d = await api("/social/api/posts?" + q);
      d.items.forEach((p) => feed.append(postCard(p)));
      more = d.more;
      if (d.items.length) before = d.items[d.items.length - 1].id;
      if (!more) {
        $("#sentinel").replaceWith(el("div", { class: "wl-end", html: icon("check") },
          el("p", { text: feed.children.length ? "Hepsi bu kadar" : filterUser ? "Henüz paylaşım yok" : "Duvar boş. İlk anıyı sen paylaş!" })));
      }
    } catch (ex) {
      SX.toast(ex.message, true);
    } finally {
      loading = false;
    }
  }
  new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) loadMore(); }, { rootMargin: "800px" }).observe($("#sentinel"));

  // bildirimden gelince ilgili gönderiyi aç
  SX.onNotification = (n) => {
    if (n.post_id) {
      SX.openItem(n.item, { onChange: () => refresh(n.post_id) });
      return true;
    }
    return false;
  };
  if (params.get("p")) {
    api(`/social/api/posts/${encodeURIComponent(params.get("p"))}`).then((p) => SX.openItem(p.item)).catch((ex) => SX.toast(ex.message, true));
  }

  /* ============================================================ yükleme */
  const MAX = { photo: 40, video: 600 };
  function openUpload(file) {
    let chosen = null, previewUrl = null;
    const input = el("input", { type: "file", accept: "image/*,video/*", hidden: true });
    const progress = el("div", { class: "wl-progress", hidden: true }, el("i"));
    const shareBtn = el("button", { class: "sx-link", text: "Paylaş", hidden: true });
    const back = el("button", { class: "sx-ibtn wl-back", "aria-label": "Geri", hidden: true, html: '<svg class="sx-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M15 6l-6 6 6 6"/></svg>' });
    const title = el("span", { text: "Yeni paylaşım" });
    const body = el("div", { class: "wl-up-body" });
    const box = el("div", { class: "wl-up" }, el("div", { class: "wl-up-head" }, back, title, shareBtn), progress, body, input);
    const m = SX.modal(box, { onClose: () => { if (previewUrl) URL.revokeObjectURL(previewUrl); } });

    const pickStep = () => {
      chosen = null;
      shareBtn.hidden = back.hidden = true;
      title.textContent = "Yeni paylaşım";
      const drop = el("div", { class: "wl-drop" },
        el("div", { html: '<svg viewBox="0 0 96 96" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="10" y="22" width="56" height="48" rx="8" transform="rotate(-8 38 46)"/><rect x="30" y="26" width="56" height="48" rx="8" fill="#fff"/><circle cx="48" cy="44" r="6"/><path d="M30 66l16-14 12 10 10-8 18 14"/></svg>' }),
        el("p", { text: "Fotoğraf veya videoyu buraya sürükleyin" }),
        el("button", { class: "sx-btn", type: "button", text: "Bilgisayardan seç", onclick: () => input.click() }),
        el("small", { text: `Fotoğraf en fazla ${MAX.photo} MB, video en fazla ${MAX.video} MB` }));
      for (const ev of ["dragenter", "dragover"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("wl-over"); });
      drop.addEventListener("dragleave", () => drop.classList.remove("wl-over"));
      drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("wl-over"); if (e.dataTransfer.files[0]) choose(e.dataTransfer.files[0]); });
      body.replaceChildren(drop);
    };

    const choose = (f) => {
      const kind = f.type.startsWith("video/") ? "video" : f.type.startsWith("image/") || /\.(heic|heif)$/i.test(f.name) ? "photo" : null;
      if (!kind) return SX.toast("Sadece fotoğraf veya video paylaşılabilir", true);
      if (f.size > MAX[kind] * 1024 * 1024) return SX.toast(`Dosya çok büyük (en fazla ${MAX[kind]} MB)`, true);
      chosen = f;
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(f);
      const prev = el("div", { class: "wl-prev" }, kind === "video"
        ? el("video", { src: previewUrl, controls: true, playsinline: true, muted: true })
        : el("img", { src: previewUrl, alt: "", onerror: (e) => e.target.replaceWith(el("p", { style: "color:#fff", text: f.name })) }));
      const ta = el("textarea", { placeholder: "Bir açıklama yazın… (@ ile birini anabilirsiniz)", maxlength: 2200, "aria-label": "Açıklama" });
      const count = el("div", { class: "wl-count", text: "0/2.200" });
      ta.addEventListener("input", () => (count.textContent = `${ta.value.length.toLocaleString("tr-TR")}/2.200`));
      const capbox = el("div", { class: "wl-capbox" }, ta);
      SX.attachMentions(ta, capbox);
      body.replaceChildren(prev, el("div", { class: "wl-meta" },
        el("div", { class: "wl-who" }, SX.avatar(SX.me, 28), SX.me ? SX.me.username : ""),
        capbox, count,
        el("div", { class: "wl-note", html: icon("face") }, el("span", { text: "Fotoğraftaki aile üyeleri yüzlerinden tanınır, fotoğraf aile arşivine eklenir ve onlara bildirim gider. Konum ve çekim bilgileri silinir." }))));
      title.textContent = "Paylaşım oluştur";
      shareBtn.hidden = back.hidden = false;
      shareBtn.onclick = () => upload(ta.value);
      setTimeout(() => ta.focus(), 30);
    };

    const upload = (caption) => {
      if (!chosen) return;
      shareBtn.disabled = back.disabled = true;
      progress.hidden = false;
      title.textContent = "Yükleniyor…";
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/social/api/posts");
      xhr.setRequestHeader("X-Requested-With", "uzakpanel");
      xhr.setRequestHeader("X-Caption", encodeURIComponent(caption.trim()));
      xhr.setRequestHeader("Content-Type", chosen.type || "application/octet-stream");
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) progress.firstChild.style.width = (e.loaded / e.total) * 100 + "%"; };
      xhr.onload = () => {
        let d = {};
        try { d = JSON.parse(xhr.responseText); } catch { /* boş */ }
        if (xhr.status >= 200 && xhr.status < 300) {
          m.close();
          SX.toast("Paylaşıldı! Fotoğraf hazırlanıyor…");
          if (!filterUser || (SX.me && filterUser === SX.me.username)) {
            feed.prepend(postCard(d));
            document.querySelector(".wl-end p") && feed.children.length === 1 && (document.querySelector(".wl-end p").textContent = "Hepsi bu kadar");
          }
          window.scrollTo({ top: 0, behavior: "smooth" });
        } else {
          SX.toast(d.detail || `Yükleme başarısız (${xhr.status})`, true);
          shareBtn.disabled = back.disabled = false;
          title.textContent = "Paylaşım oluştur";
          progress.hidden = true;
        }
      };
      xhr.onerror = () => {
        SX.toast("Bağlantı hatası, tekrar deneyin", true);
        shareBtn.disabled = back.disabled = false;
        progress.hidden = true;
      };
      xhr.send(chosen);
    };

    back.addEventListener("click", pickStep);
    input.addEventListener("change", () => { if (input.files[0]) choose(input.files[0]); });
    if (file) choose(file); else pickStep();
  }
  SX.openUpload = openUpload;
})();
