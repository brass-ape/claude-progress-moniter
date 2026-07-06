#!/usr/bin/env python3
"""Run this once to print the raw API response and diagnose parsing issues."""
import json
import sys
from pathlib import Path

import requests

CONFIG_PATH = Path(__file__).parent / "config.json"
DEFAULTS = {
    "credentials_path": "~/.claude/.credentials.json",
    "usage_url": "https://api.anthropic.com/api/oauth/usage",
    "request_timeout_seconds": 10,
}

config = dict(DEFAULTS)
if CONFIG_PATH.exists():
    config.update(json.loads(CONFIG_PATH.read_text()))

creds_path = Path(config["credentials_path"]).expanduser()
token = json.loads(creds_path.read_text())["claudeAiOauth"]["accessToken"]

resp = requests.get(
    config["usage_url"],
    headers={"Authorization": f"Bearer {token}"},
    timeout=int(config["request_timeout_seconds"]),
)
resp.raise_for_status()
data = resp.json()

print("=== Raw API response ===")
print(json.dumps(data, indent=2))
print()
print("=== Top-level keys ===")
print(list(data.keys()))
