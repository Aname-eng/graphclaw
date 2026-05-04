"""统一互联网访问工具 — 四级降级策略，自动突破人机验证.

策略:
  1. curl_cffi — TLS 指纹伪装 (Chrome 级别，Bing/Google 无法区分)
  2. requests — 标准 HTTP + 真实浏览器 Headers
  3. browser-use — Playwright 浏览器自动化
  4. chrome-devtools-mcp — 真实 Chrome DevTools 协议
"""

import json
import os
import random
import re
import subprocess
import sys
import time
import asyncio
from typing import Optional

from tools.registry import registry, tool_result, tool_error
from tools.utils import redact_sensitive_text

_last_search_time: float = 0
# 持久化 HTTP 会话（跨请求保持 cookies，模拟真实浏览行为）
_curl_session = None
_requests_session = None


def _ensure_search_delay():
    global _last_search_time
    now = time.time()
    elapsed = now - _last_search_time
    min_delay = 30 + random.randint(0, 10)
    if elapsed < min_delay:
        wait = min_delay - elapsed
        print(f"  ⏳ 搜索频率限制: 等待 {wait:.1f}s...")
        time.sleep(wait)
    _last_search_time = time.time()


def _extract_page_text(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        return "\n".join(lines[:200])
    except Exception:
        return html[:5000]


_CAPTCHA_KEYWORDS = [
    "captcha", "verify", "verification", "人机验证", "安全验证",
    "unusual traffic", "automated queries", "please confirm",
    "robot check", "prove you're human", "sorry",
]


def _is_captcha_page(text: str) -> bool:
    lower = text.lower()
    for kw in _CAPTCHA_KEYWORDS:
        if kw.lower() in lower:
            return True
    # 检测短页面 (验证码页面通常内容很少)
    if len(text.strip()) < 200 and ("verify" in lower or "security" in lower):
        return True
    return False


def _parse_bing_results(html: str) -> list:
    """从 Bing 搜索结果 HTML 中提取结果列表."""
    results = []
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        # Bing 结果容器
        for item in soup.select("li.b_algo, .b_algo"):
            title_el = item.select_one("h2 a, .b_title a")
            snippet_el = item.select_one(".b_caption p, .b_snippet")
            link_el = item.select_one("h2 a, .b_title a")
            title = title_el.get_text(strip=True) if title_el else ""
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            link = link_el.get("href", "") if link_el else ""
            if title:
                results.append({"title": title, "snippet": snippet, "link": link})
        # 备选: 通用链接
        if not results:
            for a in soup.select("a[href*='http']"):
                href = a.get("href", "")
                if any(d in href for d in ["bing.com", "microsoft.com"]):
                    continue
                txt = a.get_text(strip=True)
                if txt and len(txt) > 10:
                    results.append({"title": txt, "link": href})
    except Exception:
        pass
    return results[:20]


# =====================================================
# 第1级: curl_cffi — TLS 指纹伪装 (最优先)
# =====================================================

def _ensure_curl_session():
    """初始化或刷新 curl_cffi 持久化会话."""
    global _curl_session
    if _curl_session is None:
        from curl_cffi import requests as curl_req
        _curl_session = curl_req.Session(impersonate="chrome124")
        # 先访问首页建立会话
        _curl_session.get("https://www.bing.com/", timeout=10)
        time.sleep(2 + random.random() * 2)


def _curl_search(query: str) -> dict:
    """使用 curl_cffi + 持久化会话搜索，TLS 指纹伪装 Chrome."""
    try:
        _ensure_curl_session()
        url = f"https://www.bing.com/search?q={query.replace(' ', '+')}&count=20"
        resp = _curl_session.get(url, timeout=20)
        html = resp.text
        text = _extract_page_text(html)

        if _is_captcha_page(text):
            return {"success": False, "error": "curl_cffi 触发验证", "source": "curl"}

        results = _parse_bing_results(html)
        preview = f"找到 {len(results)} 条结果:\n"
        for r in results[:10]:
            preview += f"\n• {r['title']}"
            if r.get('snippet'):
                preview += f"\n  {r['snippet'][:150]}"
            if r.get('link'):
                preview += f"\n  {r['link']}"

        return {
            "success": True,
            "title": f"Bing: {query}",
            "url": resp.url,
            "text_preview": preview[:5000] if preview else text[:3000],
            "source": "curl_cffi",
        }
    except ImportError:
        return {"success": False, "error": "curl_cffi 未安装", "source": "curl"}
    except Exception as e:
        return {"success": False, "error": str(e), "source": "curl"}


# =====================================================
# 第2级: requests + 完整浏览器 Headers
# =====================================================

def _ensure_bing_session():
    """初始化或刷新 Bing 会话（模拟真实用户访问首页流程）."""
    global _requests_session
    if _requests_session is None:
        import requests
        _requests_session = requests.Session()
        # 设置持久 headers
        _requests_session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="125", "Microsoft Edge";v="125"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "Dnt": "1",
        })
        # 模拟真实用户: 先访问首页，等几秒，再搜索
        _requests_session.get("https://www.bing.com/", timeout=10)
        time.sleep(2 + random.random() * 2)


