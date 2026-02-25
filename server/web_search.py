"""Brave Search API 통합 — 웹 검색 + 딥 리서치."""
import os
import asyncio
import logging
import httpx

logger = logging.getLogger(__name__)

BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8888")


async def brave_search(query: str, count: int = 5) -> list[dict]:
    """Brave Search API로 웹 검색. 결과: [{title, url, snippet}]"""
    if not BRAVE_API_KEY:
        logger.info("Brave API key not configured, using SearXNG")
        return await searxng_search(query, count)

    # 입력 검증
    if not query.strip() or len(query) > 500:
        logger.warning("Invalid search query length: %d", len(query))
        return []
    
    count = min(max(count, 1), 20)  # 1-20 범위로 제한

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                BRAVE_SEARCH_URL,
                params={
                    "q": query.strip()[:500],  # 쿼리 길이 제한
                    "count": count,
                    "search_lang": "ko",
                    "country": "KR",
                    "safesearch": "moderate"
                },
                headers={
                    "X-Subscription-Token": BRAVE_API_KEY, 
                    "Accept": "application/json",
                    "User-Agent": "Claude-Web-Gateway/1.0"
                },
            )
            resp.raise_for_status()
            data = resp.json()
            
            results = []
            for item in (data.get("web", {}).get("results", []))[:count]:
                # URL 검증
                url = item.get("url", "")
                if not url.startswith(("http://", "https://")):
                    continue
                    
                results.append({
                    "title": item.get("title", "")[:200],  # 제목 길이 제한
                    "url": url,
                    "snippet": item.get("description", "")[:500],  # 스니펫 길이 제한
                })
            
            logger.info("Brave search success: %d results for query '%s'", len(results), query[:50])
            return results
            
    except httpx.HTTPStatusError as e:
        logger.warning("Brave Search HTTP error %d, fallback to SearXNG: %s", e.response.status_code, e)
    except httpx.RequestError as e:
        logger.warning("Brave Search request error, fallback to SearXNG: %s", e)
    except Exception as e:
        logger.error("Brave Search unexpected error, fallback to SearXNG: %s", e)
        
    return await searxng_search(query, count)


async def searxng_search(query: str, count: int = 5) -> list[dict]:
    """SearXNG 로컬 인스턴스 검색 (fallback)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{SEARXNG_URL}/search",
                params={"q": query, "format": "json", "pageno": 1},
            )
            resp.raise_for_status()
            data = resp.json()
            results = []
            for item in (data.get("results", []))[:count]:
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", ""),
                })
            return results
    except Exception as e:
        logger.error("SearXNG 검색 실패: %s", e)
        return []


def format_search_results(results: list[dict]) -> str:
    """검색 결과를 Claude에 주입할 텍스트로 포맷."""
    if not results:
        return ""
    lines = ["[웹 검색 결과]"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        lines.append(f"   URL: {r['url']}")
        if r['snippet']:
            lines.append(f"   {r['snippet']}")
    return "\n".join(lines)


async def fetch_page_text(url: str, max_chars: int = 5000) -> str:
    """URL에서 텍스트 추출 — Scrapling 사용 (안티봇 우회 + 구조화 파싱)."""
    try:
        from scrapling.fetchers import Fetcher
        page = await asyncio.to_thread(
            Fetcher.get, url, timeout=15, stealthy_headers=True
        )
        # 구조화된 텍스트 추출 (script/style 자동 제외)
        text = page.get_all_text(separator=' ', strip=True) if hasattr(page, 'get_all_text') else ""
        if not text:
            # fallback: raw text
            text = page.text if hasattr(page, 'text') else ""
        import re
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:max_chars]
    except Exception as e:
        logger.warning("Scrapling fetch 실패 %s: %s, httpx fallback", url, e)
        # fallback to httpx
        return await _fetch_page_httpx(url, max_chars)


async def _fetch_page_httpx(url: str, max_chars: int = 5000) -> str:
    """httpx fallback — Scrapling 실패 시 사용."""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            text = resp.text
            import re
            text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            return text[:max_chars]
    except Exception as e:
        logger.warning("httpx fallback도 실패 %s: %s", url, e)
        return ""


async def deep_research(query: str, claude_fn=None) -> str:
    """딥 리서치: 검색 → 상위 페이지 읽기 → 종합 컨텍스트 생성.

    Returns context string to prepend to user message.
    """
    # 1차 검색
    results = await brave_search(query, count=8)
    if not results:
        return "[딥 리서치: 검색 결과를 찾을 수 없습니다]"

    # 상위 3-5개 페이지 내용 수집
    tasks = [fetch_page_text(r["url"], max_chars=4000) for r in results[:5]]
    pages = await asyncio.gather(*tasks)

    # 컨텍스트 구성
    sections = ["[딥 리서치 결과]", f"검색어: {query}", ""]
    for i, (r, page_text) in enumerate(zip(results[:5], pages), 1):
        sections.append(f"--- 출처 {i}: {r['title']} ---")
        sections.append(f"URL: {r['url']}")
        if page_text:
            sections.append(page_text[:3000])
        elif r['snippet']:
            sections.append(r['snippet'])
        sections.append("")

    # 추가 검색 결과 요약
    if len(results) > 5:
        sections.append("--- 추가 검색 결과 ---")
        for r in results[5:]:
            sections.append(f"• {r['title']} ({r['url']}): {r['snippet']}")

    context = "\n".join(sections)
    # 전체 컨텍스트 제한 (20K chars)
    if len(context) > 20000:
        context = context[:20000] + "\n... (잘림)"

    return context
