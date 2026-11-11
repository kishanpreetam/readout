"""A polite HTTP client: rate limited, declared User-Agent, retries, and a manifest of every file saved.

Downloads are treated as untrusted data. They are size capped, stored gzip compressed under data/raw,
and only ever parsed as JSON.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests

from . import config


class AccessRefused(Exception):
    """The server refused the request (HTTP 401 or 403). Callers stop instead of retrying."""

    def __init__(self, url: str, status: int, body_excerpt: str):
        super().__init__(f"{status} from {url}")
        self.url = url
        self.status = status
        self.body_excerpt = body_excerpt


class Fetcher:
    def __init__(self, user_agent: str, min_interval_s: float, manifest: Path = config.MANIFEST):
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": user_agent, "Accept": "application/json", "Accept-Encoding": "gzip, deflate"}
        )
        self.min_interval_s = min_interval_s
        self.manifest = manifest
        self._last = 0.0
        self.requests_made = 0

    def _wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.min_interval_s:
            time.sleep(self.min_interval_s - gap)
        self._last = time.monotonic()

    def get_json(self, url: str, params: dict | None = None, dest: Path | None = None, retries: int = 4):
        """GET a JSON document, save it gzip compressed at `dest` (if given) and return the parsed JSON."""
        full_url = f"{url}?{urlencode(params)}" if params else url
        for attempt in range(retries + 1):
            self._wait()
            self.requests_made += 1
            try:
                resp = self.session.get(url, params=params, timeout=60, stream=True)
                body = self._read_capped(resp)
            except requests.RequestException:
                if attempt == retries:
                    raise
                time.sleep(2 ** attempt)
                continue
            if resp.status_code in (401, 403):
                raise AccessRefused(full_url, resp.status_code, body[:300].decode("utf-8", "replace"))
            if resp.status_code == 404:
                return None
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(max(2 ** attempt, float(resp.headers.get("Retry-After", 0) or 0)))
                continue
            resp.raise_for_status()
            data = json.loads(body)
            if dest is not None:
                self._save(full_url, body, dest, resp)
            return data
        raise RuntimeError(f"gave up on {full_url}")

    @staticmethod
    def _read_capped(resp: requests.Response) -> bytes:
        chunks, total = [], 0
        for chunk in resp.iter_content(chunk_size=1 << 16):
            total += len(chunk)
            if total > config.MAX_RESPONSE_BYTES:
                resp.close()
                raise ValueError(f"response from {resp.url} is over the size cap")
            chunks.append(chunk)
        return b"".join(chunks)

    def _save(self, full_url: str, body: bytes, dest: Path, resp: requests.Response) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with gzip.open(tmp, "wb", compresslevel=6) as fh:
            fh.write(body)
        tmp.replace(dest)
        record = {
            "url": full_url,
            "path": str(dest.relative_to(config.ROOT)),
            "bytes": len(body),
            "bytes_stored_gz": dest.stat().st_size,
            "wire_bytes": int(resp.headers["Content-Length"]) if resp.headers.get("Content-Length") else None,
            "sha256": hashlib.sha256(body).hexdigest(),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": resp.status_code,
        }
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a") as fh:
            fh.write(json.dumps(record) + "\n")


def read_gz_json(path: Path):
    with gzip.open(path, "rb") as fh:
        return json.loads(fh.read())
