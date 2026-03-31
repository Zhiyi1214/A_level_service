"""Cursor debug session NDJSON sink; remove after verified fix."""
from __future__ import annotations

import json
import os
import time
from typing import Any

# 与 Cursor 工作区一致：项目根/.cursor/debug-b64b75.log（本机即为 /Users/.../A_level/.cursor/...）
_ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(_ROOT, ".cursor", "debug-b64b75.log")
SESSION_ID = "b64b75"


def write(hypothesis_id: str, location: str, message: str, data: dict[str, Any] | None = None) -> None:
    payload = {
        "sessionId": SESSION_ID,
        "timestamp": int(time.time() * 1000),
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data or {},
    }
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
