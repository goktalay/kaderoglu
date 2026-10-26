# Sosyal katman: entegrasyon

Sosyal katman (hesaplar, zorunlu giriş, Duvar, yorum/beğeni, bildirimler, yükleme + yüz tanıma) tamamen
yeni dosyalarda duruyor. Ana dosyalarda gereken değişiklikler aşağıda; **zorunlu olan sadece 1. ve 2. madde**,
gerisi yönetici yetkisinin aile hesabıyla da çalışması için.

## Yeni dosyalar

| Dosya | Ne yapar |
|---|---|
| `app/social.py` | Giriş kapısı (`wrap()`), hesaplar, oturumlar, Duvar/yorum/beğeni/bildirim API'si, yükleme alma, arka plan işleyici, giriş mozaiği |
| `app/social_static/login.html, login.css, login.js` | `/giris`: arkada kayan fotoğraf mozaiği, giriş kartı, ilk girişte zorunlu şifre değiştirme |
| `app/social_static/wall.html, wall.css, wall.js` | `/duvar`: Instagram tarzı akış, paylaşım yükleme penceresi, `?u=kullanici` ile kişinin paylaşımları |
| `app/social_static/social.js, social.css` | Ortak bileşenler. Aile sayfasına eklenince kendini kurar: "Duvar" sekmesi, sağ üstte zil + kullanıcı menüsü, görüntüleyicide yorum/beğeni paneli |
| `tools/ingest_uploads.py` | Yüklemeyi işler (küçültme, EXIF/konum silme, video dönüştürme, yüz tanıma, çıplaklık kontrolü); `--archive` ile arşive alır |
| `tools/social_dev.py` | Test sunucusu (127.0.0.1:8766), `KADEROGLU_DATA` zorunlu |

Veri: `data/social.db` (SQLite, WAL), `data/uploads/raw/` (yüklenen asıl dosyalar, **hiç sunulmaz**),
`data/uploads/media/{full,thumb,video,vframes}/` (sitede gösterilenler, sadece girişle), `data/uploads/mosaic.jpg`,
`data/uploads/seeds.npz` (yüz tanıma örnekleri önbelleği). Ek paket gerekmez (multipart kullanılmıyor; dosya
gövdede ham olarak gelir).

---

## 1. `app/main.py` — giriş kapısını bağla (zorunlu)

```python
import family
import server
import social                                   # <-- ekle

...
PANEL_HOSTS = {h.lower() for h in CONFIG.get("panel_hosts", ["panel.kaderoglu.com"])}
family_app = social.wrap(family.app)            # <-- ekle (giriş zorunluluğu + /giris, /duvar, /social/...)

...
async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        return await family.app(scope, receive, send)
    host = _host(scope)
    target = server.app if host in PANEL_HOSTS else family_app      # <-- family.app yerine family_app
    await target(scope, receive, send)
```

`wrap()` şunları yapar:
- Oturumu olmayan (ya da şifresini henüz değiştirmemiş) isteklerde: sayfalar `303 /giris?next=...`, `/api/*`
  ve `/media/*` için `401`/`403` JSON. Tek istisna `/robots.txt`.
- `/giris`, `/duvar`, `/social/...` adreslerini `social.sub` uygulamasına verir (kendi güvenlik başlıkları ve
  Cloudflare-only kontrolü var).
- `/media/*` yanıtlarındaki `Cache-Control: public` değerini `private` yapar (süre ve `no-store` korunur).
- Oturumdaki kullanıcıyı `request.scope["social_user"]` içine koyar
  (`{"id","username","person","admin","must_change"}`); `family.py` bunu okuyabilir.
- İlk açılışta arka planda: tablolar, 19 hesap (bcrypt, ~5 sn), giriş mozaiği (~20 sn, günde bir yenilenir).

## 2. `app/family_static/index.html` — ortak bileşeni ekle (zorunlu)

Son iki script satırının altına:

```html
  <script src="/assets/timeline.js"></script>
  <script src="/assets/family.js"></script>
  <script src="/social/assets/social.js"></script>     <!-- ekle -->
```

