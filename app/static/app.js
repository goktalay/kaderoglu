"use strict";

/* ================================================================ yardımcılar */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fmtBytes(b, d = 1) {
  if (b == null || isNaN(b)) return "–";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (Math.abs(b) >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(i === 0 ? 0 : d)} ${u[i]}`;
}
const fmtRate = (b) => (b == null ? "–" : fmtBytes(b) + "/s");
function fmtDur(s) {
  s = Math.max(0, Math.floor(s));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return `${d}g ${h}s ${m}dk`;
  if (h) return `${h}s ${m}dk`;
  if (m) return `${m}dk ${s % 60}sn`;
  return `${s % 60}sn`;
}
const fmtDate = (t) => (t ? new Date(t * 1000).toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" }) : "–");

function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = "toast"), 4000);
}

async function api(path, opts = {}) {
  const r = await fetch(path, {
    credentials: "same-origin",
    ...opts,
    headers: { "X-Requested-With": "uzakpanel", ...(opts.headers || {}) },
  });
  if (r.status === 401) {
    location.replace("/login");
    throw new Error("Oturum sona erdi");
  }
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(d.detail || `Hata ${r.status}`);
  }
  return r.json();
}
window.UP = { $, $$, esc, fmtBytes, fmtRate, fmtDur, fmtDate, toast, api };

/* ================================================================ grafikler */
Chart.defaults.color = css("--muted");
Chart.defaults.font.family = "Inter, system-ui, sans-serif";
Chart.defaults.font.size = 11;
Chart.defaults.borderColor = "rgba(148,163,214,.08)";
Chart.defaults.animation = false;

const COLORS = { cpu: css("--cpu"), ram: css("--ram"), gpu: css("--gpu"), disk: css("--disk"), up: css("--up"), down: css("--down"), vram: "#ffd166" };
const HIST = 180, SPARK = 60;

function gradient(ctx, color, h = 240) {
  const g = ctx.createLinearGradient(0, 0, 0, h);
  g.addColorStop(0, color + "55");
  g.addColorStop(1, color + "00");
  return g;
}

function gauge(id, color) {
  return new Chart($(id), {
    type: "doughnut",
    data: { datasets: [{ data: [0, 100], backgroundColor: [color, "rgba(255,255,255,.06)"], borderWidth: 0, borderRadius: 8 }] },
    options: { cutout: "78%", rotation: -135, circumference: 270, plugins: { legend: { display: false }, tooltip: { enabled: false } }, events: [] },
  });
}
function setGauge(ch, v) {
  v = Math.max(0, Math.min(100, v || 0));
  ch.data.datasets[0].data = [v, 100 - v];
  ch.update("none");
}

function spark(id, color) {
  const ctx = $(id).getContext("2d");
  return new Chart(ctx, {
    type: "line",
    data: { labels: Array(SPARK).fill(""), datasets: [{ data: Array(SPARK).fill(null), borderColor: color, backgroundColor: gradient(ctx, color, 46), fill: true, borderWidth: 2, pointRadius: 0, tension: .35 }] },
    options: { maintainAspectRatio: false, scales: { x: { display: false }, y: { display: false, min: 0, max: 100 } }, plugins: { legend: { display: false }, tooltip: { enabled: false } }, events: [] },
  });
}

function lineChart(id, sets, { yMax, yFmt, height = 240 } = {}) {
  const ctx = $(id).getContext("2d");
  return new Chart(ctx, {
    type: "line",
    data: {
      labels: Array(HIST).fill(""),
      datasets: sets.map((s) => ({
        label: s.label, data: Array(HIST).fill(null), borderColor: s.color,
        backgroundColor: s.fill === false ? "transparent" : gradient(ctx, s.color, height),
        fill: s.fill !== false, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: .3, borderDash: s.dash || [],
      })),
    },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { display: false },
        y: { min: 0, max: yMax, grid: { color: "rgba(148,163,214,.07)" }, ticks: { maxTicksLimit: 5, callback: yFmt || ((v) => v + "%") } },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#121a2e", borderColor: "rgba(148,163,214,.25)", borderWidth: 1, padding: 10,
          callbacks: { label: (c) => ` ${c.dataset.label}: ${c.raw == null ? "–" : yFmt ? yFmt(c.raw) : c.raw.toFixed(1) + "%"}` },
        },
      },
    },
  });
}

const charts = {
  gCpu: gauge("#gCpu", COLORS.cpu), gRam: gauge("#gRam", COLORS.ram), gGpu: gauge("#gGpu", COLORS.gpu), gDisk: gauge("#gDisk", COLORS.disk),
  sCpu: spark("#sCpu", COLORS.cpu), sRam: spark("#sRam", COLORS.ram), sGpu: spark("#sGpu", COLORS.gpu), sDisk: spark("#sDisk", COLORS.disk),
  usage: lineChart("#cUsage", [
    { label: "CPU", color: COLORS.cpu }, { label: "RAM", color: COLORS.ram, fill: false },
    { label: "GPU", color: COLORS.gpu }, { label: "VRAM", color: COLORS.vram, fill: false, dash: [4, 4] },
  ], { yMax: 100 }),
  net: lineChart("#cNet", [{ label: "İndirme", color: COLORS.down }, { label: "Yükleme", color: COLORS.up }], { yFmt: (v) => fmtRate(v) }),
  diskIo: lineChart("#cDiskIo", [{ label: "Okuma", color: COLORS.disk }, { label: "Yazma", color: COLORS.up }], { yFmt: (v) => fmtRate(v), height: 110 }),
};
charts.diskIo.options.scales.y.ticks.maxTicksLimit = 3;

charts.cores = new Chart($("#cCores"), {
  type: "bar",
  data: { labels: [], datasets: [{ data: [], borderRadius: 4, backgroundColor: [] }] },
  options: {
    maintainAspectRatio: false,
    scales: { x: { grid: { display: false }, ticks: { font: { size: 9 } } }, y: { min: 0, max: 100, ticks: { maxTicksLimit: 5, callback: (v) => v + "%" } } },
    plugins: { legend: { display: false }, tooltip: { callbacks: { title: (c) => "Çekirdek " + c[0].label, label: (c) => ` ${c.raw.toFixed(1)}%` } } },
  },
});

function push(ch, idx, v, max) {
  const d = ch.data.datasets[idx].data;
  d.push(v);
  while (d.length > max) d.shift();
}
function coreColor(v) {
  if (v > 85) return css("--danger");
  if (v > 60) return css("--disk");
  return COLORS.cpu;
}

/* ================================================================ metrikler */
let system = null;
let lastMetricsOk = 0;

async function loadSystem() {
  system = await api("/api/system");
  $("#hostname").textContent = system.hostname;
  $("#cpuModel").textContent = system.cpu_model;
  $("#cpuSub").textContent = system.cpu_model;
  $("#cpuCores").textContent = `${system.cores_physical} fiziksel / ${system.cores_logical} mantıksal`;
  document.title = `${system.hostname} · Uzak Panel`;
}

async function loadHistory() {
  const h = await api("/api/history");
  for (const p of h) applyPoint(p, false);
  [charts.usage, charts.net, charts.diskIo, charts.sCpu, charts.sRam, charts.sGpu, charts.sDisk].forEach((c) => c.update("none"));
}

function applyPoint(p) {
  push(charts.usage, 0, p.cpu, HIST); push(charts.usage, 1, p.ram, HIST);
  push(charts.usage, 2, p.gpu, HIST); push(charts.usage, 3, p.vram, HIST);
  push(charts.net, 0, p.down, HIST); push(charts.net, 1, p.up, HIST);
  push(charts.diskIo, 0, p.dr, HIST); push(charts.diskIo, 1, p.dw, HIST);
  push(charts.sCpu, 0, p.cpu, SPARK); push(charts.sRam, 0, p.ram, SPARK); push(charts.sGpu, 0, p.gpu, SPARK);
}

let diskSparkMax = 1;
function render(m) {
  const cpu = m.cpu.percent, ram = m.ram.percent;
  const g = m.gpus[0] || {};
  const sysDisk = m.disks.find((d) => d.mount.toUpperCase().startsWith("C:")) || m.disks[0] || {};

  applyPoint({
    cpu, ram, gpu: g.util, vram: g.mem_total ? (g.mem_used / g.mem_total) * 100 : null,
    up: m.net.up, down: m.net.down, dr: m.disk_io.read, dw: m.disk_io.write,
  });
  // disk kıvılcım grafiği: anlık G/Ç'yi son 60 sn'nin tepe değerine göre ölçekle
  const io = m.disk_io.read + m.disk_io.write;
  const sd = charts.sDisk.data.datasets[0].data;
  sd.push(io); while (sd.length > SPARK) sd.shift();
  diskSparkMax = Math.max(1, ...sd.filter((x) => x != null));
  charts.sDisk.options.scales.y.max = diskSparkMax * 1.15;

  setGauge(charts.gCpu, cpu); setGauge(charts.gRam, ram); setGauge(charts.gGpu, g.util); setGauge(charts.gDisk, sysDisk.percent);
  $("#vCpu").innerHTML = `${Math.round(cpu)}<small>%</small>`;
  $("#vRam").innerHTML = `${Math.round(ram)}<small>%</small>`;
  $("#vGpu").innerHTML = g.util == null ? "–" : `${Math.round(g.util)}<small>%</small>`;
  $("#vDisk").innerHTML = `${Math.round(sysDisk.percent || 0)}<small>%</small>`;

  $("#cpuFreq").textContent = m.cpu.freq ? `${(m.cpu.freq / 1000).toFixed(2)} GHz` : "–";
  $("#ramSub").textContent = `${fmtBytes(m.ram.used)} / ${fmtBytes(m.ram.total)}`;
  $("#ramFree").textContent = fmtBytes(m.ram.available);
  $("#swap").textContent = `${fmtBytes(m.swap.used)} (${Math.round(m.swap.percent)}%)`;
  $("#gpuSub").textContent = g.name || "GPU bulunamadı";
  $("#gpuVram").textContent = g.mem_total ? `${fmtBytes(g.mem_used)} / ${fmtBytes(g.mem_total)}` : "–";
  $("#gpuTemp").textContent = g.temp != null ? `${g.temp}°C` : "–";
  $("#diskSub").textContent = sysDisk.mount ? `${sysDisk.mount} · ${fmtBytes(sysDisk.free)} boş` : "–";
  $("#diskR").textContent = fmtRate(m.disk_io.read);
  $("#diskW").textContent = fmtRate(m.disk_io.write);
  $("#netDown").textContent = fmtRate(m.net.down);
  $("#netUp").textContent = fmtRate(m.net.up);
  $("#uptime").textContent = fmtDur(m.uptime);
  $("#procCount").textContent = m.process_count;

  // GPU kutucukları
  $("#gpuName").textContent = g.name || "";
  const pct = (a, b) => (a != null && b ? Math.min(100, (a / b) * 100) : 0);
  $("#tPower").innerHTML = g.power != null ? `${g.power.toFixed(0)}<small> / ${g.power_limit?.toFixed(0) ?? "?"} W</small>` : "–";
  $("#bPower").style.width = pct(g.power, g.power_limit) + "%";
  $("#tTemp").innerHTML = g.temp != null ? `${g.temp}<small> °C</small>` : "–";
  $("#bTemp").style.width = pct(g.temp, 95) + "%";
  $("#tClock").innerHTML = g.clock != null ? `${g.clock}<small> MHz</small>` : "–";
  $("#bClock").style.width = pct(g.clock, g.clock_max) + "%";
  $("#tFan").innerHTML = g.fan != null ? `${g.fan}<small> %</small>` : "–";
  $("#bFan").style.width = (g.fan || 0) + "%";

  // çekirdekler
  const cores = m.cpu.per_core;
  const cc = charts.cores;
  if (cc.data.labels.length !== cores.length) cc.data.labels = cores.map((_, i) => String(i));
  cc.data.datasets[0].data = cores;
  cc.data.datasets[0].backgroundColor = cores.map(coreColor);
  $("#coreInfo").textContent = `${cores.length} iş parçacığı · en yüksek ${Math.round(Math.max(...cores))}%`;

  // sürücüler
  let tot = 0, used = 0;
  $("#drives").innerHTML = m.disks.map((d) => {
    tot += d.total; used += d.used;
    const c = d.percent > 90 ? "var(--danger)" : d.percent > 75 ? "var(--disk)" : "linear-gradient(90deg,#ffb547,#ff8a47)";
    return `<div class="drive"><div class="row"><b>${esc(d.mount)} <span>${esc(d.fstype)}</span></b><span>${fmtBytes(d.used)} / ${fmtBytes(d.total)} · %${Math.round(d.percent)}</span></div>
      <div class="bar"><i style="width:${d.percent}%;background:${c}"></i></div></div>`;
  }).join("");
  $("#diskTotal").textContent = `${fmtBytes(used)} / ${fmtBytes(tot)}`;

  Object.values(charts).forEach((c) => { if (c.config.type !== "doughnut") c.update("none"); });
}

async function tickMetrics() {
  try {
    const m = await api("/api/metrics");
    if (m && m.cpu) render(m);
    lastMetricsOk = Date.now();
  } catch (e) { /* bağlantı göstergesi aşağıda */ }
  const live = $("#live");
  const ok = Date.now() - lastMetricsOk < 5000;
  live.classList.toggle("off", !ok);
  live.lastElementChild.textContent = ok ? "Canlı" : "Bağlantı yok";
  setTimeout(tickMetrics, document.hidden ? 4000 : 1000);
}

/* ================================================================ süreçler */
const procState = { sort: { k: "cpu", dir: -1 }, mode: "group", q: "", paused: false, rows: [] };
const AV_COLORS = ["#4f9dff", "#a77bff", "#2ee6a6", "#ffb547", "#ff6b9a", "#38d1ff", "#7ee081", "#ff8a47"];
function avColor(name) {
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return AV_COLORS[h % AV_COLORS.length];
}

function groupProcs(rows) {
  const map = new Map();
  for (const r of rows) {
    const key = r.name.toLowerCase();
    let g = map.get(key);
    if (!g) {
      g = { ...r, count: 0, cpu: 0, mem: 0, threads: 0, io: 0, pids: [] };
      map.set(key, g);
    }
    g.count++; g.cpu += r.cpu; g.mem += r.mem; g.threads += r.threads; g.io += r.io_r + r.io_w;
    g.pids.push(r.pid);
    if (r.pid < g.pid) g.pid = r.pid;
    if (!g.user && r.user) g.user = r.user;
    if (r.status === "running") g.status = "running";
  }
  return [...map.values()];
}

function renderProcs() {
  const { sort, mode, q } = procState;
  let rows = procState.rows.map((r) => ({ ...r, io: r.io_r + r.io_w, count: 1 }));
  if (mode === "group") rows = groupProcs(procState.rows);
  if (q) {
    const s = q.toLowerCase();
    rows = rows.filter((r) => r.name.toLowerCase().includes(s) || String(r.pid).includes(s) || (r.user || "").toLowerCase().includes(s));
  }
  rows.sort((a, b) => {
    const x = a[sort.k], y = b[sort.k];
    if (typeof x === "string" || typeof y === "string") return String(x ?? "").localeCompare(String(y ?? ""), "tr") * sort.dir;
    return ((x ?? 0) - (y ?? 0)) * sort.dir;
  });

  const totalRam = system?.ram_total || 1;
  const cpuSum = procState.rows.reduce((s, r) => s + r.cpu, 0);
  const memSum = procState.rows.reduce((s, r) => s + r.mem, 0);
  $("#procSummary").textContent = `${rows.length} kayıt · CPU %${cpuSum.toFixed(0)} · Bellek ${fmtBytes(memSum)}`;
  $("#procTabCount").textContent = procState.rows.length;

  const html = rows.slice(0, 1500).map((r) => {
    const memPct = (r.mem / totalRam) * 100;
    const heat = r.cpu > 25 ? "heat-3" : r.cpu > 8 ? "heat-2" : r.cpu > 2 ? "heat-1" : "";
    const cpuColor = r.cpu > 25 ? "var(--danger)" : r.cpu > 8 ? "var(--disk)" : "var(--cpu)";
    const name = r.name.replace(/\.exe$/i, "");
    return `<tr class="${heat}" title="${esc(r.exe || r.name)}">
      <td class="name"><div class="pname"><span class="av" style="background:${avColor(r.name)}">${esc(name.charAt(0).toUpperCase())}</span>
        <span class="n">${esc(name)}</span>${r.count > 1 ? `<span class="grp">${r.count}</span>` : ""}</div></td>
      <td class="num muted">${r.pid}</td>
      <td class="muted">${esc(r.user || "SYSTEM")}</td>
      <td class="num"><div class="meter"><span class="mbar"><i style="width:${Math.min(100, r.cpu)}%;background:${cpuColor}"></i></span><span class="mv">${r.cpu.toFixed(1)}%</span></div></td>
      <td class="num"><div class="meter"><span class="mbar"><i style="width:${Math.min(100, memPct * 4)}%;background:var(--ram)"></i></span><span class="mv">${fmtBytes(r.mem)}</span></div></td>
      <td class="num">${r.io > 1024 ? fmtRate(r.io) : '<span class="muted">0</span>'}</td>
      <td class="num muted">${r.threads}</td>
      <td><span class="status s-${esc(r.status)}">${esc(statusTr(r.status))}</span></td>
    </tr>`;
  }).join("");
  $("#procTable tbody").innerHTML = html || `<tr><td colspan="8" class="empty">Sonuç yok</td></tr>`;

  $$("#procTable th").forEach((th) => {
    const on = th.dataset.k === sort.k;
    th.classList.toggle("sorted", on);
    th.querySelector(".arr").textContent = on && sort.dir === 1 ? "▲" : "▼";
  });
}
function statusTr(s) {
  return { running: "Çalışıyor", sleeping: "Beklemede", stopped: "Durdu", zombie: "Zombi", idle: "Boşta", "disk-sleep": "G/Ç bekliyor" }[s] || s || "–";
}

async function tickProcs() {
  if (!procState.paused && $("#pane-procs").classList.contains("active") && !document.hidden) {
    try {
      procState.rows = await api("/api/processes");
      renderProcs();
    } catch (e) { /* bir sonraki turda tekrar */ }
  }
  setTimeout(tickProcs, 2000);
}

$$("#procTable th").forEach((th) => th.addEventListener("click", () => {
  const k = th.dataset.k;
  const s = procState.sort;
  if (s.k === k) s.dir *= -1;
  else { s.k = k; s.dir = ["name", "user", "status"].includes(k) ? 1 : -1; }
  renderProcs();
}));
$("#procSearch").addEventListener("input", (e) => { procState.q = e.target.value.trim(); renderProcs(); });
$$("#procMode button").forEach((b) => b.addEventListener("click", () => {
  $$("#procMode button").forEach((x) => x.classList.toggle("on", x === b));
  procState.mode = b.dataset.mode;
  renderProcs();
}));
$("#procPause").addEventListener("click", (e) => {
  procState.paused = !procState.paused;
  e.currentTarget.textContent = procState.paused ? "Devam et" : "Duraklat";
});

/* ================================================================ sekmeler / çıkış */
$$(".tab").forEach((t) => t.addEventListener("click", () => {
  $$(".tab").forEach((x) => x.classList.toggle("active", x === t));
  $$(".tab-pane").forEach((p) => p.classList.toggle("active", p.id === "pane-" + t.dataset.tab));
  if (t.dataset.tab === "files") window.dispatchEvent(new Event("files:open"));
}));

$("#logout").addEventListener("click", async () => {
  try { await api("/api/logout", { method: "POST" }); } catch { /* yine de çık */ }
  location.replace("/login");
});

(async () => {
  try {
    await loadSystem();
    await loadHistory();
  } catch (e) {
    toast(e.message, true);
  }
  tickMetrics();
  tickProcs();
})();
