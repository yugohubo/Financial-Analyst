import re
import urllib.parse
from typing import List, Dict, Any, Tuple
import httpx
from ddgs import DDGS
from database import save_log, get_setting
from scraper_integration import SCRAPER_SERVICE_AVAILABLE

class BridgeAgent:
    """
    Advanced Discovery & Bridge Agent.
    Executes a dual-layer (global + local) targeted search query strategy,
    utilizes a prestigious financial domain booster, applies heavy anti-junk sifting,
    and runs post-search keyword relevance density checks to block 'dirty' data.
    """
    def __init__(self) -> None:
        # 1. Advanced Exclusion Patterns: social media, clickbait, forums, e-commerce, spam
        self.exclude_patterns = [
            r"youtube\.com", r"youtu\.be", r"twitter\.com", r"x\.com", 
            r"facebook\.com", r"instagram\.com", r"pinterest\.com", 
            r"linkedin\.com", r"reddit\.com", r"\.pdf$", r"tiktok\.com",
            r"ads\.", r"doubleclick\.", r"amazon\.", r"ebay\.", r"aliexpress\.",
            r"trendyol\.", r"hepsiburada\.", r"quora\.com", r"medium\.com", 
            r"eksisozluk\.com", r"donanimhaber\.com", r"r10\.net", r"w10\.net",
            r"blogspot\.", r"wordpress\.", r"coupon", r"firsat", r"indirim"
        ]

        # 2. Junk keywords in titles/snippets to drop clickbait instantly
        self.junk_keywords = [
            "indirim", "kupon", "promosyon", "çekiliş", "cekilis", "hediye", 
            "ucuz", "kargo", "satın al", "shopping", "promo", "deal", "discount",
            "casino", "betting", "bahis", "oyun oyna", "izle", "porn", "sex"
        ]

        # 3. High-quality Financial Authorities (Global & Local) to boost in rank
        self.preferred_financial_sources = {
            # Global
            "reuters.com", "bloomberg.com", "ft.com", "cnbc.com", "wsj.com", 
            "investing.com", "marketwatch.com", "finance.yahoo.com", "forexlive.com",
            "seekingalpha.com", "economist.com", "cbdtracker",
            
            # Local (Turkey)
            "bloomberght.com", "bigpara.hurriyet.com.tr", "uzmanpara.milliyet.com.tr",
            "dunya.com", "tr.investing.com", "spglobal.com", "paraanaliz.com",
            "borsagundem.com", "ekonomim.com", "foreks.com"
        }

        # 4. Instrument specific keywords for post-scrape density checks
        self.relevance_keywords = {
            "USDTRY=X": ["dolar", "tcmb", "faiz", "tl", "lira", "enflasyon", "kur", "merkez bankası", "usd", "try"],
            "EURUSD=X": ["euro", "fed", "ecb", "inflation", "rate", "eur", "usd", "dollar", "lagarde", "powell"],
            "GC=F": ["gold", "altın", "ons", "xau", "fed", "inflation", "interest", "yield", "safe haven"],
            "BZ=F": ["oil", "petrol", "brent", "opec", "crude", "barrel", "supply", "energy", "demand"],
            "BTC-USD": ["bitcoin", "btc", "crypto", "kripto", "halving", "etf", "sec", "blockchain", "ethereum"],
            "^GSPC": ["s&p", "stock", "market", "earnings", "fed", "yield", "inflation", "nasdaq", "dow"],
            "XU100.IS": ["borsa", "bist", "xu100", "hisse", "tcmb", "enflasyon", "endeks", "istanbul", "faiz"],
            "NVDA": ["nvidia", "nvda", "gpu", "ai", "artificial intelligence", "chips", "earnings", "semiconductor", "nasdaq"],
            "^TNX": ["bond", "yield", "treasury", "t-bill", "fed", "interest", "10-year", "debt", "inflation"],
            "HG=F": ["copper", "bakır", "china", "metal", "industrial", "demand", "futures", "supply", "inventory"]
        }
        # Flag to enable extra content-based filtering (adds an HTTP request per candidate)
        # Flag to enable extra content-based filtering (adds an HTTP request per candidate)
        strict_val = get_setting("strict_mode", "false")
        self.strict_mode = str(strict_val).lower() == "true"
        # If the in‑process scraper is not available, force strict mode off to avoid accidental filtering
        if not SCRAPER_SERVICE_AVAILABLE:
            self.strict_mode = False
            save_log("Bridge Agent", "INFO", "Strict mode disabled automatically because scraper service is unavailable.")

    async def _ask_llm(self, prompt: str, model_name: str, response_format: str = "text", temperature: float = 0.2) -> str:
        """Helper to call Ollama/Gemini API depending on the selected model."""
        if model_name == "Google Gemini (Bulut)":
            api_key = get_setting("gemini_api_key", "")
            if not api_key:
                raise ValueError("Gemini API Key is missing.")
            
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": temperature}
            }
            if response_format == "json":
                payload["generationConfig"]["responseMimeType"] = "application/json"
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
                res = await client.post(url, json=payload, headers=headers)
                res.raise_for_status()
                return res.json()["candidates"][0]["content"]["parts"][0]["text"]
        else:
            # Local Ollama
            ollama_url = "http://127.0.0.1:11434/api/generate"
            payload = {
                "model": model_name,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": temperature}
            }
            if response_format == "json":
                payload["format"] = "json"
                
            async with httpx.AsyncClient(timeout=60.0) as client:
                res = await client.post(ollama_url, json=payload)
                res.raise_for_status()
                return res.json().get("response", "")

    async def get_queries(self, symbol: str, name: str, model_name: str) -> Dict[str, str]:
        """Generates a dual-layer query dictionary dynamically via LLM, falls back to deterministic if fails."""
        clean_symbol = symbol.replace("=X", "").replace("-USD", "").replace("^", "").replace(".IS", "").replace("=", "")
        
        # Default fallback
        global_q = f"{name} {clean_symbol} market price forecast analysis news"
        local_q = f"{name} {clean_symbol} piyasa fiyat tahmini analiz haberleri"
        
        if symbol == "USDTRY=X":
            global_q = "USD/TRY exchange rate turkey inflation central bank Fed"
            local_q = "dolar tl kuru son dakika tcmb faiz kararı enflasyon analiz"
        elif symbol == "EURUSD=X":
            global_q = "EUR/USD exchange rate forecast Fed ECB inflation rate"
            local_q = "euro dolar paritesi son dakika faiz kararı Fed ECB analiz"
        elif symbol == "GC=F":
            global_q = "gold price XAU/USD forecast Fed interest rates inflation yield"
            local_q = "ons altın fiyatı xauusd tahmin fed faiz kararı enflasyon analiz"
        elif symbol == "BZ=F":
            global_q = "brent crude oil price forecast OPEC supply cuts global demand"
            local_q = "brent petrol fiyatı tahmin opec üretim kesintisi petrol haberleri"
        elif symbol == "BTC-USD":
            global_q = "bitcoin price BTC USD forecast crypto regulation ETF inflows"
            local_q = "bitcoin fiyatı btc analiz kripto para düzenlemeleri etf haberleri"
        elif symbol == "^GSPC":
            global_q = "S&P 500 stock index earnings forecast Fed inflation rate"
            local_q = "sp 500 endeksi abd borsaları hisse kazanç raporları fed faiz"
        elif symbol == "XU100.IS":
            global_q = "Borsa Istanbul BIST 100 index foreign investors Turkey economy"
            local_q = "bist 100 borsa istanbul endeks analiz aracı kurum raporu tcmb"
        elif symbol == "NVDA":
            global_q = "NVIDIA NVDA stock earnings AI chip supply chain demand"
            local_q = "nvidia nvda hisse senedi yapay zeka çip bilançosu analist yorum"
        elif symbol == "^TNX":
            global_q = "US 10 year treasury yield bond market forecast Fed rate cut"
            local_q = "abd 10 yıllık tahvil faizleri tahvil piyasası fed faiz indirimi"
        elif symbol == "HG=F":
            global_q = "copper price futures global industrial demand China inventory"
        
        # Try LLM generation first
        try:
            prompt = f"""
Sen Wall Street'te 20 yıllık tecrübesi olan vizyoner ve son derece yaratıcı bir makroekonomik istihbarat analistisin.
Kullanıcının araştırdığı enstrüman: {name} ({symbol}).
Görevin, arama motorlarında bu enstrümanın fiyatını etkileyebilecek 'gizli' veya 'dolaylı' gelişmeleri bulabilmek için 2 adet harika arama sorgusu üretmek.
Sadece doğrudan hisse senedi veya borsa ismine odaklanma; çok daha YARATICI ol! Bu varlığı etkileyebilecek jeopolitik riskleri, küresel tedarik zinciri şoklarını, merkez bankası fısıltılarını, emtia krizlerini veya sıradışı makro olayları da kapsayan geniş vizyonlu sorgular tasarla.
Sadece aşağıdaki JSON formatında yanıt ver, başka hiçbir açıklama ekleme:
{{"global": "en iyi ve yaratıcı ingilizce arama sorgusu", "local": "en iyi ve yaratıcı türkçe arama sorgusu"}}
"""
            save_log("Bridge Agent", "INFO", f"Generating creative dynamic queries using {model_name}...")
            response = await self._ask_llm(prompt, model_name, response_format="json", temperature=0.7)
            import json
            queries = json.loads(response)
            if "global" in queries and "local" in queries:
                save_log("Bridge Agent", "INFO", f"Dynamic queries generated successfully.")
                return queries
        except Exception as e:
            save_log("Bridge Agent", "WARNING", f"LLM query generation failed ({str(e)}), falling back to deterministic.")

        return {"global": global_q, "local": local_q}

    def is_clean_candidate(self, url: str, title: str, snippet: str) -> bool:
        """Runs heavy pre-scrape sifting to block spam, clickbait and invalid URLs."""
        if not url or not url.startswith("http"):
            return False
            
        # 1. Check exclusion domains
        for pattern in self.exclude_patterns:
            if re.search(pattern, url, re.IGNORECASE):
                return False
                
        # 2. Check junk keywords in title or snippet
        combined_meta = f"{title} {snippet}".lower()
        for jk in self.junk_keywords:
            if jk in combined_meta:
                return False
                
        return True

    async def verify_content_relevance(self, symbol: str, name: str, text: str, model_name: str, source: str = "") -> Tuple[bool, str]:
        """
        Passes the scraped text to the local LLM to determine if it is genuinely about the queried symbol
        or macroeconomic data, rejecting generic clickbait, ad-filled pages, and SPA shells.
        """
        if "cbdtracker" in source.lower():
            save_log("Bridge Agent", "INFO", f"Bypassing AI filter for globally trusted database: {source}")
            return True, "Whitelist"

        if not text or len(text) < 150:
            save_log("Bridge Agent", "WARNING", f"Text too short ({len(text)} chars). Rejecting.")
            return False, "Metin okunamadı veya çok kısa"
            
        if not self.strict_mode:
            return True, "Strict Mode Kapalı"

        try:
            prompt = f"""
Aşağıdaki haber metnini analiz et. Bu metinde '{name} ({symbol})' fiyatlarını, piyasa beklentilerini veya bu enstrümana dolaylı yoldan etki edebilecek makroekonomik, jeopolitik veya sektörel bir gelişme anlatılıyor mu? 
Haberin kalitesi ve finansal ilgisi yüksekse 'TRUE', eğer ilgisiz bir içerikse 'FALSE' olarak sadece tek kelime dön.

Haber Metni:
{text[:2000]}
"""
            save_log("Bridge Agent", "INFO", f"Validating semantic relevance with {model_name}...")
            response = await self._ask_llm(prompt, model_name)
            is_relevant = "TRUE" in response.upper()
            
            if not is_relevant:
                save_log("Bridge Agent", "WARNING", f"LLM rejected scraped article content.")
                return False, "Yapay Zeka (LLM) tarafından yetersiz/alakasız bulundu"
            return True, "Onaylandı"
            
        except Exception as e:
            save_log("Bridge Agent", "WARNING", f"LLM relevance check failed ({str(e)}), using keyword fallback.")
            
        # Fallback keyword logic
        keywords = self.relevance_keywords.get(symbol, [])
        if not keywords:
            return True, "Anahtar kelime yedeği yok"
            
        text_lower = text.lower()
        has_kw = any(kw in text_lower for kw in keywords)
        if not has_kw:
            save_log("Bridge Agent", "WARNING", f"Rejected scraped article content (Relevance score too low).")
            return False, "Anahtar kelime eşleşmedi (Fallback)"
        return True, "Anahtar kelime eşleşti (Fallback)"

    async def discover_articles(self, symbol: str, name: str, model_name: str, max_results: int = 3) -> List[Dict[str, Any]]:
        """Discover high-fidelity, targeted financial articles.
        Applies pre-scrape junk filtering, optional content-based semantic check,
        and boosts preferred financial domains.
        """
        queries = await self.get_queries(symbol, name, model_name)
        discovered = []
        urls_seen = set()
        
        save_log("Bridge Agent", "INFO", f"Initiating Dual-Layer Search for {name} ({symbol})")
        
        try:
            with DDGS() as ddgs:
                for layer, query in queries.items():
                    # Fetch a larger pool of results to ensure we have enough backups
                    results = ddgs.text(query, max_results=max_results * 5, timelimit='m')
                    if not results:
                        continue
                    for r in results:
                        url = r.get("href", "")
                        title = r.get("title", "")
                        snippet = r.get("body", "")
                        
                        if url in urls_seen:
                            continue
                            
                        # Pre-scrape anti-junk sifting
                        if self.is_clean_candidate(url, title, snippet):
                            # Strict content verification is now deferred to main_gui.py after proper async scraping
                            if self.strict_mode and not SCRAPER_SERVICE_AVAILABLE:
                                save_log("Bridge Agent", "WARNING", "Strict mode enabled but scraper service unavailable; checking may be skipped.")
                                    
                            domain = urllib.parse.urlparse(url).netloc.lower()
                            is_preferred = any(pref in domain for pref in self.preferred_financial_sources)
                            boost_score = 10 if is_preferred else 0
                            
                            urls_seen.add(url)
                            discovered.append({
                                "url": url,
                                "title": title,
                                "snippet": snippet,
                                "source": domain,
                                "boost": boost_score,
                                "layer": layer,
                            })
                        else:
                            domain = urllib.parse.urlparse(url).netloc if url else "Bilinmeyen"
                            save_log("Bridge Agent", "DEBUG", f"Filtered junk candidate from domain: [{domain}]")
            
            # Sort by boost rank (preferred elite financial sources first)
            discovered.sort(key=lambda x: x["boost"], reverse=True)
            
            save_log("Bridge Agent", "INFO", f"Discovery complete. Found {len(discovered)} candidates, sending to scraper pool.")
            return discovered
            
        except Exception as e:
            save_log("Bridge Agent", "ERROR", f"Failed to execute search sequence for {symbol}: {str(e)}")
            return []

if __name__ == "__main__":
    # Test upgraded Bridge Agent
    agent = BridgeAgent()
    print("Testing Upgraded BridgeAgent...")
    res = agent.discover_articles("USDTRY=X", "USD/TRY", max_results=3)
    print(f"\nDiscovered {len(res)} Premium Candidates:")
    for r in res:
        print(f"- {r['title']} (Source: {r['source']}, Preferred: {r['boost'] > 0})")
