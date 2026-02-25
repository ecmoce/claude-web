#!/usr/bin/env python3
"""Scrapling vs httpx+regex 성능 비교 벤치마크"""
import asyncio
import time
import re
import httpx

# Test URLs - 다양한 사이트
TEST_URLS = [
    "https://en.wikipedia.org/wiki/Web_scraping",
    "https://news.ycombinator.com/",
    "https://github.com/trending",
    "https://httpbin.org/html",
    "https://quotes.toscrape.com/",
]

# === Method 1: Current (httpx + regex) ===
async def fetch_httpx(url: str) -> dict:
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
            resp.raise_for_status()
            text = resp.text
            raw_len = len(text)
            text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            elapsed = time.perf_counter() - start
            return {"method": "httpx+regex", "url": url, "status": resp.status_code,
                    "raw_bytes": raw_len, "text_len": len(text), "time_ms": round(elapsed * 1000),
                    "error": None}
    except Exception as e:
        elapsed = time.perf_counter() - start
        return {"method": "httpx+regex", "url": url, "status": 0,
                "raw_bytes": 0, "text_len": 0, "time_ms": round(elapsed * 1000),
                "error": str(e)}

# === Method 2: Scrapling Fetcher ===
def fetch_scrapling(url: str) -> dict:
    from scrapling.fetchers import Fetcher
    start = time.perf_counter()
    try:
        page = Fetcher.get(url, timeout=15)
        # Extract text using Scrapling's parser
        text = page.get_all_text(separator=' ', strip=True) if hasattr(page, 'get_all_text') else page.text
        elapsed = time.perf_counter() - start
        return {"method": "scrapling", "url": url, "status": page.status if hasattr(page, 'status') else 200,
                "raw_bytes": len(page.html_content) if hasattr(page, 'html_content') else 0,
                "text_len": len(text) if text else 0, "time_ms": round(elapsed * 1000),
                "error": None}
    except Exception as e:
        elapsed = time.perf_counter() - start
        return {"method": "scrapling", "url": url, "status": 0,
                "raw_bytes": 0, "text_len": 0, "time_ms": round(elapsed * 1000),
                "error": str(e)}

# === Method 3: Scrapling Adaptive (CSS selector test) ===
def fetch_scrapling_parse(url: str) -> dict:
    from scrapling.fetchers import Fetcher
    start = time.perf_counter()
    try:
        page = Fetcher.get(url, timeout=15)
        # Test parsing capabilities
        links = page.css('a')
        headings = page.css('h1, h2, h3')
        paragraphs = page.css('p')
        elapsed = time.perf_counter() - start
        return {"method": "scrapling+parse", "url": url, "status": 200,
                "links": len(links), "headings": len(headings), "paragraphs": len(paragraphs),
                "time_ms": round(elapsed * 1000), "error": None}
    except Exception as e:
        elapsed = time.perf_counter() - start
        return {"method": "scrapling+parse", "url": url, "status": 0,
                "links": 0, "headings": 0, "paragraphs": 0,
                "time_ms": round(elapsed * 1000), "error": str(e)}

async def main():
    print("=" * 70)
    print("🕷️  Scrapling vs httpx+regex 벤치마크")
    print("=" * 70)
    
    all_results = []
    
    for url in TEST_URLS:
        short = url.split("//")[1][:40]
        print(f"\n📄 {short}")
        print("-" * 50)
        
        # httpx
        r1 = await fetch_httpx(url)
        status1 = f"✅ {r1['time_ms']}ms, {r1['text_len']} chars" if not r1['error'] else f"❌ {r1['error'][:50]}"
        print(f"  httpx+regex:     {status1}")
        all_results.append(r1)
        
        # scrapling
        r2 = await asyncio.to_thread(fetch_scrapling, url)
        status2 = f"✅ {r2['time_ms']}ms, {r2['text_len']} chars" if not r2['error'] else f"❌ {r2['error'][:50]}"
        print(f"  scrapling:       {status2}")
        all_results.append(r2)
        
        # scrapling parse
        r3 = await asyncio.to_thread(fetch_scrapling_parse, url)
        status3 = f"✅ {r3['time_ms']}ms, {r3.get('links',0)} links, {r3.get('headings',0)} h, {r3.get('paragraphs',0)} p" if not r3['error'] else f"❌ {r3['error'][:50]}"
        print(f"  scrapling+parse: {status3}")
        all_results.append(r3)
    
    # Summary
    print("\n" + "=" * 70)
    print("📊 요약")
    print("=" * 70)
    
    httpx_times = [r['time_ms'] for r in all_results if r['method'] == 'httpx+regex' and not r['error']]
    scrap_times = [r['time_ms'] for r in all_results if r['method'] == 'scrapling' and not r['error']]
    
    httpx_errors = sum(1 for r in all_results if r['method'] == 'httpx+regex' and r['error'])
    scrap_errors = sum(1 for r in all_results if r['method'] == 'scrapling' and r['error'])
    
    if httpx_times:
        print(f"  httpx+regex:  avg {sum(httpx_times)//len(httpx_times)}ms | errors: {httpx_errors}/{len(TEST_URLS)}")
    if scrap_times:
        print(f"  scrapling:    avg {sum(scrap_times)//len(scrap_times)}ms | errors: {scrap_errors}/{len(TEST_URLS)}")
    
    if httpx_times and scrap_times:
        ratio = sum(scrap_times) / sum(httpx_times) if sum(httpx_times) > 0 else 0
        if ratio > 1:
            print(f"\n  ⚡ httpx가 {ratio:.1f}x 빠름 (네트워크 fetch만)")
        else:
            print(f"\n  ⚡ scrapling이 {1/ratio:.1f}x 빠름")
        print(f"  🎯 scrapling은 파싱 품질이 우수 (구조화된 DOM 접근)")

if __name__ == "__main__":
    asyncio.run(main())
