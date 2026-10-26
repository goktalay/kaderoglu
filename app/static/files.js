"use strict";
/* Dosya gezgini ve klasör kopyalama (File System Access API ile ağaç görünümünde). */
(() => {
  const { $, $$, esc, fmtBytes, fmtRate, fmtDur, fmtDate, toast, api } = window.UP;

  const ICON = {
    folder: '<svg class="is-dir fi" viewBox="0 0 24 24" fill="currentColor"><path d="M3 6.5A2.5 2.5 0 0 1 5.5 4h3.6c.6 0 1.2.3 1.6.7L12 6h6.5A2.5 2.5 0 0 1 21 8.5v9a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z" opacity=".9"/></svg>',
    file: '<svg class="is-file fi" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>',
    drive: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="13" width="20" height="8" rx="2"/><path d="M5 13l2.5-8h9L19 13M17 17h.01"/></svg>',
    star: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 6.5A2.5 2.5 0 0 1 5.5 4h3.6c.6 0 1.2.3 1.6.7L12 6h6.5A2.5 2.5 0 0 1 21 8.5v9a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/></svg>',
    dl: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 3v12M7 10l5 5 5-5M5 21h14"/></svg>',
    copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/></svg>',
    caret: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M9 6l6 6-6 6"/></svg>',
  };
  const canCopy = "showDirectoryPicker" in window;
  const qs = (p) => "path=" + encodeURIComponent(p);

  /* ============================================================ gezgin */
  const fs = { cwd: null, parent: null, entries: [], sort: { k: "name", dir: 1 }, loaded: false };

  function joinPath(base, name) {
    return base.endsWith("\\") || base.endsWith("/") ? base + name : base + "\\" + name;
  }

  async function loadRoots() {
    const r = await api("/api/fs/roots");
    $("#roots").innerHTML =
      r.drives.map((d) => `<button class="chip drv" data-path="${esc(d.path)}">${ICON.drive}${esc(d.path)} <span class="muted small">${fmtBytes(d.free)} boş</span></button>`).join("") +
      r.quick.map((q) => `<button class="chip" data-path="${esc(q.path)}">${ICON.star}${esc(q.name)}</button>`).join("");
    $$("#roots .chip").forEach((c) => c.addEventListener("click", () => open(c.dataset.path)));
    return r;
  }

  async function open(path) {
    try {
      const r = await api("/api/fs/list?" + qs(path));
      fs.cwd = r.path; fs.parent = r.parent; fs.entries = r.entries;
      renderCrumbs();
      renderFiles();
    } catch (e) {
      toast(e.message, true);
    }
  }

  function renderCrumbs() {
    const p = fs.cwd;
    const m = p.match(/^([A-Za-z]:\\|\\\\[^\\]+\\[^\\]+\\?|\/)/);
    const root = m ? m[0] : "";
    const rest = p.slice(root.length).split(/[\\/]/).filter(Boolean);
    const parts = [{ name: root.replace(/\\$/, "") || root, path: root }];
    let acc = root;
    for (const seg of rest) {
      acc = joinPath(acc, seg);
      parts.push({ name: seg, path: acc });
    }
    $("#crumbs").innerHTML = parts.map((x, i) => (i ? '<span class="sep">›</span>' : "") + `<button data-path="${esc(x.path)}">${esc(x.name)}</button>`).join("");
    $$("#crumbs button").forEach((b) => b.addEventListener("click", () => open(b.dataset.path)));
    $("#fsUp").disabled = !fs.parent;
  }

  function renderFiles() {
    const showHidden = $("#showHidden").checked;
    const { k, dir } = fs.sort;
    const rows = fs.entries.filter((e) => showHidden || !e.hidden).sort((a, b) => {
      if (a.dir !== b.dir) return a.dir ? -1 : 1;
      if (k === "name") return a.name.localeCompare(b.name, "tr", { numeric: true }) * dir;
      return ((a[k] || 0) - (b[k] || 0)) * dir;
    });
    $("#fileTable tbody").innerHTML = rows.map((e) => {
      const full = joinPath(fs.cwd, e.name);
      const actions = e.dir
        ? `<button class="icon-btn" data-act="zip" title="ZIP olarak indir">${ICON.dl}ZIP</button>` +
          (canCopy ? `<button class="icon-btn" data-act="copy" title="Bilgisayarıma kopyala">${ICON.copy}Kopyala</button>` : "")
        : `<button class="icon-btn" data-act="dl" title="İndir">${ICON.dl}İndir</button>`;
      return `<tr class="${e.dir ? "dir-row" : ""} ${e.hidden ? "hidden-f" : ""}" data-path="${esc(full)}" data-dir="${e.dir ? 1 : 0}">
        <td class="name"><div class="fname">${e.dir ? ICON.folder : ICON.file}<span>${esc(e.name)}</span></div></td>
        <td class="num muted">${e.dir ? "—" : fmtBytes(e.size)}</td>
        <td class="muted">${fmtDate(e.mtime)}</td>
        <td class="num"><div class="row-actions">${actions}</div></td></tr>`;
    }).join("") || `<tr><td colspan="4" class="empty">Bu klasör boş</td></tr>`;

    $$("#fileTable th[data-k]").forEach((th) => {
      const on = th.dataset.k === k;
      th.classList.toggle("sorted", on);
      th.querySelector(".arr").textContent = on && dir === -1 ? "▼" : "▲";
    });
  }

  function download(url) {
    const a = document.createElement("a");
    a.href = url;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  $("#fileTable tbody").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-path]");
    if (!tr) return;
    const btn = e.target.closest("[data-act]");
    const path = tr.dataset.path;
    if (btn) {
      const act = btn.dataset.act;
      if (act === "dl") download("/api/fs/download?" + qs(path));
      if (act === "zip") { download("/api/fs/zip?" + qs(path)); toast("ZIP hazırlanıyor, indirme birazdan başlayacak…"); }
      if (act === "copy") startCopy(path);
      return;
    }
    if (tr.dataset.dir === "1") open(path);
  });
  $$("#fileTable th[data-k]").forEach((th) => th.addEventListener("click", () => {
    const s = fs.sort;
    if (s.k === th.dataset.k) s.dir *= -1; else { s.k = th.dataset.k; s.dir = th.dataset.k === "name" ? 1 : -1; }
    renderFiles();
  }));
  $("#showHidden").addEventListener("change", renderFiles);
  $("#fsUp").addEventListener("click", () => fs.parent && open(fs.parent));
  $("#fsZip").addEventListener("click", () => { download("/api/fs/zip?" + qs(fs.cwd)); toast("ZIP hazırlanıyor, indirme birazdan başlayacak…"); });
  $("#fsCopy").addEventListener("click", () => startCopy(fs.cwd));
  if (!canCopy) {
    $("#fsCopy").disabled = true;
    $("#fsCopy").title = "Klasör kopyalama Chrome veya Edge gerektirir. ZIP indirmeyi kullanabilirsiniz.";
  }

  window.addEventListener("files:open", async () => {
    if (fs.loaded) return;
    fs.loaded = true;
    try {
      const r = await loadRoots();
      const home = r.quick.find((q) => q.name === "Ev");
      await open(home ? home.path : r.drives[0]?.path || "C:\\");
    } catch (e) {
      toast(e.message, true);
    }
  });

  /* ============================================================ kopyalama */
  const WORKERS = 4;
  const MAX_CHILDREN_RENDER = 2000;
  let job = null;

  function buildTree(t) {
    const root = { name: t.name, rel: "", dir: true, children: [], size: 0, done: 0, files: 0, filesDone: 0, errors: 0, parent: null };
    const dirs = new Map([["", root]]);
    const ensureDir = (rel) => {
      if (dirs.has(rel)) return dirs.get(rel);
      const i = rel.lastIndexOf("/");
      const parent = ensureDir(i < 0 ? "" : rel.slice(0, i));
      const n = { name: rel.slice(i + 1), rel, dir: true, children: [], size: 0, done: 0, files: 0, filesDone: 0, errors: 0, parent };
      parent.children.push(n);
      dirs.set(rel, n);
      return n;
    };
    t.dirs.forEach(ensureDir);
    const fileNodes = [];
    for (const f of t.files) {
      const i = f.p.lastIndexOf("/");
      const parent = ensureDir(i < 0 ? "" : f.p.slice(0, i));
      const n = { name: f.p.slice(i + 1), rel: f.p, dir: false, size: f.s, done: 0, state: "wait", parent };
      parent.children.push(n);
      for (let p = parent; p; p = p.parent) { p.size += f.s; p.files++; }
    }
    // klasörler önce, alfabetik; dosyalar sırayla (ağaçta yukarıdan aşağı ilerlesin)
    const sortRec = (n) => {
      n.children.sort((a, b) => (a.dir !== b.dir ? (a.dir ? -1 : 1) : a.name.localeCompare(b.name, "tr", { numeric: true })));
      n.children.forEach((c) => (c.dir ? sortRec(c) : fileNodes.push(c)));
    };
    sortRec(root);
    return { root, dirs, fileNodes };
  }

  function nodeRow(n, depth) {
    const wrap = document.createElement("div");
    const row = document.createElement("div");
    row.className = "tnode";
    row.style.paddingLeft = 14 + depth * 18 + "px";
    const hasKids = n.dir && n.children.length;
    row.innerHTML = `<div class="tl"><button class="caret ${hasKids ? "" : "none"}">${ICON.caret}</button>${n.dir ? ICON.folder : ICON.file}
      <span class="nm" title="${esc(n.rel || n.name)}">${esc(n.name)}</span><span class="sz">${fmtBytes(n.size)}${n.dir ? ` · ${n.files} dosya` : ""}</span></div>
      <div class="pb"><i></i></div><div class="pc">–</div>`;
    wrap.appendChild(row);
    n.el = row;
    n.barEl = row.querySelector(".pb i");
    n.pcEl = row.querySelector(".pc");
    if (hasKids) {
      const kids = document.createElement("div");
      kids.hidden = true;
      wrap.appendChild(kids);
      n.kidsEl = kids;
      const caret = row.querySelector(".caret");
      const toggle = () => {
        const open = kids.hidden;
        if (open && !n.rendered) renderChildren(n, depth + 1);
        kids.hidden = !open;
        caret.classList.toggle("open", open);
      };
      caret.addEventListener("click", toggle);
      row.querySelector(".nm").addEventListener("click", toggle);
      n.toggle = toggle;
    }
    updateNode(n);
    return wrap;
  }

  function renderChildren(n, depth) {
    n.rendered = true;
    const frag = document.createDocumentFragment();
    n.children.slice(0, MAX_CHILDREN_RENDER).forEach((c) => frag.appendChild(nodeRow(c, depth)));
    if (n.children.length > MAX_CHILDREN_RENDER) {
      const more = document.createElement("div");
      more.className = "tnode muted small";
      more.style.paddingLeft = 14 + depth * 18 + "px";
      more.textContent = `… ve ${n.children.length - MAX_CHILDREN_RENDER} öğe daha (ilerleme üst klasörde görünür)`;
      frag.appendChild(more);
    }
    n.kidsEl.appendChild(frag);
  }

  function updateNode(n) {
    if (!n.el) return;
    let pct, done, err;
    if (n.dir) {
      pct = n.size ? (n.done / n.size) * 100 : n.files ? (n.filesDone / n.files) * 100 : 100;
      done = n.filesDone >= n.files;
      err = done && n.errors > 0;
      n.pcEl.textContent = err ? `${n.errors} hata` : done ? "✓" : n.done || n.filesDone ? `%${pct.toFixed(0)}` : "–";
    } else {
      pct = n.size ? (n.done / n.size) * 100 : n.state === "done" ? 100 : 0;
      done = n.state === "done";
      err = n.state === "err";
      n.pcEl.textContent = err ? "Hata" : done ? "✓" : n.state === "run" ? `%${pct.toFixed(0)}` : "–";
      if (err) n.el.title = n.error || "";
    }
    n.barEl.style.width = Math.min(100, pct) + "%";
    n.el.classList.toggle("done", done && !err);
    n.el.classList.toggle("err", !!err);
    n.el.classList.toggle("active", !n.dir && n.state === "run");
  }

  function markDirty(n) {
    for (let p = n; p; p = p.parent) job.dirty.add(p);
  }

  function frame() {
    if (!job) return;
    job.dirty.forEach(updateNode);
    job.dirty.clear();
    const now = performance.now();
    if (now - job.speedT > 1000) {
      job.speed = ((job.root.done - job.speedB) / (now - job.speedT)) * 1000;
      job.speedT = now;
      job.speedB = job.root.done;
    }
    const r = job.root;
    const pct = r.size ? (r.done / r.size) * 100 : r.files ? (r.filesDone / r.files) * 100 : 100;
    $("#copyBar").style.width = pct + "%";
    $("#copyFiles").textContent = `${r.filesDone.toLocaleString("tr")} / ${r.files.toLocaleString("tr")}`;
    $("#copyBytes").textContent = `${fmtBytes(r.done)} / ${fmtBytes(r.size)}`;
    $("#copySpeed").textContent = job.finished ? "–" : fmtRate(job.speed);
    $("#copyEta").textContent = job.finished || !job.speed ? "–" : fmtDur((r.size - r.done) / job.speed);
    if (!job.finished) requestAnimationFrame(frame);
  }

  async function dirHandle(rel) {
    if (!job.handles.has(rel)) {
      const i = rel.lastIndexOf("/");
      const p = dirHandle(i < 0 ? "" : rel.slice(0, i)).then((parent) => parent.getDirectoryHandle(rel.slice(i + 1), { create: true }));
      job.handles.set(rel, p);
    }
    return job.handles.get(rel);
  }

  async function copyFile(n) {
    n.state = "run";
    markDirty(n);
    for (let attempt = 1; ; attempt++) {
      let writable;
      try {
        const i = n.rel.lastIndexOf("/");
        const dir = await dirHandle(i < 0 ? "" : n.rel.slice(0, i));
        const fh = await dir.getFileHandle(n.name, { create: true });
        const res = await fetch("/api/fs/download?" + qs(job.src + "\\" + n.rel.replaceAll("/", "\\")), { signal: job.abort.signal, credentials: "same-origin" });
        if (res.status === 401) { location.replace("/login"); return; }
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
        writable = await fh.createWritable();
        const reader = res.body.getReader();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          await writable.write(value);
          n.done += value.length;
          for (let p = n.parent; p; p = p.parent) p.done += value.length;
          markDirty(n);
        }
        await writable.close();
        n.state = "done";
        break;
      } catch (e) {
        if (writable) await writable.abort().catch(() => {});
        // yarım kalan baytları geri al
        for (let p = n.parent; p; p = p.parent) p.done -= n.done;
        n.done = 0;
        if (job.abort.signal.aborted) { n.state = "wait"; markDirty(n); return; }
        if (attempt < 2 && !/HTTP 4\d\d|erişim|izin/i.test(e.message)) continue;
        n.state = "err";
        n.error = e.message;
        for (let p = n.parent; p; p = p.parent) p.errors++;
        job.errors.push(`${n.rel}: ${e.message}`);
        break;
      }
    }
    for (let p = n.parent; p; p = p.parent) p.filesDone++;
    markDirty(n);
  }

  async function startCopy(srcPath) {
    if (!canCopy) { toast("Klasör kopyalama için Chrome veya Edge kullanın; diğer tarayıcılarda ZIP indirin.", true); return; }
    if (job && !job.finished) { toast("Devam eden bir kopyalama var.", true); return; }

    let dest;
    try {
      dest = await window.showDirectoryPicker({ id: "uzakpanel", mode: "readwrite", startIn: "downloads" });
    } catch {
      return; // kullanıcı vazgeçti
    }

    const panel = $("#copyPanel");
    panel.hidden = false;
    $("#filesLayout").classList.add("with-copy");
    $("#copyTree").innerHTML = '<div class="empty"><span class="spinner" style="display:inline-block;vertical-align:middle"></span> Klasör taranıyor…</div>';
    $("#copyTitle").textContent = srcPath;
    $("#copyState").textContent = "Taranıyor";
    $("#copyNote").textContent = "";
    $("#copyCancel").hidden = false;
    $("#copyClose").hidden = true;

    let tree;
    try {
      tree = await api("/api/fs/tree?" + qs(srcPath));
    } catch (e) {
      $("#copyTree").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
      $("#copyState").textContent = "Hata";
      $("#copyCancel").hidden = true;
      $("#copyClose").hidden = false;
      return;
    }

    const { root, dirs, fileNodes } = buildTree(tree);
    const destRoot = await dest.getDirectoryHandle(tree.name.replace(/[\\/:*?"<>|]/g, "_"), { create: true });
    job = {
      src: tree.root.replace(/\\$/, ""), root, abort: new AbortController(), handles: new Map([["", Promise.resolve(destRoot)]]),
      dirty: new Set(), errors: [...tree.errors.map((x) => `${x.p}: ${x.e}`)], finished: false,
      speed: 0, speedT: performance.now(), speedB: 0, started: performance.now(),
    };
    $("#copyTitle").textContent = `${tree.name} → ${dest.name}`;
    $("#copyState").textContent = "Kopyalanıyor";
    if (tree.truncated) $("#copyNote").textContent = "Uyarı: klasör çok büyük, listenin bir kısmı alındı.";

    const treeEl = $("#copyTree");
    treeEl.innerHTML = "";
    treeEl.appendChild(nodeRow(root, 0));
    root.toggle?.();
    requestAnimationFrame(frame);

    // boş klasörler dahil tüm klasörleri oluştur
    try {
      await Promise.all([...dirs.keys()].filter(Boolean).map(dirHandle));
    } catch (e) {
      job.errors.push("Klasör oluşturulamadı: " + e.message);
    }

    let next = 0;
    const worker = async () => {
      while (next < fileNodes.length && !job.abort.signal.aborted) await copyFile(fileNodes[next++]);
    };
    await Promise.all(Array.from({ length: WORKERS }, worker));

    // içi boş klasörlerin ✓ göstermesi için
    dirs.forEach((d) => job.dirty.add(d));
    job.dirty.forEach(updateNode);
    job.finished = true;
    frame();
    const took = fmtDur((performance.now() - job.started) / 1000);
    const cancelled = job.abort.signal.aborted;
    $("#copyState").textContent = cancelled ? "İptal edildi" : job.errors.length ? `${job.errors.length} hata ile bitti` : `Tamamlandı · ${took}`;
    if (job.errors.length) {
      $("#copyNote").textContent = job.errors.slice(0, 3).join(" • ") + (job.errors.length > 3 ? ` • +${job.errors.length - 3}` : "");
      $("#copyNote").title = job.errors.join("\n");
    }
    $("#copyCancel").hidden = true;
    $("#copyClose").hidden = false;
    toast(cancelled ? "Kopyalama iptal edildi" : `Kopyalama bitti: ${root.filesDone - root.errors} dosya, ${fmtBytes(root.done)}`, !!job.errors.length || cancelled);
  }

  $("#copyCancel").addEventListener("click", () => job?.abort.abort());
  $("#copyClose").addEventListener("click", () => {
    $("#copyPanel").hidden = true;
    $("#filesLayout").classList.remove("with-copy");
  });
  window.addEventListener("beforeunload", (e) => {
    if (job && !job.finished) { e.preventDefault(); e.returnValue = ""; }
  });
})();
