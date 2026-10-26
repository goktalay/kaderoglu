# kaderoglu.com

Tek sunucuda iki site, gelen alan adına göre ayrılır ([app/main.py](app/main.py)):

| Adres | Ne |
| --- | --- |
| `https://kaderoglu.com` | Aile ağacı sitesi, herkese açık ([app/family.py](app/family.py)) |
| `https://panel.kaderoglu.com` | Bilgisayar paneli, şifreli giriş ([app/server.py](app/server.py)) |

## Kurulum (yeni bilgisayar)

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy config.example.json config.json        # değerleri doldurun
.venv\Scripts\python.exe setup_user.py      # panel e-postası ve şifresi (bcrypt özeti config.json'a yazılır)
.venv\Scripts\python.exe tools\get_models.py # yüz tanıma ve çıplaklık modelleri (~70 MB)
```

Cloudflare origin sertifikası `certs/origin.pem` + `certs/origin.key`. Depoya **girmeyenler** (`.gitignore`):
`config.json`, `certs/`, `data/` (fotoğraflar, videolar, dizin, kişiler, veritabanı), `models/`, `logs/`, `.venv/`.

## Çalıştırma

Windows'ta oturum açılınca **"Kaderoglu Web"** zamanlanmış görevi sunucuyu pencere açmadan başlatır,
çökerse 1 dakika içinde yeniden başlatır. Loglar: `logs/server.log`.

```powershell
Start-ScheduledTask -TaskName "Kaderoglu Web"     # başlat
Stop-ScheduledTask  -TaskName "Kaderoglu Web"     # durdur
```

Kodu değiştirdikten sonra görevi durdurup yeniden başlatın. Yerel test için: `.venv\Scripts\python.exe run.py --dev`
(aile sitesi `http://localhost:8765`, panel `http://panel.localhost:8765`).

## Aile sitesi

### Ağaç

Kişiler ve ağaç yapısı [data/family.json](data/family.json) dosyasında. Kişi eklemek, rol yazmak veya ağaçta
yer değiştirmek için bu dosyayı düzenleyin; sunucuyu yeniden başlatmaya gerek yok.

- `aliases`: Google Fotoğraflar'daki etiket adı ağaçtaki addan farklıysa buraya yazın.
- `avatar_photo`: Profil fotoğrafını elle seçmek için fotoğraf kimliği (fotoğraf adresindeki `/media/full/<kimlik>.jpg`).
  Değiştirdikten sonra: `.venv\Scripts\python.exe tools\avatars.py` (yüz algılamayla seçip yüze odaklı kırpar).
- `match_name: false`: Görünen ad etiket eşleştirmede kullanılmaz, sadece `aliases` (aynı adlı iki kişi için).
- `not_before`: Bu tarihten önce çekilmiş fotoğraflardaki etiketi yok say (ör. doğum tarihi).

### Fotoğrafları Google Fotoğraflar'dan almak

