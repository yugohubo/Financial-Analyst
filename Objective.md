# Objective: Otonom Finansal Analist ve Tahmin Sistemi (Masaüstü GUI)

## 1. Projenin Amacı ve Özeti
Bu projenin amacı; dünya genelindeki sosyo-politik ve ekonomik haberleri dinamik olarak keşfeden, bu haberleri yerelde çalışan yüksek hızlı bir kazıyıcı (Scraper API) vasıtasıyla toplayan, makroekonomik veriler ile finansal grafikleri bir ekonomist gözüyle analiz eden ve nihai analizi, tahminleri (forecasting) ve korelasyonları tamamen yerel (HTML/Web tabanlı olmayan) bir masaüstü arayüzünde (GUI) sunan otonom bir "Finansal Analist Ajan Sistemi" inşa etmektir.

---

## 2. Takip Edilecek Sabit Varlık Listesi (10 Seçilmiş Enstrüman)
Sistem, aşağıda belirtilen 10 farklı küresel ve yerel varlığı ana odağına alacak ve araştırmalarını bu enstrümanlar etrafında yoğunlaştıracaktır:
1. **Döviz Çifti (Forex):** USD/TRY (Amerikan Doları / Türk Lirası)
2. **Döviz Çifti (Forex):** EUR/USD (Euro / Amerikan Doları)
3. **Emtia (Commodity):** XAU/USD (Ons Altın)
4. **Emtia (Commodity):** Brent Crude Oil (Brent Petrol)
5. **Kripto Para:** BTC/USD (Bitcoin)
6. **Küresel Endeks:** S&P 500 (GSPC)
7. **Yerel Endeks:** BIST 100 (XU100)
8. **Teknoloji Hissesi:** NVDA (NVIDIA Corporation)
9. **Makro Gösterge:** US 10Y Bond (ABD 10 Yıllık Tahvil Faizleri)
10. **Değerli Metal / Endüstriyel Emtia:** COPPER (Bakır Vadeli İşlemleri)

---

## 3. Sistem Mimarisi ve Modüller

### A. Arama, Keşif ve Köprü Ajanı (Bridge Agent)
- **Görev:** Belirlenen 10 varlık ile ilgili internetteki en güncel siyasi ve ekonomik haber kaynaklarını, makaleleri ve analiz sitelerini otonom olarak araştırır.
- **Çalışma Prensibi:** Doğrusal bir arama yerine, DuckDuckGo veya benzeri açık kaynaklı arama araçlarını kullanarak ilgili varlığa ait anahtar kelimeleri (örneğin: *"Fed faiz kararı etkileri"*, *"OPEC petrol üretimi kesintisi"*) tarar, çıkan sonuçlardaki URL'lerin kalitesini ve uygunluğunu yerel LLM (Ollama) üzerinden süzerek doğrular. Uygun bulunan hedef URL listesini kazıyıcı modüle paslar.

### B. Yüksek Hızlı Veri Çekme Hattı (Fast Scraper Integration) ( U modül elimizde var workspace içinde.)
- **Görev:** Köprü ajandan gelen temiz URL'lerdeki ham içeriği hızla kazır.
- **Çalışma Prensibi:** Yerelde çalışan, asenkron ve yüksek performanslı FastAPI tabanlı Scraper API yapısına `POST` istekleri atarak web sayfalarının metinsel içeriklerini, meta verilerini temiz bir JSON formatında sisteme geri döndürür. Anti-bot mekanizmalarına karşı korumalı ve hız limitlerine duyarlı çalışmalıdır.

### C. Ekonomist Analiz ve Tahmin Motoru (Quantitative & LLM Analysis)
- **Görev:** Finansal grafikleri (fiyat hareketleri, hareketli ortalamalar, volatilite) nümerik olarak inceler ve kazınan haber metinleriyle korelasyon kurarak gelecek tahmini yapar.
- **Çalışma Prensibi:**
  - **Grafik/Teknik Analiz:** Geleneksel finans kütüphaneleriyle verileri işler (Trend analizi, RSI, MACD veya istatistiksel modeller).
  - **Haber / Korelasyon Analizi:** Toplanan sosyo-politik haberlerin duyarlılık (sentiment) analizini yapar. LLM'e bir makroekonomist personası tanımlanarak; *"X haberi, Y varlığının grafiğindeki şu kırılmayı nasıl tetiklemiş olabilir ve önümüzdeki 7 günde ne yönde bir forecasting öngörülüyor?"* sorusuna yanıt arar.

### D. Yerel Masaüstü Kullanıcı Arayüzü (Native Desktop GUI)
- **Görev:** Tüm bu süreçleri, analizleri, canlı haber akışlarını ve tahmin grafiklerini kullanıcıya sunar.
- **KRİTİK KISITLAMA:** Kesinlikle HTML, CSS, JavaScript, Electron, Tauri veya Webview tabanlı teknolojiler kullanılmayacaktır. Arayüz tamamen masaüstü yerel (native OS canvas) kütüphaneleriyle çizilecektir.
- **İçerik:** 10 varlığın listelendiği yan panel, seçilen varlığın teknik/istatistiksel grafiği, ajanların o an arka planda yaptığı araştırmaları gösteren canlı "Düşünce ve Log Akışı", makroekonomik tahmin raporu ekranı.

