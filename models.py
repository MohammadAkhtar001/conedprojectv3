"""
Fetch layer.

Centralizes HTTP behavior so per-source extractors stay focused on parsing.
All requests are logged into ExtractionAttempt records — successes AND
failures — so the orchestrator can report what was tried.

The fetcher does NOT decide whether to fall back; it just reports what
happened.  The no-fallback rule lives in the orchestrator.
"""

from __future__ import annotations
import logging
import os
import sys
import time
from threading import Lock
from typing import Optional
from urllib.parse import urlparse

import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential_jitter,
    retry_if_exception_type,
    RetryError,
)

from .models import ExtractionAttempt

log = logging.getLogger("fetcher")


# ── Per-host User-Agent policy ──────────────────────────────────────────────
# Some hosts (notably SEC) require a contact email or they 403 immediately.
# A few sites are friendlier to a browser-style UA than to "python-requests".
_DEFAULT_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_HOST_UA: dict[str, str] = {
    # SEC requires a contact email per their fair-access policy:
    # https://www.sec.gov/os/accessing-edgar-data
    "data.sec.gov": os.environ.get("SEC_USER_AGENT", "").strip()
        or "Utility Benchmark Pipeline contact@example.com",
    "www.sec.gov":  os.environ.get("SEC_USER_AGENT", "").strip()
        or "Utility Benchmark Pipeline contact@example.com",
}


def _ua_for(url: str) -> str:
    host = urlparse(url).hostname or ""
    return _HOST_UA.get(host, _DEFAULT_BROWSER_UA)


# ── Per-host rate limiting ──────────────────────────────────────────────────
# SEC asks for max 10 req/s; ProPublica is unspecified but courteous use
# suggests ~5 req/s.  We default to 4 req/s per host with a small jitter.
_HOST_LAST: dict[str, float] = {}
_HOST_MIN_INTERVAL: dict[str, float] = {
    "data.sec.gov": 0.10,
    "www.sec.gov":  0.10,
    "projects.propublica.org": 0.20,
}
_DEFAULT_INTERVAL = 0.25
_lock = Lock()


def _throttle(url: str):
    host = urlparse(url).hostname or ""
    interval = _HOST_MIN_INTERVAL.get(host, _DEFAULT_INTERVAL)
    with _lock:
        now = time.time()
        last = _HOST_LAST.get(host, 0.0)
        wait = (last + interval) - now
        if wait > 0:
            time.sleep(wait)
        _HOST_LAST[host] = time.time()


# ── Core fetch ──────────────────────────────────────────────────────────────


class FetchError(Exception):
    """Raised when fetch fails after all retries.  Carries the last
    ExtractionAttempt so the caller can attach it to a DataPoint."""

    def __init__(self, attempt: ExtractionAttempt, message: str):
        super().__init__(message)
        self.attempt = attempt


def _truncate(text: str, n: int = 500) -> str:
    if text is None:
        return ""
    return text if len(text) <= n else text[:n] + "…[truncated]"


# Retry on transient errors only — 5xx, connection errors, timeouts.
# Do NOT retry 403/404 — those are deterministic and retrying just wastes time.
class _RetryableHTTP(Exception):
    pass


def _http_get(url: str, *, timeout: int, extra_headers: Optional[dict]) -> requests.Response:
    headers = {
        "User-Agent": _ua_for(url),
        "Accept": "application/json, text/html, application/pdf;q=0.9, */*;q=0.5",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if extra_headers:
        headers.update(extra_headers)
    r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    if 500 <= r.status_code < 600:
        raise _RetryableHTTP(f"{r.status_code} {r.reason}")
    return r


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1.0, max=8.0),
    retry=retry_if_exception_type((_RetryableHTTP, requests.ConnectionError, requests.Timeout)),
    reraise=True,
)
def _http_get_with_retries(url: str, *, timeout: int, extra_headers: Optional[dict]):
    return _http_get(url, timeout=timeout, extra_headers=extra_headers)