`family.py`'deki `index()` sürüm ekleme döngüsü bu dosyayı bilmiyor; önbellek sorunu olmaz çünkü
`/social/assets/` her zaman `Cache-Control: no-cache` ile verilir. `social.js` kendi CSS'ini kendisi ekler.
`family.js`'de değişiklik gerekmez: panel `#viewer`'ın `hidden` özelliğini ve `#vImg`/`#vVid`'in
`src`/`poster` adreslerini (`/media/full/<kimlik>.jpg`) MutationObserver ile izler; ok tuşları ve dokunma
olayları panelden görüntüleyiciye geçmez.

## 3. `app/family.py` — aile hesabıyla yönetici yetkisi (önerilir)

Muhammed Kaderoğlu'nun aile hesabı (`muhammed.kaderoglu`) yönetici. Panel hesabıyla girişi de geçerli kalsın
diye `social.is_admin()` / `social.require_admin` ikisini de kabul eder.

```python
import server
import social                                   # <-- ekle (social family'yi içe aktarmaz, döngü yok)
```

Gizli medya kontrolü (`headers` ara katmanı, şu an satır ~92):

```python
            if not server.get_session(request):          # eski
            if not social.is_admin(request):             # yeni
```

`/api/me` (satır ~255):

```python
def me(request: Request):
    return {"admin": social.is_admin(request), "panel": bool(server.get_session(request))}
```

Yönetici uçlarındaki bağımlılık (satır ~259, ~271, ~288, ~325): `Depends(server.require_auth)` →
`Depends(social.require_admin)`. Örnek:

```python
@app.get("/api/admin/faces", dependencies=[Depends(social.require_admin)])
@app.get("/api/admin/hidden", dependencies=[Depends(social.require_admin)])
@app.post("/api/admin/hide", dependencies=[Depends(server.require_csrf), Depends(social.require_admin)])
@app.post("/api/admin/avatar", dependencies=[Depends(server.require_csrf), Depends(social.require_admin)])
```

`/media` önbellek satırı (satır ~115) `wrap()` zaten `private` yaptığı için şart değil, ama okuyan kafası
karışmasın diye:

```python
        h["Cache-Control"] = "private, max-age=86400"     # giriş zorunlu: Cloudflare saklamasın
```

## 4. `app/family_static/family.js` — "Yönetici girişi" düğmesi (isteğe bağlı)

Yönetici aile hesabıyla girmişse alttaki "Yönetici · Çıkış" düğmesi panel oturumunu kapatmaya çalışır ama
aile oturumu açık kaldığı için yönetici kalır. En basiti: `checkAdmin()` içinde yanıttaki `panel` alanına bak.

```js
  let admin = false, panelAdmin = false, faceMode = null;
  ...
  async function checkAdmin() {
    try { const me = await api("/api/me"); admin = me.admin; panelAdmin = me.panel; } catch { admin = false; }
    renderAdmin();
  }
  function renderAdmin() {
    const b = $("#adminBtn");
    b.hidden = admin && !panelAdmin;           // aile hesabıyla yönetici: düğmeye gerek yok
    ...
```

Çıkış ve şifre değiştirme sağ üstteki kullanıcı menüsünde (social.js).

## 5. `config.json` (isteğe bağlı)

```json
"social_auto_archive": false
```

`true` olursa her yükleme hazır olunca hemen `data/index.json`'a eklenir. **İçe aktarma (import_takeout /
faces / nudity) bitene kadar `false` kalmalı**: o araçlar index.json'u kendi kopyalarıyla yazar, buradaki
eklemeleri ezer. Bu sürede yüklemeler Duvar'da görünür, yüz tanıma ve bildirimler çalışır; sadece aile
ağacındaki galerilere girmezler.

## 6. Yayına alma adımları

1. Yukarıdaki 1–2 (ve 3) değişiklik, sonra "Kaderoglu Web" görevini yeniden başlat.
2. **Cloudflare önbelleğini temizle** (Caching → Purge Everything). Şimdiye kadar `/media/*` `public` olarak
   verildi; Cloudflare'deki kopyalar girişsiz de görünebilir. Purge sonrası yeni yanıtlar `private`.
3. İlk açılışta hesaplar açılır (log: `[social] hesap açıldı: ...`). Liste: `.venv\Scripts\python.exe app\social.py users`
4. Aileye: kullanıcı adı `ad.soyad`, başlangıç şifresi `config.json` → `social_default_password`; ilk girişte kendi şifrelerini belirlerler.
   Unutan olursa: `.venv\Scripts\python.exe app\social.py reset ayse.kaderoglu` (ya da yönetici olarak
   `POST /social/api/admin/reset {"username": ...}`).
