"""
HTTP client for NSE archive downloads.

NSE requires:
  1. A browser-like User-Agent and Referer header.
  2. A session cookie obtained by first visiting nseindia.com.

This module provides a seeded requests.Session with retry logic.
"""

import logging
import time
from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

_NSE_HOME = "https://www.nseindia.com"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": _NSE_HOME,
    "Connection": "keep-alive",
}


def build_session(
    max_retries: int = 3,
    backoff_factor: float = 5.0,
    connect_timeout: int = 30,
) -> Session:
    """
    Create a requests.Session pre-seeded with NSE cookies.

    The session will automatically retry on connection errors and 5xx responses,
    with exponential backoff.
    """
    retry = Retry(
        total=max_retries,
        backoff_factor=backoff_factor,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)

    session = Session()
    session.headers.update(_HEADERS)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    # Seed cookies — NSE blocks direct archive access without this
    try:
        resp = session.get(_NSE_HOME, timeout=(connect_timeout, 30))
        resp.raise_for_status()
        logger.debug("NSE session seeded OK (status %s)", resp.status_code)
    except Exception as exc:
        logger.warning("Could not seed NSE session: %s", exc)

    return session


def download_file(
    session: Session,
    url: str,
    dest_path,
    connect_timeout: int = 30,
    read_timeout: int = 120,
) -> bool:
    """
    Stream-download *url* to *dest_path*.

    Returns True on success, False if the server returned 403/404.
    Raises on other HTTP errors.
    """
    logger.info("Downloading %s", url)
    try:
        with session.get(url, timeout=(connect_timeout, read_timeout),
                         stream=True) as resp:
            if resp.status_code in (403, 404):
                logger.warning("URL not accessible (HTTP %s): %s", resp.status_code, url)
                return False
            resp.raise_for_status()

            with open(dest_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    fh.write(chunk)

        logger.info("Saved to %s", dest_path)
        return True

    except Exception as exc:
        logger.error("Download failed for %s: %s", url, exc)
        raise
