# Financial Analyst Projesi Mimarisi ve İşleyişi

Bu doküman, **Financial Analyst** masaüstü uygulamasının temel bileşenlerini, veri akışını ve modüller arası etkileşimi şematize ederek açıklamaktadır.

## 🏗️ Sistem Mimarisi Şeması

```mermaid
graph TD
    UI[🖥️ PyQt6 Arayüzü] --> |Araştırma Başlat| RW[⚙️ Research Worker (Arka Plan)]
    
    subgraph 1. Sayısal Analiz ve Tahmin
        RW --> AE1[📊 Analysis Engine]
        AE1 --> |Fiyat Verisi| YF[(Yahoo Finance)]
        AE1 --> |Hesaplama| TI[Teknik Göstergeler\nSMA, RSI, MACD vb.]
        AE1 --> |İstatistiksel Model| ARIMA[📈 ARIMA Gelecek Tahmini]
    end
    
    subgraph 2. İnternet Taraması ve Keşif
        RW --> BA[🔎 Bridge Agent]
        BA --> |Dual-Layer Sorgu| DDG[DuckDuckGo Arama]
        BA --> |Ön Filtreleme| Junk[Spam ve Clickbait Filtresi]
    end
    
    subgraph 3. İçerik Çıkarma (3 Katmanlı Scraper)
        Junk --> SI[🕸️ Scraper Integration]
        SI --> |Tier 1| Premium[Blazing Fast Scraper Servisi]
        SI --> |Tier 2| Primp[Primp: Tarayıcı Taklidi]
        SI --> |Tier 3| BS4[HTTPX + BeautifulSoup]
    end
    
    subgraph 4. Veritabanı ve RAG (Vektör Arama)
        SI --> DB[(🗄️ SQLite Veritabanı)]
        DB --> VS[🧠 Lightweight Vector Store]
        VS --> |TF-IDF & Cosine Similarity| RAG[İlgili Haber Bağlamı]
    end
    
    subgraph 5. Yapay Zeka Sentezi
        TI --> Sentez
        ARIMA --> Sentez
        RAG --> Sentez
        Sentez --> |Prompt| LLM{Ollama / Gemini Cloud}
        LLM --> |Makroekonomik Rapor| UI
    end
    
    classDef default fill:#1e1e24,stroke:#2c2c35,stroke-width:2px,color:#f8f8f2;
    classDef highlight fill:#2979ff,stroke:#2962ff,stroke-width:2px,color:#fff;
    classDef ai fill:#00e676,stroke:#00c853,stroke-width:2px,color:#000;
    
    class UI highlight;
    class LLM ai;
```

## 🧩 Temel Bileşenler (Modüller)

### 1. `main_gui.py` (Kullanıcı Arayüzü ve İş Parçacıkları)
Uygulamanın kalbidir. Kullanıcı ile etkileşime geçer, verileri modern ve karanlık temalı bir GUI üzerinde gösterir.
- **`ResearchWorker`:** Arayüzün donmaması için tüm ağır veri çekme, kazıma ve yapay zeka işlemlerini arka planda asenkron olarak yürütür.
- Dinamik grafikler (Matplotlib & Cyberpunk tema) ve rapor görselleştirmeleri burada oluşturulur.

### 2. `analysis_engine.py` (Analiz ve LLM Motoru)
Kantitatif verilerle kalitatif (metin) verilerin birleştiği yerdir.
- **Veri Çekme:** `yfinance` üzerinden 90 günlük geçmiş fiyat verisini çeker.
- **Matematiksel Modeller:** SMA, RSI, MACD ve volatilite hesaplar; `statsmodels` kütüphanesiyle geleceğe yönelik 7 günlük ARIMA tahmini yapar.
- **Yapay Zeka Promptu:** Tüm verileri ve RAG bağlamını bir araya getirip, Ollama (Yerel) veya Gemini (Bulut) modeline "Makroekonomist" kimliğiyle kapsamlı bir prompt gönderir.

### 3. `bridge_agent.py` (Köprü ve Keşif Ajanı)
Modelin kör noktası olan "güncel haberleri" internetten bulmakla görevlidir.
- DuckDuckGo (`ddgs`) kullanarak ilgili finansal enstrüman için Global ve Lokal (Türkçe/İngilizce) sorgular atar.
- Clickbait siteleri, spam kelimeleri eler ve Bloomberg, Reuters, Investing gibi prestijli finansal kaynaklara öncelik (boost) verir.

### 4. `scraper_integration.py` (Kazıyıcı Entegrasyonu)
Bulunan haber bağlantılarının içindeki gerçek makale metnini çıkarır. Bot korumalarını aşmak için 3 katmanlı zırhlı bir yapı kullanır:
- **Tier 1:** Varsa hızlı ve premium harici servis çalıştırılır.
- **Tier 2 (`primp`):** Reuters vb. sitelerin Cloudflare/TLS bot korumalarını, Chrome tarayıcı parmak izini taklit ederek aşar.
- **Tier 3 (`httpx+bs4`):** Basit ve hızlı bir şekilde standart web sayfalarından metinleri ayıklar.

### 5. `vector_store.py` (Vektör Veritabanı ve RAG)
Kaydedilen binlerce haber arasından, yapay zekaya sadece "o anki bağlam için en alakalı" olanları seçip verir (Retrieval-Augmented Generation). 
- Ağır vektör veritabanları yerine Scikit-Learn tabanlı hafif bir TF-IDF modeli kullanır.

### 6. `database.py` (Veri Yönetimi)
SQLite tabanlı yerel ve sunucusuz veritabanı işlemlerini yönetir. Ayarlar, işlem günlükleri (log), geçmiş raporlar ve fiyat geçmişi tek bir dosyada tutulur.
