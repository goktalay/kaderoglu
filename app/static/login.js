const form = document.getElementById("form");
const btn = document.getElementById("submit");
const err = document.getElementById("error");
const pw = document.getElementById("password");

document.getElementById("pwToggle").addEventListener("click", () => {
  pw.type = pw.type === "password" ? "text" : "password";
  pw.focus();
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  err.textContent = "";
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>';
  try {
    const r = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "uzakpanel" },
      body: JSON.stringify({ username: form.username.value, password: pw.value }),
    });
    if (r.ok) {
      location.replace("/");
      return;
    }
    const data = await r.json().catch(() => ({}));
    err.textContent = data.detail || "Giriş başarısız";
    pw.value = "";
    pw.focus();
    form.classList.remove("shake");
    void form.offsetWidth;
    form.classList.add("shake");
  } catch {
    err.textContent = "Sunucuya ulaşılamadı";
  }
  btn.disabled = false;
  btn.textContent = "Giriş yap";
});
