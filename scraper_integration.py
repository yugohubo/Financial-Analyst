import sys
import os
import asyncio
import httpx
import re
import datetime
from typing import Dict, Any
from database import save_log

# Append the Blazing Fast Article Scraper path to Python's system paths
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SCRAPER_PATH = os.path.join(BASE_DIR, "Blazing Fast Article Scraper")
if SCRAPER_PATH not in sys.path:
    sys.path.insert(0, SCRAPER_PATH)

try:
    from app.services.scraper_service import ScraperService
    SCRAPER_SERVICE_AVAILABLE = True
except ImportError as e:
    print(f"Warning: Could not import ScraperService directly: {str(e)}")
    SCRAPER_SERVICE_AVAILABLE = False

# ---------- Browser-Impersonation Client (primp) ----------

try:
    import primp
    PRIMP_AVAILABLE = True
except ImportError:
    PRIMP_AVAILABLE = False

# ---------- Lightweight Fallback Scraper (BeautifulSoup) ----------

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,tr;q=0.8",
}


def _extract_meta(soup, names):
    """Return the first matching meta content for a list of property/name candidates."""
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or \
              soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def _extract_article_text(soup) -> str:
    """Extract the main article body text using multiple heuristic strategies."""
    # Strategy 1: <article> tag
    article = soup.find("article")
    if article:
        paragraphs = article.find_all("p")
        text = "\n".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 30)
        if len(text) > 200:
            return text

    # Strategy 2: Common article container classes/ids
    for selector in [
        {"class_": re.compile(r"article[-_]?(body|content|text)", re.I)},
        {"class_": re.compile(r"story[-_]?(body|content)", re.I)},
        {"class_": re.compile(r"post[-_]?(body|content)", re.I)},
        {"id": re.compile(r"article[-_]?(body|content)", re.I)},
        {"class_": "caas-body"},          # Yahoo Finance
        {"class_": "paywall"},            # Some news sites
        {"class_": re.compile(r"^body$", re.I)},
    ]:
        container = soup.find("div", selector)
        if container:
            paragraphs = container.find_all("p")
            text = "\n".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 30)
            if len(text) > 200:
                return text

    # Strategy 3: Collect all <p> tags from <main> or <body>, filter noise
    main = soup.find("main") or soup.find("body")
    if main:
        paragraphs = main.find_all("p")
        # Filter short paragraphs (navigation, footers, etc.)
        good = [p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 50]
        if good:
            return "\n".join(good)

    return ""


def _parse_article(html: str, url: str) -> Dict[str, Any]:
    """Parse raw HTML into a structured article dict."""
    soup = BeautifulSoup(html, "lxml")

    # Title
    title = _extract_meta(soup, ["og:title", "twitter:title"]) or \
            (soup.title.string.strip() if soup.title and soup.title.string else "Başlık bulunamadı")

    # Author
    author = _extract_meta(soup, ["author", "article:author", "og:author"]) or "Bilinmeyen"

    # Publication date
    pub_date = _extract_meta(soup, [
        "article:published_time", "datePublished", "date",
        "og:article:published_time", "pubdate"
    ])
    if not pub_date:
        time_tag = soup.find("time", attrs={"datetime": True})
        if time_tag:
            pub_date = time_tag["datetime"]
    if not pub_date:
        pub_date = datetime.date.today().strftime("%Y-%m-%d")
    else:
        # Normalise to YYYY-MM-DD
        pub_date = pub_date[:10]

    # Content
    content = _extract_article_text(soup)

    return {
        "title": title,
        "author": author,
        "publication_date": pub_date,
        "content": content,
        "url": url,
    }


def _fetch_with_primp(url: str) -> str:
    """
    Fetch HTML using primp (browser TLS fingerprint impersonation).
    Bypasses most 401/403 anti-bot protections.
    Returns raw HTML string or raises an exception.
    """
    client = primp.Client(
        impersonate="random",
        follow_redirects=True,
        timeout=15,
    )
    resp = client.get(url)
    if resp.status_code >= 400:
        raise RuntimeError(f"primp returned HTTP {resp.status_code} for {url}")
    return resp.text


async def _fetch_with_httpx(url: str, client: httpx.AsyncClient) -> str:
    """
    Fetch HTML using httpx with realistic headers.
    Fallback when primp is not installed or fails.
    """
    response = await client.get(url, headers=_HEADERS, follow_redirects=True, timeout=12.0)
    response.raise_for_status()
    return response.text


# ---------- Main InProcessScraper ----------

