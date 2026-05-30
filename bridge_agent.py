import re
import urllib.parse
from typing import List, Dict, Any
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
            "seekingalpha.com", "economist.com",
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

    def get_queries(self, symbol: str, name: str) -> Dict[str, str]:
        """Generates a dual-layer query dictionary: 'global' and 'local'."""
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
            local_q = "bakır fiyatı vadeli işlemler çin sanayi talebi bakır haberleri"
            
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

    def verify_content_relevance(self, symbol: str, text: str) -> bool:
        """
        Runs post-scrape keyword relevance density checks.
        Ensures the article is actually related to the requested instrument.
        """
        if not text or len(text) < 150:
            return False
            
        keywords = self.relevance_keywords.get(symbol, [])
        if not keywords:
            return True  # Fallback for custom user added symbols
            
        text_lower = text.lower()
        match_count = 0
        
        # Count unique keywords matched in the scraped body text
        for kw in keywords:
            if kw in text_lower:
                match_count += 1
                
        # Must contain at least 2 distinct target keywords to be considered relevant
        # Rejects generic 'noise' articles, clickbaits, and SPA empty loads
        is_relevant = match_count >= 2
        
        if not is_relevant:
            save_log("Bridge Agent", "WARNING", f"Rejected scraped article content (Relevance score too low: {match_count}/{len(keywords)} matches).")
        
        return is_relevant

    def discover_articles(self, symbol: str, name: str, max_results: int = 3) -> List[Dict[str, Any]]:
        """Discover high-fidelity, targeted financial articles.
        Applies pre-scrape junk filtering, optional content-based relevance check,
        and boosts preferred financial domains.
        """
        queries = self.get_queries(symbol, name)
        discovered = []
        urls_seen = set()
        
        save_log("Bridge Agent", "INFO", f"Initiating Dual-Layer Search for {name} ({symbol})")
        
        try:
            with DDGS() as ddgs:
                for layer, query in queries.items():
                    save_log("Bridge Agent", "INFO", f"Searching [{layer.upper()} Layer] using query: '{query}'")
                    results = ddgs.text(query, max_results=max_results * 3)
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
                            # Optional strict content verification
                            if self.strict_mode and SCRAPER_SERVICE_AVAILABLE:
                                try:
                                    scraped = run_scrape_sync(url)
                                    content = scraped.get("content", "")
                                    if not self.verify_content_relevance(symbol, content):
                                        save_log("Bridge Agent", "DEBUG", f"Discarded low-relevance article after scrape: {url}")
                                        continue
                                except Exception as e:
                                    save_log("Bridge Agent", "WARNING", f"Scrape failed during discovery for {url}: {str(e)}")
                                    continue
                            elif self.strict_mode and not SCRAPER_SERVICE_AVAILABLE:
                                save_log("Bridge Agent", "WARNING", "Strict mode enabled but scraper service unavailable; skipping strict verification.")
                                    
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
            final_selection = discovered[:max_results]
            
            for f in final_selection:
                tag = "⭐ [PRESTİJLİ]" if f["boost"] > 0 else "[GENEL]"
                save_log("Bridge Agent", "INFO", f"Accepted {tag} ({f['layer'].upper()}): {f['title'][:45]}... on [{f['source']}]")
                
            save_log("Bridge Agent", "INFO", f"Discovery stream finished. Found {len(final_selection)} premium articles.")
            return final_selection
            
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