def _requests_search(query: str) -> dict:
    """使用持久化 requests 会话搜索（模拟真实用户浏览行为）. """
    try:
        import requests
        _ensure_bing_session()
        url = f"https://www.bing.com/search?q={query.replace(' ', '+')}&count=20"
        resp = _requests_session.get(url, timeout=20)
        html = resp.text
        text = _extract_page_text(html)

        if _is_captcha_page(text):
            return {"success": False, "error": "requests 触发验证", "source": "requests"}

        results = _parse_bing_results(html)
        preview = f"找到 {len(results)} 条结果:\n"
        for r in results[:10]:
            preview += f"\n• {r['title']}"
            if r.get('snippet'):
                preview += f"\n  {r['snippet'][:150]}"

        return {
            "success": True, "title": f"Bing: {query}",
            "url": resp.url, "text_preview": preview[:5000],
            "source": "requests",
        }
    except Exception as e:
        return {"success": False, "error": str(e), "source": "requests"}


# =====================================================
# 第3级: browser-use (Playwright)
# =====================================================

async def _bu_navigate(url: str) -> dict:
    try:
        from browser_use import Browser
        from playwright.async_api import async_playwright
        p = await async_playwright().start()
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)
        title = await page.title()
        content = await page.content()
        text = _extract_page_text(content)
        await browser.close()
        await p.stop()
        return {"success": True, "title": title, "url": page.url, "text_preview": text[:3000], "source": "browser-use"}
    except Exception as e:
        return {"success": False, "error": str(e), "source": "browser-use"}


def _run_async(coro) -> dict:
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# =====================================================
# 第4级: chrome-devtools-mcp (真实 Chrome)
# =====================================================

_MCP_PROCESS: Optional[subprocess.Popen] = None


def _mcp_navigate(url: str) -> dict:
    """通过 chrome-devtools-mcp 的 CLI 模式导航."""
    try:
        # 直接用 npx chrome-devtools-mcp --slim --headless 的 cli 模式
        result = subprocess.run(
            ["npx", "-y", "chrome-devtools-mcp@latest", "--slim", "--headless", "navigate", url],
            capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        output = (result.stdout or "") + (result.stderr or "")
        text = _extract_page_text(output)
        status = "success" if result.returncode == 0 else "error"
        return {
            "success": result.returncode == 0,
            "title": f"MCP: {url}",
            "url": url,
            "text_preview": text[:3000],
            "source": f"mcp-{status}",
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "MCP 超时", "source": "mcp"}
    except FileNotFoundError:
        return {"success": False, "error": "npx 未安装", "source": "mcp"}
    except Exception as e:
        return {"success": False, "error": str(e), "source": "mcp"}


# =====================================================
# 统一入口
# =====================================================

def browser_navigate(url: str, wait_for: Optional[str] = None,
                     headless: bool = False, task_id: str = "",
                     on_log: Optional[callable] = None) -> dict:
    """打开网页/搜索 — 自动选择最佳方式突破人机验证.

    四级降级策略:
    1. curl_cffi (TLS 指纹伪装) ← 搜索引擎专用，成功率最高
    2. requests (完整浏览器 Headers)
    3. browser-use (Playwright + 反自动化检测)
    4. chrome-devtools-mcp (真实 Chrome)
    """
    is_search = any(d in url.lower() for d in
                    ("bing.com/search", "google.com/search", "baidu.com/s"))

    if is_search:
        _ensure_search_delay()

    if on_log:
        on_log(f"[{task_id}] 🌐 {url}")

    # 提取搜索词
    query = ""
    if is_search and "q=" in url:
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(url)
        query = parse_qs(parsed.query).get("q", [""])[0]

    strategies = []

    # 搜索引擎: curl_cffi → requests → browser-use → mcp
    if is_search:
        strategies = [
            ("curl_cffi TLS 指纹伪装", lambda: _curl_search(query)),
            ("requests 浏览器 Headers", lambda: _requests_search(query)),
            ("browser-use Playwright", lambda: _run_async(_bu_navigate(url))),
            ("chrome-devtools-mcp", lambda: _mcp_navigate(url)),
        ]
    else:
        strategies = [
            ("browser-use Playwright", lambda: _run_async(_bu_navigate(url))),
            ("chrome-devtools-mcp", lambda: _mcp_navigate(url)),
        ]

    for name, fn in strategies:
        if on_log:
            on_log(f"[{task_id}] 尝试 {name}...")
        try:
            result = fn()
            if result.get("success"):
                text = result.get("text_preview", "")
                if text and len(text) > 100:
                    if on_log:
                        on_log(f"[{task_id}] ✅ {name} 成功 ({len(text)} chars)")
                    return tool_result(data={
                        "title": result.get("title", ""),
                        "url": result.get("url", url),
                        "content_preview": redact_sensitive_text(text),
                        "source": result.get("source", ""),
                    })
                elif _is_captcha_page(text):
                    if on_log:
                        on_log(f"[{task_id}] ⚠️ 触发验证，切下一级...")
                    continue
                else:
                    if on_log:
                        on_log(f"[{task_id}] ⚠️ 内容为空，切下一级...")
                    continue
            else:
                if on_log:
                    on_log(f"[{task_id}] ⚠️ 失败: {result.get('error', '')}")
        except Exception as e:
            if on_log:
                on_log(f"[{task_id}] ⚠️ 异常: {e}")
            continue

    return tool_error("BROWSER_ERROR", "所有方式均无法获取页面内容")


def register_all():
    registry.register(
        name="browser_navigate",
        handler=browser_navigate,
        description="打开网页或搜索引擎搜索。自动使用 TLS 指纹伪装突破人机验证，支持四级降级策略确保持续可用。",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL 或搜索查询"},
                "task_id": {"type": "string", "description": "任务追踪 ID"},
            },
            "required": ["url"],
        },
        toolset="browser",
        emoji="🌐",
    )


register_all()
