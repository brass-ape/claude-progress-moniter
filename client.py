from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests


class ClaudeUsageClient:
    def __init__(self, credentials_path: str, usage_url: str, timeout_seconds: int) -> None:
        self.credentials_path = Path(credentials_path).expanduser()
        self.usage_url = usage_url
        self.timeout_seconds = timeout_seconds
        self._cached_token: str | None = None

    def load_token(self, force: bool = False) -> str:
        if self._cached_token is None or force:
            data = json.loads(self.credentials_path.read_text())
            self._cached_token = data["claudeAiOauth"]["accessToken"]
        return self._cached_token

    def fetch_usage(self) -> tuple[dict[str, Any], int]:
        token = self.load_token()
        headers = {"Authorization": f"Bearer {token}"}
        started = time.monotonic()
        try:
            response = requests.get(self.usage_url, headers=headers, timeout=self.timeout_seconds)
            response.raise_for_status()
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (401, 403):
                # Token may have been refreshed on disk — reload and retry once
                token = self.load_token(force=True)
                headers = {"Authorization": f"Bearer {token}"}
                started = time.monotonic()
                response = requests.get(self.usage_url, headers=headers, timeout=self.timeout_seconds)
                response.raise_for_status()
            else:
                raise
        latency_ms = int((time.monotonic() - started) * 1000)
        return response.json(), latency_ms