5. İçe aktarma bittikten sonra yüklemeleri arşive almak için:
   `.venv\Scripts\python.exe tools\ingest_uploads.py --archive` (index.json son 2 dakikada değiştiyse durur;
   `--force` ile zorlanır). Ardından `tools\faces.py --match` normal fotoğraflar gibi yeniden eşleştirir.

## Dikkat edilecekler

- **Çerez**: `kf_session`, httpOnly, `SameSite=Lax` (WhatsApp'tan gelen bağlantıda oturum düşmesin diye
  Strict değil), https'te `Secure`, `Domain` yok (sadece kaderoglu.com). Oturumlar veritabanında (yeniden
  başlatmada kimse çıkmaz): 30 gün hareketsizlik / 90 gün en fazla. Şifre değişince diğer cihazlar kapanır.
- **CSRF**: tüm POST/DELETE'lerde `X-Requested-With: uzakpanel` (mevcut kodla aynı `server.require_csrf`).
- **Hız sınırı**: giriş IP başına 10, kullanıcı başına 8 hatalı deneme / 15 dk; kullanıcı yoksa da bcrypt
  çalışır (zamanlama farkı yok). Yükleme saatte 40, yorum dakikada 15.
- **Yükleme**: tür içerikten (sihirli baytlar) belirlenir, uzantı/başlık değil; fotoğraf 40 MB, video 600 MB,
  akışla diske yazılırken sınır kontrol edilir. Kimlik = içeriğin SHA-1'inin ilk 20 hanesi (içe aktarmayla aynı:
  aynı dosya arşivdeki fotoğrafla aynı öğe olur, yorumlar ortak).
- **EXIF/konum**: fotoğraflar Pillow ile yeniden kodlanır (EXIF yazılmaz), videolar `videos.transcode()`
  (`-map_metadata -1`). Testte GPS'li dosya yüklendi, çıktıda EXIF yok. Asıl dosya `uploads/raw/`'da kalır,
  sunulmaz.
- **Çıplaklık**: yüklemeler `nudity.detect/verdict` ile kontrol edilir; şüpheli olan sadece sahibine ve
  yöneticiye görünür, Duvar'da "Yayına al" düğmesi çıkar; onaylanana kadar bildirim gitmez, arşive alınmaz.
  Arşivdeki öğeler için `family.py`'nin kararı (`_hidden`, moderation.json) kullanılır.
- **Yorumlar** sunucuda ham metin saklanır, arayüzde sadece `textContent`/DOM düğümüyle yazılır (XSS testi:
  `<b>xss</b>` düz metin göründü). Kontrol ve yön değiştirme karakterleri silinir.
- **Bildirim kuralları**: `@kullanici` → `mention`; fotoğraftaki kişi (etiket ya da tanınan yüz) adı yorumda
  geçiyorsa `mention`, geçmiyorsa `tagged`; paylaşımın sahibi → `comment`; beğeni → sahibine `like` (kişi başına
  bir kez); yüklemede tanınan yüz → `face`; açıklamada `@` → `mention`. Kimse kendine bildirim almaz,
  herkes bir olaydan tek bildirim alır.
- **Yüz tanıma eşiği** arşivdeki etiketsiz yüzlerle aynı (`faces.RECOG_MIN` 0.50 + `RECOG_MARGIN` 0.06):
  yanlış kişi eklememek için bilerek sıkı. Örnekler `faces_cache.npz`'den hesaplanıp `uploads/seeds.npz`'de
  tutulur; içe aktarma sürerken en fazla 6 saatte bir yenilenir.
- **Giriş mozaiği** girişsiz görülen tek görüntü: 1408 adet 49 px kare tek JPEG'de (`/social/mosaic.jpg`,
  Cloudflare 1 saat saklayabilir). Sadece `family.py`'nin `_visible` kümesinden (çıplaklık kontrolünden
  geçmiş, gizlenmemiş, içinde aileden biri olan) seçilir. Büyük fotoğraf verilmez.
- `index.json` çok büyürse: `social.index_data()` aynı süreçte `family._load()` önbelleğini kullanır, ikinci
  kopya tutmaz.