---

## 4. Kullanılacak Teknolojiler, Araçlar ve Kütüphaneler

- **Masaüstü GUI:** `PyQt6` veya `PySide6` (Gelişmiş Qt widget mimarisi için) ya da `CustomTkinter` (Modern ve hafif bir native görünüm için).
- **Finansal Veri Sağlayıcı:** `yfinance` veya `Alpha Vantage` (Geçmiş ve anlık ham piyasa verilerini çekmek için).
- **Veri Analitiği & İstatistik:** `pandas`, `numpy`, `statsmodels` (Zaman serisi analizi ve forecasting için).
- **Grafik Çizim (GUI Entegre):** `matplotlib` (Qt/Tkinter canvas içerisine gömülerek native grafik çizimi sağlamak için).
- **Arama & Keşif:** `duckduckgo_search` (Ücretsiz ve API key gerektirmeyen otonom arama döngüleri için).
- **Ajan Yönetimi & Durum Makinesi:** `LangGraph` veya saf Python ile yazılmış `State Pattern` (Ajanın döngüsel kararlar alabilmesi, hata durumunda aramayı tekrarlayabilmesi için).
- **Yerel Zeka Motoru:** `Ollama` (Gelişmiş finansal çıkarımlar ve akıl yürütme döngüleri için `llama3`, `mistral` veya muadili yerel modeller).
- **Veri Saklama (Hafıza):** `sqlite3` (Yapısal veriler, enstrüman fiyatları ve loglar için) + `ChromaDB` (Haberlerin anlamsal analizi ve RAG için yerel vektör veri tabanı).

---

## 5. Proje Kısıtlamaları ve Katı Kurallar (Constraints)

1. **Web Teknolojileri Yasağı:** Projenin görsel katmanında hiçbir şekilde tarayıcı motoru (Chromium vb.), `cefpython`, `html/css render` kütüphaneleri yer alamaz. UI tamamen masaüstü işletim sisteminin yerel pencereleriyle kararlı şekilde çalışmalıdır.
2. **Çevrimdışı / Yerel Öncelik (Local-First):** Finansal veri çekme ve web aramaları dışındaki tüm analiz, çıkarım ve veritabanı süreçleri yerel makinede (Ollama & SQLite/ChromaDB) dönmelidir. Üçüncü parti paralı LLM API'lerine bağımlılık minimumda tutulmalıdır.
3. **Modülerlik ve Asenkron Yapı:** Ajanın internette araştırma yapması veya veri kazıması GUI'yi dondurmamalıdır. Tüm veri çekme ve analiz süreçleri `asyncio` veya `QThread` (eğer PyQt seçilirse) mimarisiyle arka planda yürütülmeli, arayüze sinyallerle aktarılmalıdır.
4. **Log Şeffaflığı:** Ajanın o an hangi sitede araştırma yaptığı, hangi linki "not worth my time" diyerek elediği, hangi haberi önemli bulduğu arayüzdeki log ekranında anlık olarak akmalıdır.

---

## 6. Adım Adım Geliştirme Yol Haritası (Agentic IDE İçin Talimatlar)

### Faz 1: Altyapı ve Veri Katmanının Kurulması
- Seçilen 10 varlık için `yfinance` entegrasyonunu tamamla.
- SQLite veritabanı şemasını oluştur (Fiyat geçmişi, Haber kayıtları, Ajan logları).
- Yerel FastAPI Scraper API'ye bağlanacak istemci (client) fonksiyonlarını yaz.

### Faz 2: Köprü Ajanı ve Keşif Döngüsü (LangGraph / State-Machine)
- Arama motorundan gelen sonuçları analiz eden, filtreleyen ve temiz URL listesi çıkaran otonom döngüyü kurgula.
- Kazınan haber içeriklerini ChromaDB üzerinde vektörleştirerek anlamsal RAG havuzunu hazırla.

### Faz 3: Analiz ve Ekonomist Tahmin Motoru
- Geleneksel zaman serisi tahmin modelleri ile LLM'den gelen makro duyarlılık raporunu birleştiren hibrid bir analiz fonksiyonu geliştir.
- Analiz sonuçlarını yapısal JSON formatında çıktı vermeye zorla.

### Faz 4: Native GUI Tasarımı ve Entegrasyon
- Belirlenen GUI kütüphanesi ile pencereleri, listeleri ve log akış ekranını inşa et.
- `matplotlib` grafiklerini GUI içerisine göm ve asenkron sinyal yapısıyla arka plandaki veri değişimlerini ekrana yansıt.