def fetch(
    url: str,
    *,
    source: str,
    timeout: int = 20,
    extra_headers: Optional[dict] = None,
    expect: str = "any",          # "json" | "html" | "pdf" | "any"
) -> tuple[requests.Response, ExtractionAttempt]:
    """Fetch a URL, returning (response, ExtractionAttempt).

    Always returns an ExtractionAttempt — even on failure, via FetchError.
    """
    _throttle(url)
    started = time.time()
    log.info("GET %s  (source=%s)", url, source)

    try:
        r = _http_get_with_retries(url, timeout=timeout, extra_headers=extra_headers)
    except RetryError as e:
        cause = e.last_attempt.exception()
        attempt = ExtractionAttempt(
            source=source, url=url, method="GET",
            status_code=None, content_type=None, response_bytes=None,
            response_preview="", selectors_matched=None,
            duration_ms=int((time.time() - started) * 1000),
            success=False, error=f"retry exhausted: {cause}",
        )
        raise FetchError(attempt, f"retries exhausted: {cause}") from e
    except (requests.ConnectionError, requests.Timeout) as e:
        attempt = ExtractionAttempt(
            source=source, url=url, method="GET",
            status_code=None, content_type=None, response_bytes=None,
            response_preview="", selectors_matched=None,
            duration_ms=int((time.time() - started) * 1000),
            success=False, error=str(e),
        )
        raise FetchError(attempt, str(e)) from e

    duration = int((time.time() - started) * 1000)
    body_text = ""
    try:
        if expect == "pdf" or "application/pdf" in (r.headers.get("Content-Type", "")):
            body_text = f"[binary PDF: {len(r.content)} bytes]"
        else:
            body_text = r.text
    except Exception:
        body_text = ""

    attempt = ExtractionAttempt(
        source=source,
        url=url,
        method="GET",
        status_code=r.status_code,
        content_type=r.headers.get("Content-Type"),
        response_bytes=len(r.content) if r.content else 0,
        response_preview=_truncate(body_text, 500),
        selectors_matched=None,    # extractor will set this if applicable
        duration_ms=duration,
        success=(200 <= r.status_code < 300),
        error=None if 200 <= r.status_code < 300 else f"HTTP {r.status_code}",
    )

    log.info("  → %s %s (%d bytes, %dms)",
             r.status_code, r.headers.get("Content-Type", "?"), len(r.content), duration)

    if not attempt.success:
        raise FetchError(attempt, f"HTTP {r.status_code}")
    return r, attempt


# ── Optional: Playwright fallback for JS-rendered pages ─────────────────────
# Only invoked by extractors that have explicitly determined their target
# page is a SPA / requires JS to render.  We import lazily so the dependency
# is optional at runtime (Playwright pulls a 100MB+ browser binary).


def fetch_rendered_html(
    url: str,
    *,
    source: str,
    wait_selector: Optional[str] = None,
    timeout_ms: int = 15000,
) -> tuple[str, ExtractionAttempt]:
    """Render a page with headless Chromium and return its HTML.  This is
    the *only* anti-bot/JS escape hatch in the pipeline."""

    if os.environ.get("USE_PLAYWRIGHT", "1") != "1":
        attempt = ExtractionAttempt(
            source=source, url=url, method="playwright",
            status_code=None, content_type=None, response_bytes=None,
            response_preview="", selectors_matched=None, duration_ms=0,
            success=False, error="USE_PLAYWRIGHT=0; rendering disabled",
        )
        raise FetchError(attempt, "Playwright disabled")

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError as e:
        attempt = ExtractionAttempt(
            source=source, url=url, method="playwright",
            status_code=None, content_type=None, response_bytes=None,
            response_preview="", selectors_matched=None, duration_ms=0,
            success=False, error=f"playwright not installed: {e}",
        )
        raise FetchError(attempt, "playwright not installed") from e

    _throttle(url)
    started = time.time()
    log.info("PLAYWRIGHT %s  (source=%s)", url, source)

    def _do_render():
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=_DEFAULT_BROWSER_UA)
            page = context.new_page()
            try:
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                if wait_selector:
                    try:
                        page.wait_for_selector(wait_selector, timeout=timeout_ms)
                    except PWTimeout:
                        pass
                return page.content()
            finally:
                context.close()
                browser.close()

    try:
        html = _do_render()
    except Exception as e:
        # If the Chromium binary isn't installed yet (common on Streamlit
        # Cloud first boot), try to install it ONCE and retry.  This avoids
        # making users SSH in to run `playwright install`.
        msg = str(e)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            log.warning("Chromium binary missing; running 'playwright install chromium'…")
            try:
                import subprocess
                subprocess.run(
                    [sys.executable, "-m", "playwright", "install", "chromium"],
                    check=True, timeout=300,
                )
                log.info("Chromium installed; retrying render")
                html = _do_render()
            except Exception as e2:
                attempt = ExtractionAttempt(
                    source=source, url=url, method="playwright",
                    status_code=None, content_type=None, response_bytes=None,
                    response_preview="", selectors_matched=None,
                    duration_ms=int((time.time() - started) * 1000),
                    success=False, error=f"{e}; auto-install also failed: {e2}",
                )
                raise FetchError(attempt, str(e2)) from e2
        else:
            attempt = ExtractionAttempt(
                source=source, url=url, method="playwright",
                status_code=None, content_type=None, response_bytes=None,
                response_preview="", selectors_matched=None,
                duration_ms=int((time.time() - started) * 1000),
                success=False, error=msg,
            )
            raise FetchError(attempt, msg) from e

    duration = int((time.time() - started) * 1000)
    attempt = ExtractionAttempt(
        source=source, url=url, method="playwright",
        status_code=200, content_type="text/html (rendered)",
        response_bytes=len(html), response_preview=_truncate(html, 500),
        selectors_matched=None, duration_ms=duration, success=True,
    )
    log.info("  → rendered %d bytes in %dms", len(html), duration)
    return html, attempt
