#!/usr/bin/env python3

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


DEFAULT_BASE_URL = "https://api.qingyuntop.top/v1"
DEFAULT_MODEL = "deepseek-v3.1"
DEFAULT_SYSTEM_PROMPT = "You are a concise helpful assistant."
DEFAULT_QUESTION = "请用一句话回答：苹果公司总部在哪个城市？"


def chat_completion(
    api_key: str,
    question: str,
    model: str,
    base_url: str,
    system_prompt: str,
    temperature: float,
    timeout_seconds: float = 180,
) -> dict:
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
    }

    request = urllib.request.Request(
        url=f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send a simple chat request to Qingyuntop."
    )
    parser.add_argument(
        "question",
        nargs="?",
        default=DEFAULT_QUESTION,
        help="Question to ask the model.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model name to use. Default: {DEFAULT_MODEL}",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"OpenAI-compatible base URL. Default: {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--system-prompt",
        default=DEFAULT_SYSTEM_PROMPT,
        help="System prompt to send with the request.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.2,
        help="Sampling temperature. Default: 0.2",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full JSON response instead of just the answer text.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("QINGYUNTOP_API_KEY")
    if not api_key:
        print("Missing environment variable: QINGYUNTOP_API_KEY", file=sys.stderr)
        return 1

    try:
        result = chat_completion(
            api_key=api_key,
            question=args.question,
            model=args.model,
            base_url=args.base_url,
            system_prompt=args.system_prompt,
            temperature=args.temperature,
            timeout_seconds=180,
        )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code}: {body}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    try:
        print(result["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