1. https://takeout.google.com adresinde **Tümünün seçimini kaldır**, sadece **Google Fotoğraflar**'ı seçin.
2. Dosya türü `.zip`, boyut **50 GB**. Dışa aktarma bitince gelen e-postadaki zip dosyalarının hepsini tek bir klasöre indirin.
3. İçe aktarın (zip'leri açmanıza gerek yok):

```powershell
.venv\Scripts\python.exe tools\import_takeout.py "D:\Takeout-indirilenler"
```

Araç her fotoğrafın yanındaki `.json` dosyasından o fotoğrafta etiketli kişileri okur. Fotoğrafları 1600 px'e
küçültür, **konum (GPS) dahil tüm EXIF bilgisini siler** ve `data/media` altına yazar. Sonunda kişi başına
fotoğraf sayısını, ağaçta karşılığı olmayan etiketleri ve birden fazla kişiye uyan adları listeler.
Tekrar çalıştırmak güvenlidir: daha önce işlenen fotoğraflar atlanır. Videolar şimdilik alınmaz.

**İki Elmas Kaderoğlu:** Anneniz ve kızınız aynı adı taşıdığı için, Google Fotoğraflar'daki etiket adı
ikisinde de "Elmas Kaderoğlu" ise araç hangisi olduğunu bilemez ve o fotoğrafları atlar. Google Fotoğraflar'da
birinin etiketini farklılaştırın (ör. "Elmas Kaderoğlu (Kızım)") ve aynı adı `family.json`'da ilgili kişinin
`aliases` listesine ekleyin.

### Zaman çizelgesi ve "Birlikte"

- Ağaçta bir kişiye tıklayınca fotoğrafları Google Fotoğraflar gibi ay ay, en yeniden eskiye açılır.
  Sağdaki tarih çubuğu sürüklenerek istenen yıla/aya atlanır; aşağı inildikçe 10'ar fotoğraf yüklenir.
- "Birlikte" sekmesi: soldaki listeden kişilere çift tıklayın (veya sürükleyin, dokunmatikte "+"),
  sağda seçilen kişilerin **hepsinin** bulunduğu fotoğraflar aynı zaman çizelgesiyle listelenir.
  Bağlantı paylaşılabilir: `kaderoglu.com/#birlikte/kerim,muhammed` (kimlikler `family.json`'daki anahtarlar).

### Yüzlerde isimler ve profil fotoğrafı

- Tam ekran görüntüleyicide etiketli kişilerin yüzleri köşe çizgileriyle (hedef çerçevesi) işaretlenir, isimleri
  başlarının üstünde yazar; isme tıklayınca o kişinin fotoğrafları açılır. Sağ üstteki düğme isimleri gizler.
- Yüzleri bulup kişilerle eşleştirmek için (yeni içe aktarmadan sonra): `.venv\Scripts\python.exe tools\faces.py`
  (OpenCV YuNet + SFace, `models/` klasöründe; her şey bu bilgisayarda çalışır). Sadece yeniden eşleştirmek: `--match`.
- **Profil fotoğrafı:** sayfanın altındaki "Yönetici girişi" (panel hesabı) → bir fotoğraf aç → "Profil fotoğrafı yap"
  → kişiyi ve yüzü seç. Seçim `family.json`'a yazılır, sonraki içe aktarmalarda korunur.
- Google'da etiketi olmayan birini örnek bir fotoğraftaki yüzünden bulmak (önce `--dry-run --sheet kontrol.jpg` ile bakın):
  `.venv\Scripts\python.exe tools\find_person.py hatice ornek.jpg --pick bottom --threshold 0.47`
  `--pick`: örnekte birden çok yüz varsa hangisi (left/right/top/bottom/largest). Eşik düştükçe benzer ama
  farklı kişiler karışır; `--rounds` ile genişletme kayma yapabilir, varsayılan 1 tur.
- Profil fotoğrafını daha yakın kırpmak için `family.json`'da kişiye `"avatar_scale": 1.6` (varsayılan 2.6).
- Yanlış kişiye bağlanmış etiketleri taşımak: `.venv\Scripts\python.exe tools\relabel.py <eski> <yeni>`
  (+ `family.json`'da ilgili `aliases` düzeltmesi).

### Harita

"Harita" sekmesi konumu bilinen fotoğrafları OpenStreetMap üzerinde küçük fotoğraflı işaretçilerle gösterir;
yakın olanlar kümelenir, kişiye göre filtrelenir, işaretçiye tıklayınca ön izleme açılır. Kişi galerisindeki
"Haritada göster" düğmesi o kişinin fotoğraflarını haritada açar.

- Konum, Takeout'taki `geoData`'dan (yoksa fotoğrafın EXIF GPS'inden) alınır ve sadece `data/index.json`'da saklanır.
  Yayımlanan fotoğraf dosyalarında konum yoktur.
- Site koordinatları `config.json`'daki `map_precision` kadar yuvarlar: `2` ≈ 1 km (varsayılan; semt görünür,
  ev adresi görünmez), `3` ≈ 100 m, `4` ≈ 10 m. Değişiklik sunucuyu yeniden başlatınca geçerli olur.
- Daha önce içe aktarılmış fotoğrafların konumunu eklemek için içe aktarma aracını aynı arşivle yeniden çalıştırmak yeterli.

## Cloudflare

- DNS: `kaderoglu.com` ve `panel.kaderoglu.com` → ev bağlantısının sabit IP'si, Proxied. Google MX kayıtları ayrı, dokunulmadı.
- SSL/TLS: Full (strict), Always Use HTTPS, en az TLS 1.2. Origin sertifikası `certs/` altında (2041'e kadar).
- WAF: `/api/login` için IP başına 10 saniyede 5 istek sınırı.
- Fotoğraflar değişmeyen adlarla sunulur; Cloudflare önbelleğe alır, ev bağlantınız yorulmaz.
- Sitenin CSS/JS dosyaları `?v=<değişiklik zamanı>` ile çağrılır; dosya değişince Cloudflare önbelleğini temizlemeye gerek kalmaz.

## Güvenlik

- Sunucu sadece Cloudflare IP'lerinden (ve yerel ağdan) gelen bağlantıları kabul eder.
- Panel şifresi `config.json`'da bcrypt özeti olarak durur; değiştirmek için `.venv\Scripts\python.exe setup_user.py`.
- Aile sitesi arama motorlarına "dizine ekleme" der (`family_noindex`). Görünür olmasını isterseniz `config.json`'da `false` yapın.
- `certs/origin.key` dosyasını kimseyle paylaşmayın.
