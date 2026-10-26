"use strict";
(() => {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const PAGE = 10;
  const COLORS = ["#4f7a4a", "#b8742f", "#7a5c9e", "#3f7d8c", "#a5534f", "#6b7d3a", "#8c6a3f", "#4d6aa3"];
  const RING = '<svg class="ring" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 21s-7.5-4.6-9.6-9.2C.9 8.4 3 4.5 6.7 4.5c2.1 0 3.6 1.2 4.3 2.4.7-1.2 2.2-2.4 4.3-2.4 3.7 0 5.8 3.9 4.3 7.3C19.5 16.4 12 21 12 21z"/></svg>';
  let fam = null;

  const initials = (name) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toLocaleUpperCase("tr")).join("");
  function colorFor(id) {
    let h = 0;
    for (const c of id) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    return COLORS[h % COLORS.length];
  }
  function avatarHtml(id, p, withCount) {
    const inner = p.avatar
      ? `<img src="${esc(p.avatar)}" alt="" loading="lazy">`
      : `<span class="initials" style="background:${colorFor(id)}">${esc(initials(p.name))}</span>`;
    const count = withCount && p.count ? `<span class="count">${p.count > 999 ? (p.count / 1000).toFixed(1) + "B" : p.count}</span>` : "";
    return `<span class="avatar">${inner}${count}</span>`;
  }
  const fmtDate = (t) => (t ? new Date(t * 1000).toLocaleDateString("tr-TR", { day: "numeric", month: "long", year: "numeric" }) : "");
  const namesOf = (ids) => ids.map((id) => fam.people[id]?.name).filter(Boolean);

  /* ============================================================ ağaç */
  function personHtml(id) {
    const p = fam.people[id];
    if (!p) return "";
    return `<button class="person" data-id="${esc(id)}" title="${esc(p.name)} · ${p.count} fotoğraf">
      ${avatarHtml(id, p, true)}
      <span class="name">${esc(p.name)}</span>
      <span class="role">${esc(p.role || "")}</span>
    </button>`;
  }
  function nodeHtml(node) {
    const people = node.couple.map(personHtml).join(node.couple.length > 1 ? RING : "");
    const kids = node.children?.length ? `<ul>${node.children.map(nodeHtml).join("")}</ul>` : "";
    return `<li><div class="couple">${people}</div>${kids}</li>`;
  }

  // ana ağaç + eşlerin aileleri (ör. Beştav, Şentapancı) ayrı küçük ağaçlar olarak
  function renderTrees() {
    $("#tree").innerHTML = `<ul>${nodeHtml(fam.tree)}</ul>`;
    $("#extraTrees").innerHTML = (fam.extra_trees || []).map((t) =>
      `<section class="extra-tree"><h3>${esc(t.title)}</h3>
        <div class="tree-scroller"><div class="tree"><ul>${nodeHtml(t.tree)}</ul></div></div></section>`).join("");
  }
  $("#extraTrees").addEventListener("click", (e) => {
    const b = e.target.closest(".person");
    if (b) openPerson(b.dataset.id);
  });

  async function load() {
    const r = await fetch("/api/family");
    fam = await r.json();
    $("#title").textContent = fam.title || "Ailemiz";
    $("#subtitle").textContent = fam.subtitle || "";
    document.title = fam.title || "Ailemiz";
    renderTrees();
    const parts = [];
    if (fam.total_photos) parts.push(`${fam.total_photos.toLocaleString("tr-TR")} fotoğraf`);
    if (fam.geo_photos) parts.push(`${fam.geo_photos.toLocaleString("tr-TR")} tanesi haritada`);
    $("#foot").textContent = parts.join(" · ");
    const sc = $("#scroller");
    sc.scrollLeft = (sc.scrollWidth - sc.clientWidth) / 2;
  }

  $("#tree").addEventListener("click", (e) => {
    if (dragMoved) return;
    const b = e.target.closest(".person");
    if (b) openPerson(b.dataset.id);
  });

  // geniş ağacı fareyle sürükleyerek kaydırma
  let dragStart = null, dragMoved = false;
  const sc = $("#scroller");
  sc.addEventListener("pointerdown", (e) => {
    if (e.pointerType !== "mouse") return;
    dragStart = { x: e.clientX, left: sc.scrollLeft };
    dragMoved = false;
  });
  window.addEventListener("pointermove", (e) => {
    if (!dragStart) return;
    const dx = e.clientX - dragStart.x;
    if (Math.abs(dx) > 5) { dragMoved = true; sc.classList.add("dragging"); }
    if (dragMoved) sc.scrollLeft = dragStart.left - dx;
  });
  window.addEventListener("pointerup", () => {
    dragStart = null;
    sc.classList.remove("dragging");
    setTimeout(() => (dragMoved = false), 0);
  });

  /* ============================================================ görünümler */
  let view = "tree";
  function showView(name, arg = null) {
    view = name;
    document.querySelectorAll(".view-btn").forEach((b) => b.classList.toggle("on", b.dataset.view === name));
    for (const v of ["tree", "map", "together"]) $("#view-" + v).hidden = name !== v;
    $("#footer").hidden = name === "together";
    if (name === "map") openMap(arg);
    else if (name === "together") openTogether(arg);
    else history.replaceState(null, "", location.pathname);
  }
  document.querySelectorAll(".view-btn").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));

  /* ============================================================ kişi paneli (zaman çizelgesi) */
  const personTl = new Timeline($("#mTimeline"), { pageSize: PAGE, onOpen: (list, i) => openViewer(list, i) });
  let modalPerson = null;

  function openPerson(id) {
    const p = fam.people[id];
    modalPerson = id;
    $("#mAvatar").innerHTML = avatarHtml(id, p, false);
    $("#mName").textContent = p.name;
    $("#mMeta").textContent = [p.role, p.count ? `${p.count.toLocaleString("tr-TR")} fotoğraf` : "Henüz fotoğraf yok"].filter(Boolean).join(" · ");
    $("#mMap").hidden = !p.geo_count;
    $("#modal").hidden = false;
    document.body.style.overflow = "hidden";
    history.replaceState(null, "", "#" + id);
    personTl.load([id], { emptyText: "Bu kişi için henüz fotoğraf yok." });
  }

  function closeModal() {
    $("#modal").hidden = true;
    document.body.style.overflow = "";
    personTl.reset();
    if (view === "tree") history.replaceState(null, "", location.pathname);
  }

  $("#mMap").addEventListener("click", () => {
    const id = modalPerson;
    closeModal();
    showView("map", id);
  });
  document.querySelectorAll("[data-close]").forEach((el) => el.addEventListener("click", closeModal));

  /* ============================================================ birlikte */
  const togetherTl = new Timeline($("#tgTimeline"), { pageSize: PAGE, onOpen: (list, i) => openViewer(list, i) });
  let picked = [];
  let tgReady = false;

  // tüm ağaçlardaki kişiler, ağaç sırasıyla ve tekrarsız (Tevhide hem Kaderoğlu hem Beştav ağacında)
  function treeOrder() {
    const order = [];
    const walk = (n) => { n.couple.forEach((id) => order.includes(id) || order.push(id)); (n.children || []).forEach(walk); };
    walk(fam.tree);
    (fam.extra_trees || []).forEach((t) => walk(t.tree));
    return order.filter((id) => fam.people[id]);
  }

  function renderTgList() {
    $("#tgList").innerHTML = treeOrder().map((id) => {
      const p = fam.people[id];
      const on = picked.includes(id);
      return `<button class="tg-person ${on ? "picked" : ""}" data-id="${esc(id)}" draggable="true" title="Çift tıklayın veya sürükleyin">
        ${avatarHtml(id, p, false)}
        <span class="who"><b>${esc(p.name)}</b><span>${p.count.toLocaleString("tr-TR")} fotoğraf</span></span>
        <span class="add" data-add aria-hidden="true">${on ? "✓" : "+"}</span>
      </button>`;
    }).join("");
  }

  function renderPicked() {
    const drop = $("#tgDrop");
    $("#tgClear").hidden = !picked.length;
    if (!picked.length) {
      drop.innerHTML = `<div class="tg-empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="12" cy="8" r="3.5"/><path d="M5 20c0-3.9 3.1-7 7-7s7 3.1 7 7"/><path d="M19 3v4M17 5h4"/></svg>
        Soldaki listeden bir kişiye <b>çift tıklayın</b> ya da buraya sürükleyin.<br>Birden fazla kişi seçince hepsinin birlikte olduğu fotoğraflar gelir.</div>`;
      return;
    }
    drop.innerHTML = picked.map((id, i) => `${i ? '<div class="tg-plus">+</div>' : ""}
      <div class="tg-chip">${avatarHtml(id, fam.people[id], false)}<b>${esc(fam.people[id].name)}</b>
      <button class="x" data-remove="${esc(id)}" aria-label="Çıkar">×</button></div>`).join("");
  }

  function updateTogether() {
    renderTgList();
    renderPicked();
    history.replaceState(null, "", "#birlikte" + (picked.length ? "/" + picked.join(",") : ""));
    if (!picked.length) {
      $("#tgTitle").innerHTML = "Birlikte fotoğraflar";
      togetherTl.reset();
      togetherTl.status.textContent = "Kişi seçildiğinde fotoğraflar burada, en yeniden eskiye görünecek.";
      return;
    }
    const names = picked.map((id) => fam.people[id].name.split(" ")[0]);
    const title = picked.length === 1 ? `${esc(fam.people[picked[0]].name)}` : `${esc(names.slice(0, -1).join(", "))} ve ${esc(names.at(-1))} birlikte`;
    $("#tgTitle").innerHTML = `<span>${title}</span><small id="tgCount"></small>`;
    togetherTl.load(picked, { emptyText: picked.length > 1 ? "Bu kişilerin birlikte olduğu bir fotoğraf bulunamadı." : "Bu kişi için henüz fotoğraf yok." })
      .then(() => { const c = $("#tgCount"); if (c) c.textContent = togetherTl.total ? `${togetherTl.total.toLocaleString("tr-TR")} fotoğraf` : ""; });
  }

  function addPerson(id) {
    if (!fam.people[id] || picked.includes(id)) return;
    picked.push(id);
    updateTogether();
  }
  function removePerson(id) {
    picked = picked.filter((x) => x !== id);
    updateTogether();
  }

  function openTogether(arg) {
    if (!tgReady) {
      tgReady = true;
      const list = $("#tgList"), drop = $("#tgDrop");
      list.addEventListener("dblclick", (e) => { const b = e.target.closest(".tg-person"); if (b) addPerson(b.dataset.id); });
      // dokunmatik ekranlar ve erişilebilirlik için "+" düğmesi tek tıkla ekler
      list.addEventListener("click", (e) => {
        if (!e.target.closest("[data-add]")) return;
        const id = e.target.closest(".tg-person").dataset.id;
        picked.includes(id) ? removePerson(id) : addPerson(id);
      });
      list.addEventListener("keydown", (e) => { if (e.key === "Enter") { const b = e.target.closest(".tg-person"); if (b) addPerson(b.dataset.id); } });
      list.addEventListener("dragstart", (e) => {
        const b = e.target.closest(".tg-person");
        if (b) { e.dataTransfer.setData("text/plain", b.dataset.id); e.dataTransfer.effectAllowed = "copy"; }
      });
      drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
      drop.addEventListener("dragleave", () => drop.classList.remove("over"));
      drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); addPerson(e.dataTransfer.getData("text/plain")); });
      drop.addEventListener("click", (e) => { const x = e.target.closest("[data-remove]"); if (x) removePerson(x.dataset.remove); });
      $("#tgClear").addEventListener("click", () => { picked = []; updateTogether(); });
    }
    if (arg !== null && arg !== undefined) picked = arg.split(",").filter((id) => fam.people[id]);
    updateTogether();
  }

  /* ============================================================ harita */
  const LEAFLET = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/";
  const CLUSTER = "https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/";
  let mapReady = null, map = null, layer = null, mapPerson = null, mapToken = 0;

  const loadCss = (href) => {
    const l = document.createElement("link");
    l.rel = "stylesheet";
    l.href = href;
    document.head.appendChild(l);
  };
  const loadJs = (src) => new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = ok;
    s.onerror = () => fail(new Error(src));
    document.head.appendChild(s);
  });

  function ensureMap() {
    mapReady ||= (async () => {
      loadCss(LEAFLET + "leaflet.css");
      loadCss(CLUSTER + "MarkerCluster.css");
      await loadJs(LEAFLET + "leaflet.js");
      await loadJs(CLUSTER + "leaflet.markercluster.js");
      // sayfa kaydırılırken harita yakınlaşmasın: tekerlek ancak haritaya tıklanınca çalışır
      map = L.map("map", { worldCopyJump: true, minZoom: 2, scrollWheelZoom: false }).setView([39, 35], 5);
      map.on("click focus", () => map.scrollWheelZoom.enable());
      map.getContainer().addEventListener("mouseleave", () => map.scrollWheelZoom.disable());
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> katkıcıları',
      }).addTo(map);
    })();
    return mapReady;
  }

  function renderFilter() {
    const chips = [`<button class="chip all ${mapPerson ? "" : "on"}" data-p="">Herkes <span class="n">${fam.geo_photos}</span></button>`];
    for (const id of treeOrder()) {
      const p = fam.people[id];
      if (!p) continue;
      chips.push(`<button class="chip ${mapPerson === id ? "on" : ""}" data-p="${esc(id)}" ${p.geo_count ? "" : "disabled"}>
        ${avatarHtml(id, p, false)}${esc(p.name)} <span class="n">${p.geo_count}</span></button>`);
    }
    $("#mapFilter").innerHTML = chips.join("");
  }
  $("#mapFilter").addEventListener("click", (e) => {
    const c = e.target.closest(".chip");
    if (c && !c.disabled) openMap(c.dataset.p || null);
  });

  function popupEl(list, i) {
    const it = list.items[i];
    const el = document.createElement("div");
    el.className = "pop";
    const names = namesOf(it.people);
    el.innerHTML = `<img src="${esc(it.thumb)}" alt=""><div class="t"><b>${esc(fmtDate(it.taken) || "Tarihsiz")}</b><span>${esc(names.join(", "))}</span></div>`;
    el.querySelector("img").addEventListener("click", () => openViewer(list, i));
    return el;
  }

  async function openMap(person) {
    mapPerson = person || null;
    history.replaceState(null, "", "#harita" + (mapPerson ? "/" + mapPerson : ""));
    renderFilter();
    const token = ++mapToken;
    $("#mapInfo").textContent = "Harita yükleniyor…";
    try {
      await ensureMap();
    } catch {
      $("#mapInfo").textContent = "Harita yüklenemedi.";
      return;
    }
    map.invalidateSize();
    const raw = await fetch("/api/map" + (mapPerson ? "?person=" + encodeURIComponent(mapPerson) : "")).then((r) => r.json());
    if (token !== mapToken) return;
    const data = {
      items: raw.items.map(([id, lat, lng, taken, people, isVideo]) => ({
        id, lat, lng, taken, people, with: people.filter((p) => p !== mapPerson),
        thumb: `/media/thumb/${id}.jpg`, full: `/media/full/${id}.jpg`,
        ...(isVideo ? { type: "video", video: `/media/video/${id}.mp4` } : { type: "photo" }),
      })),
    };

    if (layer) map.removeLayer(layer);
    layer = L.markerClusterGroup({
      showCoverageOnHover: false, maxClusterRadius: 56, spiderfyOnMaxZoom: true,
      iconCreateFunction: (c) => {
        const first = c.getAllChildMarkers()[0].options.item;
        return L.divIcon({ className: "", iconSize: [54, 54],
          html: `<div class="photo-cluster"><div class="photo-pin"><img src="${esc(first.thumb)}" alt=""></div><b>${c.getChildCount()}</b></div>` });
      },
    });
    const list = { items: data.items, total: () => data.items.length, more: null };
    data.items.forEach((it, i) => {
      const m = L.marker([it.lat, it.lng], {
        item: it, icon: L.divIcon({ className: "", iconSize: [46, 46], html: `<div class="photo-pin ${it.type === "video" ? "vid" : ""}"><img src="${esc(it.thumb)}" alt="" loading="lazy"></div>` }),
      });
      m.bindPopup(() => popupEl(list, i), { closeButton: false, offset: [0, -16] });
      layer.addLayer(m);
    });
    map.addLayer(layer);

    document.querySelector(".map-empty")?.remove();
    if (data.items.length) {
      map.fitBounds(layer.getBounds(), { padding: [50, 50], maxZoom: 13 });
      const who = mapPerson ? fam.people[mapPerson].name + " · " : "";
      $("#mapInfo").textContent = `${who}${data.items.length.toLocaleString("tr-TR")} konumlu fotoğraf`;
    } else {
      $("#mapInfo").textContent = "";
      const empty = document.createElement("div");
      empty.className = "map-empty";
      empty.textContent = fam.total_photos ? "Bu seçimde konum bilgisi olan fotoğraf yok." : "Fotoğraflar içe aktarıldığında burada haritada görünecek.";
      $(".map-wrap").appendChild(empty);
    }
  }

  /* ============================================================ tam ekran */
  let vl = null, vi = -1;
  function openViewer(list, i) {
    vl = list;
    show(i);
  }
  function show(i) {
    vi = i;
    const it = vl.items[i];
    const vid = $("#vVid"), img = $("#vImg");
    if (it.type === "video") {
      img.hidden = true;
      img.removeAttribute("src");
      vid.poster = it.full;
      vid.src = it.video;
      vid.hidden = false;
      vid.play().catch(() => { /* otomatik oynatma engellenirse kullanıcı başlatır */ });
    } else {
      vid.pause();
      vid.removeAttribute("src");
      vid.hidden = true;
      img.hidden = false;
      img.src = it.full;
    }
    const withNames = namesOf(it.with || []);
    $("#vCap").textContent = [fmtDate(it.taken), withNames.length ? "Birlikte: " + withNames.join(", ") : ""].filter(Boolean).join(" · ");
    $("#vPrev").disabled = i <= 0;
    $("#vNext").disabled = i >= vl.total() - 1;
    $("#viewer").hidden = false;
    endFaceMode();
    updateAdminTools();
    renderTags(it);
    // sona yaklaşınca sıradaki sayfayı önceden getir
    if (vl.more && i >= vl.items.length - 3) vl.more();
    const next = vl.items[i + 1];
    if (next) new Image().src = next.full;
  }
  async function step(d) {
    const j = vi + d;
    if (!vl || j < 0 || j >= vl.total()) return;
    if (j >= vl.items.length && vl.more) await vl.more();
    if (vl.items[j]) show(j);
  }
  const closeViewer = () => {
    endFaceMode();
    tagToken++;
    $("#vTags").innerHTML = "";
    $("#viewer").hidden = true;
    $("#vImg").removeAttribute("src");
    const vid = $("#vVid");
    vid.pause();
    vid.removeAttribute("src");
    vid.load();
  };

  /* ---------- yüzlerin üstünde isimler (hedef çerçevesi) */
  let namesOn = (() => { try { return localStorage.getItem("kad-names") !== "0"; } catch { return true; } })();
  let tagToken = 0;
  const faceCache = new Map();

  function applyNames() {
    const b = $("#vNames");
    b.classList.toggle("on", namesOn);
    b.setAttribute("aria-pressed", String(namesOn));
    $("#vTags").classList.toggle("off", !namesOn);
  }
  $("#vNames").addEventListener("click", (e) => {
    e.stopPropagation();
    namesOn = !namesOn;
    try { localStorage.setItem("kad-names", namesOn ? "1" : "0"); } catch { /* özel pencere */ }
    applyNames();
  });
  applyNames();

  function facesFor(it) {
    if (Array.isArray(it.faces)) return Promise.resolve(it.faces);
    if (!faceCache.has(it.id)) {
      faceCache.set(it.id, fetch(`/api/faces?photo=${encodeURIComponent(it.id)}`).then((r) => r.json()).then((d) => d.faces || []).catch(() => []));
    }
    return faceCache.get(it.id);
  }

  function positionTags() {
    const r = $("#vImg").getBoundingClientRect();
    Object.assign($("#vTags").style, { left: r.left + "px", top: r.top + "px", width: r.width + "px", height: r.height + "px" });
  }

  async function renderTags(it) {
    const box = $("#vTags"), img = $("#vImg");
    const token = ++tagToken;
    box.innerHTML = "";
    if (it.type === "video") return;  // video karesi sürekli değişir: isim çerçevesi sadece fotoğraflarda
    const loaded = img.complete && img.naturalWidth ? null : new Promise((ok) => img.addEventListener("load", ok, { once: true }));
    const [faces] = await Promise.all([facesFor(it), loaded]);
    if (token !== tagToken) return;
    positionTags();
    box.innerHTML = faces.filter((f) => fam.people[f[4]]).map(([x, y, w, h, pid], n) =>
      `<div class="v-tag ${y < 0.09 ? "below" : ""}" data-p="${esc(pid)}" title="${esc(fam.people[pid].name)} · fotoğraflarını aç"
        style="left:${x * 100}%;top:${y * 100}%;width:${w * 100}%;height:${h * 100}%;--d:${n * 90}ms">
        <i></i><i></i><i></i><i></i><b>${esc(fam.people[pid].name)}</b></div>`).join("");
  }

  $("#vTags").addEventListener("click", (e) => {
    const t = e.target.closest(".v-tag");
    if (!t) return;
    e.stopPropagation();
    closeViewer();
    if (view !== "tree") showView("tree");
    openPerson(t.dataset.p);
  });
  window.addEventListener("resize", () => { if (!$("#viewer").hidden) positionTags(); });
  $("#vPrev").addEventListener("click", () => step(-1));
  $("#vNext").addEventListener("click", () => step(1));
  document.querySelector("[data-vclose]").addEventListener("click", closeViewer);
  $("#viewer").addEventListener("click", (e) => { if (e.target === e.currentTarget || e.target.classList.contains("v-fig")) closeViewer(); });

  let touchX = null;
  $("#viewer").addEventListener("touchstart", (e) => (touchX = e.touches[0].clientX), { passive: true });
  $("#viewer").addEventListener("touchend", (e) => {
    if (touchX == null) return;
    const dx = e.changedTouches[0].clientX - touchX;
    if (Math.abs(dx) > 50) step(dx < 0 ? 1 : -1);
    touchX = null;
  });

  document.addEventListener("keydown", (e) => {
    if (!$("#loginModal").hidden) {
      if (e.key === "Escape") closeLogin();
      return;
    }
    if (!$("#viewer").hidden) {
      if (e.key === "Escape") { if (faceMode) endFaceMode(); else closeViewer(); return; }
      if (faceMode) return;
      if (e.key === "ArrowLeft") step(-1);
      if (e.key === "ArrowRight") step(1);
    } else if (!$("#modal").hidden && e.key === "Escape") closeModal();
  });

  /* ============================================================ yönetici: profil fotoğrafı */
  let admin = false, panelAdmin = false, faceMode = null;
  const api = (url, body) => fetch(url, {
    method: body ? "POST" : "GET", credentials: "same-origin",
    headers: { "X-Requested-With": "uzakpanel", ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  }).then(async (r) => {
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(d.detail || `Hata ${r.status}`), { status: r.status });
    return d;
  });

  function toast(msg, err = false) {
    const t = $("#toast");
    t.textContent = msg;
    t.className = "toast show" + (err ? " err" : "");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => (t.className = "toast"), 3500);
  }

  function renderAdmin() {
    const b = $("#adminBtn");
    b.textContent = admin ? "Yönetici · Çıkış" : "Yönetici girişi";
    b.classList.toggle("on", admin);
    b.hidden = admin && !panelAdmin;  // aile hesabıyla yönetici: çıkış sağ üstteki kullanıcı menüsünde
    $("#hiddenBtn").hidden = !admin;
    updateAdminTools();
  }
  async function checkAdmin() {
    try { const me = await api("/api/me"); admin = me.admin; panelAdmin = me.panel; } catch { admin = false; }
    renderAdmin();
  }

  function openLogin() {
    $("#loginModal").hidden = false;
    $("#lError").textContent = "";
    $("#loginForm").username.focus();
  }
  function closeLogin() { $("#loginModal").hidden = true; $("#loginForm").password.value = ""; }
  document.querySelectorAll("[data-lclose]").forEach((el) => el.addEventListener("click", closeLogin));
  $("#adminBtn").addEventListener("click", async () => {
    if (!admin) return openLogin();
    try { await api("/api/logout", {}); } catch { /* yine de çık */ }
    admin = false;
    renderAdmin();
    toast("Yönetici oturumu kapatıldı");
  });
  $("#loginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.currentTarget;
    $("#lSubmit").disabled = true;
    $("#lError").textContent = "";
    try {
      await api("/api/login", { username: f.username.value, password: f.password.value });
      admin = true;
      closeLogin();
      renderAdmin();
      toast("Giriş yapıldı · Fotoğraf açıp \"Profil fotoğrafı yap\"a basabilirsiniz");
    } catch (err) {
      $("#lError").textContent = err.message;
      f.password.value = "";
      f.password.focus();
    } finally {
      $("#lSubmit").disabled = false;
    }
  });

  const currentItem = () => (vl && vi >= 0 ? vl.items[vi] : null);
  function updateAdminTools() {
    const it = currentItem();
    $("#vAdmin").hidden = !admin || $("#viewer").hidden || !it;
    $("#vPick").hidden = true;
    if (it) {
      $("#vHide span").textContent = it.hiddenNow ? "Geri aç" : "Gizle";
      $("#vAvatar").hidden = it.type === "video" || !!it.hiddenNow;
    }
  }

  // gizle / geri aç (data/moderation.json; çıplaklık kontrolünün kararından önce gelir)
  $("#vHide").addEventListener("click", async (e) => {
    e.stopPropagation();
    const it = currentItem();
    const hide = !it.hiddenNow;
    try {
      await api("/api/admin/hide", { photo: it.id, hide });
      it.hiddenNow = hide;
      updateAdminTools();
      if (hiddenTiles.has(it.id)) hiddenTiles.get(it.id).classList.toggle("restored", !hide);
      toast(hide ? "Gizlendi · artık sitede görünmeyecek" : "Geri açıldı · sitede görünecek");
    } catch (err) {
      if (err.status === 401) { admin = false; renderAdmin(); }
      toast(err.message, true);
    }
  });

  // gizlenenler listesi (bulanık küçük resimler)
  const hidden = { items: [], total: 0, loading: false };
  const hiddenList = { get items() { return hidden.items; }, total: () => hidden.total, more: () => loadHidden() };
  const hiddenTiles = new Map();
  async function loadHidden() {
    if (hidden.loading || (hidden.items.length && hidden.items.length >= hidden.total)) return;
    hidden.loading = true;
    try {
      const d = await api(`/api/admin/hidden?offset=${hidden.items.length}&limit=60`);
      hidden.total = d.total;
      for (const it of d.items) {
        it.hiddenNow = true;
        const i = hidden.items.push(it) - 1;
        const b = document.createElement("button");
        b.className = "h-tile";
        b.dataset.i = i;
        b.innerHTML = `<img src="${esc(it.thumb)}" alt="" loading="lazy">` +
          (it.type === "video" ? `<span class="play"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 4v16l13-8z"/></svg>${esc(window.fmtDur(it.dur))}</span>` : "") +
          `<span class="why ${it.why}">${it.why === "manual" ? "Elle gizlendi" : "Otomatik"}</span>`;
        hiddenTiles.set(it.id, b);
        $("#hGrid").appendChild(b);
      }
      $("#hMeta").textContent = `${hidden.total.toLocaleString("tr-TR")} öğe gizli. Görmek için tıklayın; uygunsa görüntüleyicide "Geri aç".`;
      $("#hMore").hidden = hidden.items.length >= hidden.total;
    } catch (err) {
      toast(err.message, true);
    } finally {
      hidden.loading = false;
    }
  }
  $("#hiddenBtn").addEventListener("click", () => {
    hidden.items = []; hidden.total = 0;
    hiddenTiles.clear();
    $("#hGrid").innerHTML = "";
    $("#hiddenModal").hidden = false;
    document.body.style.overflow = "hidden";
    loadHidden();
  });
  $("#hMore").addEventListener("click", loadHidden);
  $("#hGrid").addEventListener("click", (e) => {
    const t = e.target.closest(".h-tile");
    if (t) openViewer(hiddenList, +t.dataset.i);
  });
  document.querySelectorAll("[data-hclose]").forEach((el) => el.addEventListener("click", () => {
    $("#hiddenModal").hidden = true;
    document.body.style.overflow = "";
  }));

  $("#vAvatar").addEventListener("click", (e) => {
    e.stopPropagation();
    const it = currentItem();
    const people = (it.people || []).filter((id) => fam.people[id]);
    if (!people.length) return toast("Bu fotoğrafta ağaçtaki birinin etiketi yok", true);
    if (people.length === 1) return startFaceMode(people[0]);
    const pick = $("#vPick");
    if (!pick.hidden) { pick.hidden = true; return; }
    pick.innerHTML = "<small>Kimin profil fotoğrafı?</small>" +
      people.map((id) => `<button data-p="${esc(id)}">${esc(fam.people[id].name)}</button>`).join("");
    pick.hidden = false;
  });
  $("#vPick").addEventListener("click", (e) => {
    e.stopPropagation();
    const b = e.target.closest("[data-p]");
    if (b) { $("#vPick").hidden = true; startFaceMode(b.dataset.p); }
  });

  async function startFaceMode(pid) {
    $("#vTags").hidden = true;
    const it = currentItem();
    const img = $("#vImg");
    if (!img.complete) await new Promise((ok) => img.addEventListener("load", ok, { once: true }));
    let info;
    try { info = await api(`/api/admin/faces?photo=${encodeURIComponent(it.id)}`); }
    catch (err) {
      if (err.status === 401) { admin = false; renderAdmin(); return toast("Oturum sona erdi, yeniden giriş yapın", true); }
      return toast(err.message, true);
    }
    faceMode = { pid, photo: it.id, w: info.w, h: info.h };
    const r = img.getBoundingClientRect();
    const box = $("#vFaces");
    Object.assign(box.style, { left: r.left + "px", top: r.top + "px", width: r.width + "px", height: r.height + "px" });
    box.innerHTML = info.faces.map(([x, y, w, h], i) =>
      `<div class="v-face" data-i="${i}" style="left:${x / info.w * 100}%;top:${y / info.h * 100}%;width:${w / info.w * 100}%;height:${h / info.h * 100}%"></div>`).join("");
    faceMode.faces = info.faces;
    box.hidden = false;
    const name = fam.people[pid].name.split(" ")[0];
    $("#vHint").innerHTML = `<span>${info.faces.length ? `<b>${esc(name)}</b> için yüzü seçin` : `Yüz bulunamadı · <b>${esc(name)}</b>: yüzüne tıklayın`}</span><button id="vCancel">Vazgeç</button>`;
    $("#vHint").hidden = false;
    $("#vCancel").addEventListener("click", (e) => { e.stopPropagation(); endFaceMode(); });
    $("#vAdmin").hidden = true;
  }

  function endFaceMode() {
    faceMode = null;
    $("#vTags").hidden = false;
    $("#vFaces").hidden = true;
    $("#vFaces").innerHTML = "";
    $("#vFaces").classList.remove("busy");
    $("#vHint").hidden = true;
    if (admin && !$("#viewer").hidden) $("#vAdmin").hidden = false;
  }

  $("#vFaces").addEventListener("click", async (e) => {
    e.stopPropagation();
    if (!faceMode || $("#vFaces").classList.contains("busy")) return;
    const fm = faceMode;
    let face;
    const f = e.target.closest(".v-face");
    if (f) face = fm.faces[+f.dataset.i];
    else {
      // algılanmayan yüz: tıklanan noktanın çevresi
      const r = e.currentTarget.getBoundingClientRect();
      const px = (e.clientX - r.left) / r.width * fm.w, py = (e.clientY - r.top) / r.height * fm.h;
      const s = Math.round(Math.min(fm.w, fm.h) * 0.18);
      face = [Math.round(px - s / 2), Math.round(py - s / 2), s, s];
    }
    $("#vFaces").classList.add("busy");
    try {
      const res = await api("/api/admin/avatar", { person: fm.pid, photo: fm.photo, face });
      fam.people[fm.pid].avatar = res.avatar;
      renderTrees();
      if (tgReady) { renderTgList(); renderPicked(); }
      if (modalPerson === fm.pid) $("#mAvatar").innerHTML = avatarHtml(fm.pid, fam.people[fm.pid], false);
      toast(`${fam.people[fm.pid].name} için profil fotoğrafı güncellendi`);
      endFaceMode();
    } catch (err) {
      $("#vFaces").classList.remove("busy");
      if (err.status === 401) { admin = false; renderAdmin(); endFaceMode(); }
      toast(err.message, true);
    }
  });
  window.addEventListener("resize", () => { if (faceMode) endFaceMode(); });

  function route() {
    const hash = decodeURIComponent(location.hash.slice(1));
    if (!$("#modal").hidden && hash !== modalPerson) closeModal();
    if (hash.startsWith("harita")) showView("map", hash.split("/")[1] || null);
    else if (hash.startsWith("birlikte")) showView("together", hash.split("/")[1] || "");
    else if (hash && fam.people[hash]) { if (view !== "tree") showView("tree"); openPerson(hash); }
  }

  load().then(() => {
    checkAdmin();
    route();
    window.addEventListener("hashchange", route);
  }).catch(() => {
    $("#tree").innerHTML = '<p class="hint">Aile ağacı yüklenemedi.</p>';
  });
})();
