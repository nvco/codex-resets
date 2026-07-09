#!/usr/bin/env python3
import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ENDPOINT = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"


def main():
    args = parse_args()
    color_enabled = should_use_color(args)

    try:
        payload = load_payload(args)
        resets = normalize_resets(payload)
    except CodexResetsError as exc:
        print(f"codex-resets: {exc}", file=sys.stderr)
        return 1

    print(format_resets(resets, datetime.now(timezone.utc), color_enabled))
    return 0


def parse_args():
    parser = argparse.ArgumentParser(
        description="Show available banked Codex rate-limit resets."
    )
    parser.add_argument(
        "--auth",
        default=str(Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json"),
        help="Path to Codex auth.json. Defaults to $CODEX_HOME/auth.json or ~/.codex/auth.json.",
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("CODEX_RESETS_ENDPOINT", DEFAULT_ENDPOINT),
        help="Rate-limit reset credits endpoint.",
    )
    parser.add_argument(
        "--file",
        help="Read reset JSON from a file instead of calling the endpoint.",
    )
    parser.add_argument(
        "--color",
        action="store_true",
        help="Force ANSI color output.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color output.",
    )
    return parser.parse_args()


def should_use_color(args):
    if args.no_color:
        return False
    if args.color:
        return True
    if os.environ.get("NO_COLOR") or os.environ.get("CODEX_RESETS_COLOR") == "never":
        return False
    if os.environ.get("CODEX_RESETS_COLOR") == "always":
        return True
    return sys.stdout.isatty()


def load_payload(args):
    if args.file:
        return read_json(Path(args.file))
    return fetch_reset_credits(Path(args.auth), args.endpoint)


def read_json(path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise CodexResetsError(f"file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CodexResetsError(f"invalid JSON in {path}: {exc}") from exc


def fetch_reset_credits(auth_path, endpoint):
    auth = read_json(auth_path)
    tokens = auth.get("tokens") or {}
    access_token = tokens.get("access_token")
    account_id = tokens.get("account_id")

    if not access_token:
        raise CodexResetsError(f"access token not found in {auth_path}")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "originator": "Codex Desktop",
        "OAI-Product-Sku": "CODEX",
        "Accept": "application/json",
    }
    if account_id:
        headers["ChatGPT-Account-ID"] = account_id

    request = urllib.request.Request(endpoint, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise CodexResetsError(f"request failed with HTTP {exc.code}: {body[:300]}") from exc
    except urllib.error.URLError as exc:
        raise CodexResetsError(f"request failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise CodexResetsError("request timed out") from exc
    except json.JSONDecodeError as exc:
        raise CodexResetsError(f"endpoint returned invalid JSON: {exc}") from exc


def normalize_resets(payload):
    if isinstance(payload, list):
        raw_resets = payload
    elif isinstance(payload, dict) and isinstance(payload.get("resets"), list):
        raw_resets = payload["resets"]
    elif isinstance(payload, dict) and isinstance(payload.get("credits"), list):
        raw_resets = [
            item
            for item in payload["credits"]
            if item.get("status", "available") == "available"
        ]
    else:
        raw_resets = []

    resets = []
    for item in raw_resets:
        if not isinstance(item, dict):
            continue
        expires_at = parse_datetime(item.get("expires_at") or item.get("expiresAt"))
        resets.append(
            {
                "title": item.get("title") or item.get("description") or "Full reset",
                "expires_at": expires_at,
            }
        )
    return sorted(resets, key=lambda reset: reset["expires_at"] or datetime.max.replace(tzinfo=timezone.utc))


def parse_datetime(value):
    if not value or not isinstance(value, str):
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_resets(resets, now, color_enabled):
    if not resets:
        return "Available resets: 0"

    lines = [f"Available resets: {len(resets)}"]
    for reset in resets:
        lines.extend(format_reset(reset, now, color_enabled))
    return "\n".join(lines)


def format_reset(reset, now, color_enabled):
    expires_at = reset["expires_at"]
    title = reset["title"]

    if expires_at is None:
        return [f"- {title} - expiry unknown"]

    days_remaining = math.ceil((expires_at - now).total_seconds() / 86_400)
    urgency = urgency_level(days_remaining)
    title_text = colorize(title, urgency, color_enabled)
    expires_line = f"  Expires: {format_local(expires_at)} local / {format_utc(expires_at)} UTC"

    return [
        f"- {title_text} - {format_days_remaining(days_remaining)}",
        dim(expires_line, color_enabled),
    ]


def urgency_level(days_remaining):
    if days_remaining < 3:
        return "urgent"
    if days_remaining < 7:
        return "soon"
    return "plenty"


def format_days_remaining(days_remaining):
    if days_remaining == 1:
        return "1 day remaining"
    if days_remaining == 0:
        return "expires today"
    if days_remaining < 0:
        days_expired = abs(days_remaining)
        unit = "day" if days_expired == 1 else "days"
        return f"expired {days_expired} {unit} ago"
    return f"{days_remaining} days remaining"


def colorize(value, urgency, color_enabled):
    if not color_enabled:
        return value
    colors = {
        "urgent": "\033[31;1m",
        "soon": "\033[33m",
        "plenty": "\033[32m",
    }
    color = colors.get(urgency)
    return f"{color}{value}\033[0m" if color else value


def dim(value, color_enabled):
    return f"\033[2m{value}\033[0m" if color_enabled else value


def format_local(value):
    local = value.astimezone()
    return local.strftime("%b %d, %Y, %I:%M %p %Z")


def format_utc(value):
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")


class CodexResetsError(Exception):
    pass


if __name__ == "__main__":
    raise SystemExit(main())
