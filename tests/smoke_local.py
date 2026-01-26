#!/usr/bin/env python3
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime


def _url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + path


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = resp.read().decode("utf-8")
    return json.loads(data)


def _post_json(url: str, payload: dict) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read().decode("utf-8")
    return json.loads(data)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke test for ReviewBot WebApp backend (requires running server)."
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8081",
        help="Base URL of running server (default: http://127.0.0.1:8081)",
    )
    args = parser.parse_args()

    base_url = args.base_url
    print(f"Using base URL: {base_url}")

    try:
        # 1) Debug page should be reachable
        debug_url = _url(base_url, "/debug?debug=1")
        urllib.request.urlopen(debug_url, timeout=10).read()
        print("OK: /debug reachable")

        # 2) History endpoint should respond
        history_url = _url(base_url, "/api-debug/review?offset=0&limit=1")
        history = _get_json(history_url)
        if not isinstance(history, dict) or "items" not in history:
            print("FAIL: /api-debug/review invalid response")
            return 2
        print("OK: /api-debug/review responded")

        # 3) Preview endpoint should validate payload and return formatted output
        preview_url = _url(base_url, "/api-debug/preview")
        payload = {
            "type": "review",
            "createdAt": datetime.utcnow().isoformat(),
            "category": "service",
            "nameMode": "custom",
            "displayName": "Тест",
            "rating": 5,
            "likedMost": "Скорость",
            "dislikedMost": "",
            "text": "Тестовый отзыв для проверки preview.",
            "tgUser": None,
        }
        preview = _post_json(preview_url, payload)
        if not preview.get("ok"):
            print("FAIL: /api-debug/preview returned ok=false")
            print(preview)
            return 3
        print("OK: /api-debug/preview responded")
        print("Preview summary:")
        print(preview.get("summary", ""))
        return 0
    except urllib.error.HTTPError as e:
        print(f"HTTP error: {e.code} {e.reason}")
        try:
            print(e.read().decode("utf-8"))
        except Exception:
            pass
        return 1
    except Exception as e:
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
