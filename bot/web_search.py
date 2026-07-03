"""Люба Web Search — DuckDuckGo + SearXNG + Yandex + article fetch."""
import asyncio, logging, re
from typing import List, Dict
from urllib.parse import quote_plus
import httpx
from bot.config import config

logger = logging.getLogger("nastya.web_search")

class SearchResult:
    def __init__(self, title, url, snippet="", source=""):
        self.title, self.url, self.snippet, self.source = title, url, snippet, source

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36", "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"}
_search_client: httpx.AsyncClient | None = None

async def _get_client():
    global _search_client
    if _search_client is None or _search_client.is_closed:
        _search_client = httpx.AsyncClient(timeout=httpx.Timeout(config.SEARCH_TIMEOUT_SECONDS, connect=8.0), limits=httpx.Limits(max_connections=20, max_keepalive_connections=10), follow_redirects=True, headers=_HEADERS)
    return _search_client

def _clean_html(s):
    s = re.sub(r"<[^>]+>", "", s)
    for old, new in [("&amp;","&"),("&nbsp;"," "),("&quot;",'"'),("&#39;","'"),("&lt;","<"),("&gt;",">")]:
        s = s.replace(old, new)
    return re.sub(r"\s+", " ", s).strip()

async def search_ddg_html(query, max_results=5):
    results = []
    try:
        client = await _get_client()
        resp = await client.get("https://html.duckduckgo.com/html/", params={"q": query, "kl": "ru-ru", "no_redirect": "1"})
        if resp.status_code == 202:
            resp = await client.get("https://lite.duckduckgo.com/lite/", params={"q": query, "kl": "ru-ru"})
            if resp.status_code != 200: return results
            urls = re.findall(r'<a[^>]+class="result-link"[^>]+href="([^"]+)"', resp.text)
            titles = re.findall(r'<a[^>]+class="result-link"[^>]*>(.*?)</a>', resp.text, re.DOTALL)
            snippets = re.findall(r'<td[^>]+class="result-snippet"[^>]*>(.*?)</td>', resp.text, re.DOTALL)
            for i, url in enumerate(urls[:max_results]):
                results.append(SearchResult(_clean_html(titles[i]) if i < len(titles) else "", url, _clean_html(snippets[i]) if i < len(snippets) else "", "ddg_lite"))
            return results
        if resp.status_code != 200: return results
        blocks = re.findall(r'<a rel="nofollow" class="result__a" href="([^"]+?)".*?>(.*?)</a>.*?<a class="result__snippet".*?>(.*?)</a>', resp.text, re.DOTALL)
        for url, title, snippet in blocks[:max_results]:
            results.append(SearchResult(_clean_html(title), url, _clean_html(snippet), "ddg"))
    except Exception as e:
        logger.debug(f"DDG error: {e}")
    return results

async def search_searxng(query, max_results=5):
    results = []
    for instance in ["https://searx.be/search", "https://search.sapti.me/search"]:
        try:
            client = await _get_client()
            resp = await client.get(instance, params={"q": query, "format": "html"})
            if resp.status_code != 200: continue
            urls = re.findall(r'<h[34]>.*?<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', resp.text, re.DOTALL)
            for url, title in urls[:max_results]:
                title = _clean_html(title)
                if url and title and "searx" not in url:
                    results.append(SearchResult(title=title, url=url, source="searxng"))
            if results: break
        except Exception: pass
    return results

async def web_search(query, max_results=5):
    results = await search_ddg_html(query, max_results)
    if not results: results = await search_searxng(query, max_results)
    seen, unique = set(), []
    for r in results:
        if r.url not in seen: seen.add(r.url); unique.append(r)
    return unique[:max_results]

def format_search_results(results, max_items=3):
    if not results: return ""
    lines = []
    for r in results[:max_items]:
        snippet = f" — {r.snippet}" if r.snippet else ""
        lines.append(f"• {r.title}{snippet}\n  {r.url}")
    return "\n".join(lines)

async def fetch_article(url, max_chars=1500):
    try:
        client = await _get_client()
        r = await client.get(url)
        if r.status_code != 200: return ""
        html = r.text
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<nav[^>]*>.*?</nav>", "", html, flags=re.DOTALL | re.IGNORECASE)
        article = re.search(r"<article[^>]*>(.*?)</article>", html, re.DOTALL | re.IGNORECASE)
        if article: html = article.group(1)
        paras = re.findall(r"<p[^>]*>(.*?)</p>", html, re.DOTALL | re.IGNORECASE)
        text = "\n".join(_clean_html(p) for p in (paras or [html]))
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text).strip()) if len(s.strip()) > 30]
        return " ".join(sentences)[:max_chars]
    except Exception: return ""

async def research_topic(topic, max_queries=2):
    topic = (topic or "").strip()
    if len(topic) < 5: return ""
    queries = [topic[:300]]
    short = topic[:60].split("—")[0].split(":")[0].strip()
    if short and short.lower() != topic[:60].lower(): queries.append(short)
    async def _q(q):
        try: return await asyncio.wait_for(web_search(q, max_results=5), timeout=8.0)
        except: return []
    search_results_lists = await asyncio.gather(*[_q(q) for q in queries[:max_queries]])
    seen, all_results = set(), []
    for lst in search_results_lists:
        for r in lst:
            if r.url not in seen: seen.add(r.url); all_results.append(r)
    if not all_results: return ""
    top = all_results[:2]
    articles = await asyncio.gather(*[fetch_article(r.url, 1200) for r in top])
    lines = [f"Развёрнутые результаты веб-поиска по теме «{topic[:80]}»:"]
    for i, r in enumerate(all_results[:4]):
        snippet = f" — {r.snippet}" if r.snippet else ""
        lines.append(f"\n[{i+1}] {r.title}{snippet}\n    {r.url}")
    for i, (r, content) in enumerate(zip(top, articles)):
        if content: lines.append(f"\nСодержание статьи [{i+1}] ({r.title[:60]}):\n{content}")
    lines.append("\nИспользуй эти данные чтобы РАЗВЁРНУТО дополнить ответ: приведи конкретные факты, цифры, даты, контекст. Упомяни источник ссылкой.")
    return "\n".join(lines)

async def verify_claim(claim, fast=True):
    timeout = 5.0 if fast else config.SEARCH_TIMEOUT_SECONDS
    try:
        results = await asyncio.wait_for(web_search(claim, max_results=3), timeout=timeout)
    except: return ""
    if not results: return ""
    return format_search_results(results, max_items=2)

def first_url(context):
    m = re.search(r"https?://\S+", context or "")
    return m.group(0) if m else ""

def all_urls(context):
    return re.findall(r"https?://\S+", context or "")
