from __future__ import annotations

import re
import threading
import urllib.request
from PyQt6.QtCore import QThread, pyqtSignal


def _fetch_og(url: str, timeout: int = 4) -> dict:
    """
    Fetch Open Graph metadata from a URL.
    Returns dict with: og_title, og_description, og_image, site_name, favicon_url.
    """
    result: dict[str, str] = {}
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(20_000).decode("utf-8", errors="replace")

        def _meta(prop: str) -> str:
            for pattern in [
                rf'<meta[^>]+property=["\']og:{prop}["\'][^>]+content=["\']([^"\']+)["\']',
                rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:{prop}["\']',
                rf'<meta[^>]+name=["\']og:{prop}["\'][^>]+content=["\']([^"\']+)["\']',
            ]:
                m = re.search(pattern, raw, re.IGNORECASE)
                if m:
                    return m.group(1).strip()
            return ""

        def _meta_name(name: str) -> str:
            m = re.search(
                rf'<meta[^>]+name=["\']{name}["\'][^>]+content=["\']([^"\']+)["\']',
                raw, re.IGNORECASE
            )
            return m.group(1).strip() if m else ""

        result["og_title"]       = _meta("title") or _meta_name("title") or ""
        result["og_description"] = _meta("description") or _meta_name("description") or ""
        result["og_image"]       = _meta("image") or ""
        result["site_name"]      = _meta("site_name") or ""

        # Favicon: prefer <link rel="icon"> or use Google favicon API as fallback
        icon_match = re.search(
            r'<link[^>]+rel=["\'](?:shortcut )?icon["\'][^>]+href=["\']([^"\']+)["\']',
            raw, re.IGNORECASE
        )
        if icon_match:
            href = icon_match.group(1)
            if href.startswith("http"):
                result["favicon_url"] = href
            elif href.startswith("//"):
                result["favicon_url"] = "https:" + href
            else:
                from urllib.parse import urlparse, urljoin
                result["favicon_url"] = urljoin(url, href)
        else:
            from urllib.parse import urlparse
            p = urlparse(url)
            result["favicon_url"] = f"https://www.google.com/s2/favicons?domain={p.netloc}&sz=64"

    except Exception:
        pass

    return result


class OGWorker(QThread):
    """
    Background thread: fetches Open Graph metadata for a URL.
    Emits og_result(data_dict, original_query) on success,
    no_result(original_query) on failure / empty.
    """
    og_result = pyqtSignal(dict, str)
    no_result = pyqtSignal(str)

    def __init__(self, url: str, query: str, parent=None):
        super().__init__(parent)
        self.url = url
        self.query = query
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        if self._cancelled:
            return
        data = _fetch_og(self.url)
        if self._cancelled:
            return
        if data.get("og_title") or data.get("og_image"):
            data["source_url"] = self.url
            self.og_result.emit(data, self.query)
        else:
            self.no_result.emit(self.query)
