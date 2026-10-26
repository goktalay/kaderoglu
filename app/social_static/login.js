(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const api = (url, body) => fetch(url, {
    method: body ? "POST" : "GET", credentials: "same-origin",
    headers: { "X-Requested-With": "uzakpanel", ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  }).then(async (r) => {
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(d.detail || `Hata ${r.status}`), { status: r.status });
    return d;
  });
  const next = (() => {
    const n = new URLSearchParams(location.search).get("next") || "/";
    return n.startsWith("/") && !n.startsWith("//") && !n.startsWith("/giris") ? n : "/";
  })();

  /* ------------------------------------------------ arka plan: mozaik yüklenince belirsin, fareyle hafif kaysın */
  const tiles = $(".lg-tiles");
  const probe = new Image();
  probe.onload = () => tiles.classList.add("on");
  probe.src = "/social/mosaic.jpg";
  const tilt = $("#tilt");
  if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
    let raf = 0;
    window.addEventListener("pointermove", (e) => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        const x = (e.clientX / innerWidth - 0.5) * -36, y = (e.clientY / innerHeight - 0.5) * -28;
        tilt.style.setProperty("--px", x.toFixed(1) + "px");
        tilt.style.setProperty("--py", y.toFixed(1) + "px");
      });
    }, { passive: true });
  }

  /* ------------------------------------------------ şifre göster */
  document.querySelectorAll("[data-eye]").forEach((b) => b.addEventListener("click", () => {
    const i = b.parentElement.querySelector("input");
    const show = i.type === "password";
    i.type = show ? "text" : "password";
    b.textContent = show ? "Gizle" : "Göster";
  }));

  const shake = () => {
    const c = $(".lg-card");
    c.classList.remove("shake");
    void c.offsetWidth;
    c.classList.add("shake");
  };

  /* ------------------------------------------------ giriş */
  let lastPassword = null;
  const showChange = (name, needCurrent) => {
    $("#loginForm").hidden = true;
    $("#changeForm").hidden = false;
    if (name) $("#hello").textContent = `Hoş geldiniz, ${name.split(" ")[0]}!`;
    $("#currentWrap").hidden = !needCurrent;
    $("#currentWrap input").required = needCurrent;
    $(needCurrent ? "#currentWrap input" : "#changeForm input[name=new]").focus();
  };

  $("#loginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const btn = $("#loginBtn");
    btn.disabled = true;
    $("#loginErr").textContent = "";
    try {
      const d = await api("/social/api/login", { username: f.get("username"), password: f.get("password") });
      if (d.must_change) {
        lastPassword = f.get("password");
        const me = await api("/social/api/me").catch(() => ({}));
        showChange(me.name, false);
      } else location.replace(next);
    } catch (ex) {
      $("#loginErr").textContent = ex.message;
      shake();
    } finally {
      btn.disabled = false;
    }
  });

  /* ------------------------------------------------ zorunlu şifre değiştirme */
  const meter = $(".lg-meter i");
  $("#changeForm input[name=new]").addEventListener("input", (e) => {
    const v = e.target.value;
    let s = 0;
    if (v.length >= 8) s++;
    if (v.length >= 12) s++;
    if (/[a-zçğıöşü]/.test(v) && /[A-ZÇĞİÖŞÜ]/.test(v)) s++;
    if (/\d/.test(v)) s++;
    if (/[^\p{L}\d]/u.test(v)) s++;
    meter.style.width = Math.min(100, s * 22 + (v ? 10 : 0)) + "%";
    meter.style.background = s <= 1 ? "#c0392b" : s <= 2 ? "#d4a017" : "#4f7a4a";
  });

  $("#changeForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const err = $("#changeErr");
    err.textContent = "";
    if (f.get("new") !== f.get("again")) { err.textContent = "Yeni şifreler aynı değil"; return shake(); }
    const btn = $("#changeBtn");
    btn.disabled = true;
    try {
      await api("/social/api/password", { current: lastPassword ?? f.get("current"), new: f.get("new") });
      location.replace(next);
    } catch (ex) {
      err.textContent = ex.message;
      shake();
    } finally {
      btn.disabled = false;
    }
  });

  $("#changeOut").addEventListener("click", async () => {
    await api("/social/api/logout", {}).catch(() => {});
    location.reload();
  });

  /* ------------------------------------------------ zaten girişli ama şifresi değişmemişse */
  api("/social/api/me").then((me) => {
    if (me.must_change) showChange(me.name, true);
    else location.replace(next);
  }).catch(() => $("#username").focus());
})();
