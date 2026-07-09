"""HTTP download adapter with rate-limit retry and progress reporting."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

import requests

log = logging.getLogger(__name__)


class HttpDownloadAdapter:
    """Downloads files via HTTP with retry logic and progress callbacks."""

    def __init__(
        self,
        max_retries: int = 5,
        backoff_base: float = 2.0,
        chunk_size: int = 65536,
    ) -> None:
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.chunk_size = chunk_size

    def download(
        self,
        url: str,
        destination: Path,
        on_progress: Callable[[float], None] | None = None,
        expected_size: int | None = None,
    ) -> Path:
        """Download *url* to *destination* with retry and progress.

        Streams to a ``.part`` temp file, then atomically renames on success.
        Calls *on_progress* with a fraction ``[0.0, 1.0]``, throttled to 1 %
        granularity.

        Raises :class:`RuntimeError` when retries are exhausted.
        """
        last_exception: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            part_path = destination.with_suffix(destination.suffix + ".part")
            try:
                response = requests.get(url, stream=True, timeout=30)

                # Explicitly handle rate-limiting since raise_for_status
                # may be mocked in tests
                if response.status_code == 429:
                    raise requests.exceptions.HTTPError(
                        "HTTP 429 Too Many Requests",
                        response=response,
                    )

                response.raise_for_status()

                content_length = response.headers.get("content-length")
                total = int(content_length) if content_length else 0
                downloaded = 0
                last_reported = -1.0

                part_path.parent.mkdir(parents=True, exist_ok=True)

                with part_path.open("wb") as f:
                    for chunk in response.iter_content(chunk_size=self.chunk_size):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)

                            if total and on_progress:
                                fraction = downloaded / total
                                # Throttle: report only when crossing 1 % boundaries
                                bucket = int(fraction * 100)
                                if bucket > last_reported:
                                    last_reported = bucket
                                    on_progress(fraction)

                if expected_size is not None and downloaded != expected_size:
                    raise RuntimeError(
                        f"Downloaded size {downloaded} does not match "
                        f"expected size {expected_size}"
                    )

                part_path.rename(destination)

                # Signal completion after rename
                if on_progress:
                    on_progress(1.0)

                return destination

            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                log.warning(
                    "HTTP %s downloading %s (attempt %d/%d)",
                    status,
                    url,
                    attempt,
                    self.max_retries,
                )
                last_exception = exc

                if attempt < self.max_retries:
                    sleep_time = (2 ** (attempt - 1)) * self.backoff_base
                    time.sleep(sleep_time)
            except requests.exceptions.RequestException as exc:
                log.warning(
                    "Request error downloading %s (attempt %d/%d): %s",
                    url,
                    attempt,
                    self.max_retries,
                    exc,
                )
                last_exception = exc

                if attempt < self.max_retries:
                    sleep_time = (2 ** (attempt - 1)) * self.backoff_base
                    time.sleep(sleep_time)
            except OSError as exc:
                log.warning(
                    "OS error downloading %s (attempt %d/%d): %s",
                    url,
                    attempt,
                    self.max_retries,
                    exc,
                )
                last_exception = exc

                if attempt < self.max_retries:
                    sleep_time = (2 ** (attempt - 1)) * self.backoff_base
                    time.sleep(sleep_time)
            finally:
                # Clean up partial .part files on retry
                if part_path.exists() and attempt < self.max_retries:
                    part_path.unlink(missing_ok=True)

        # All retries exhausted
        error_msg = f"Failed to download {url} after {self.max_retries} attempts"
        if isinstance(last_exception, requests.exceptions.HTTPError):
            status = (
                last_exception.response.status_code
                if last_exception.response is not None
                else 0
            )
            if status == 429:
                error_msg = (
                    f"Rate limited downloading {url} "
                    f"after {self.max_retries} retries"
                )
        raise RuntimeError(error_msg) from last_exception