class InProcessScraper:
    """
    Multi-layered article scraper with three fallback tiers:
      1. Blazing Fast Article Scraper (premium, if installed)
      2. primp + BeautifulSoup (browser TLS impersonation, bypasses most anti-bot)
      3. httpx + BeautifulSoup (lightweight, works for unprotected sites)
    """
    def __init__(self) -> None:
        if SCRAPER_SERVICE_AVAILABLE:
            self.service = ScraperService()
            save_log("Scraper", "INFO", "Premium ScraperService loaded successfully.")
        else:
            self.service = None

        capabilities = []
        if PRIMP_AVAILABLE:
            capabilities.append("primp (browser impersonation)")
        if BS4_AVAILABLE:
            capabilities.append("BeautifulSoup (lightweight)")
        if not capabilities and not self.service:
            save_log("Scraper", "ERROR",
                     "No scraper backend available. Install beautifulsoup4 + lxml.")
        else:
            save_log("Scraper", "INFO",
                     f"Scraper initialised — fallback chain: {', '.join(capabilities)}")

    async def scrape_url(self, url: str) -> Dict[str, Any]:
        """
        Scrapes article title, author, publication date, and content from a URL.
        Tries each available backend in order until one succeeds.
        """
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        async with httpx.AsyncClient(limits=limits) as client:

            # ── Tier 1: Premium Blazing Fast Scraper ──
            if self.service:
                try:
                    save_log("Scraper", "INFO", f"[Tier 1 — Premium] Scraping: {url}")
                    article_data = await self.service.scrape(url, client)
                    save_log("Scraper", "INFO",
                             f"[Tier 1] OK: {article_data.get('title', '?')[:40]}...")
                    return article_data
                except Exception as e:
                    save_log("Scraper", "WARNING",
                             f"[Tier 1] Premium scrape failed: {str(e)} — escalating...")

            # ── Tier 2: primp (browser impersonation) + BeautifulSoup ──
            if PRIMP_AVAILABLE and BS4_AVAILABLE:
                try:
                    save_log("Scraper", "INFO", f"[Tier 2 — primp] Scraping: {url}")
                    html = _fetch_with_primp(url)
                    article_data = _parse_article(html, url)
                    content_len = len(article_data.get("content", ""))
                    if content_len > 100:
                        save_log("Scraper", "INFO",
                                 f"[Tier 2] OK: {article_data.get('title', '?')[:40]}... "
                                 f"({content_len} chars)")
                        return article_data
                    else:
                        save_log("Scraper", "WARNING",
                                 f"[Tier 2] Extracted only {content_len} chars — escalating...")
                except Exception as e:
                    save_log("Scraper", "WARNING",
                             f"[Tier 2] primp scrape failed: {str(e)} — escalating...")

            # ── Tier 3: httpx + BeautifulSoup (lightweight fallback) ──
            if BS4_AVAILABLE:
                try:
                    save_log("Scraper", "INFO", f"[Tier 3 — httpx] Scraping: {url}")
                    html = await _fetch_with_httpx(url, client)
                    article_data = _parse_article(html, url)
                    content_len = len(article_data.get("content", ""))
                    save_log("Scraper", "INFO",
                             f"[Tier 3] OK: {article_data.get('title', '?')[:40]}... "
                             f"({content_len} chars)")
                    return article_data
                except Exception as e:
                    save_log("Scraper", "ERROR",
                             f"[Tier 3] httpx scrape failed: {str(e)}")
                    raise

            raise RuntimeError("No scraper backend available (install beautifulsoup4 + lxml).")


def run_scrape_sync(url: str) -> Dict[str, Any]:
    """
    Synchronous entry point that runs the async scrape in a fresh event loop.
    Useful inside QThread workers.
    """
    scraper = InProcessScraper()
    return asyncio.run(scraper.scrape_url(url))


if __name__ == "__main__":
    test_urls = [
        "https://www.reuters.com/markets/commodities/",
        "https://www.cnbc.com/2025/05/26/stock-market-today-live-updates.html",
        "https://www.investing.com/news/stock-market-news/",
    ]
    for url in test_urls:
        print(f"\n{'='*60}")
        print(f"Testing: {url}")
        try:
            res = run_scrape_sync(url)
            print(f"  Title:   {res.get('title', '?')[:60]}")
            print(f"  Author:  {res.get('author', '?')}")
            print(f"  Date:    {res.get('publication_date', '?')}")
            print(f"  Content: {len(res.get('content', ''))} chars")
            print(f"  Snippet: {res.get('content', '')[:150]}...")
        except Exception as err:
            print(f"  FAILED: {err}")
