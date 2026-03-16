#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
from openai import OpenAI


DEFAULT_BASE_URL = os.getenv("QINGYUNTOP_BASE_URL", "https://api.qingyuntop.top/v1")


def _api_key() -> str:
    return (
        os.getenv("QINGYUNTOP_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("LLM_API_KEY")
        or ""
    )


def list_models(base_url: str) -> dict:
    api_key = _api_key()
    if not api_key:
        raise RuntimeError("Missing QINGYUNTOP_API_KEY")

    resp = httpx.get(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()


def chat_once(base_url: str, model: str) -> str:
    api_key = _api_key()
    if not api_key:
        raise RuntimeError("Missing QINGYUNTOP_API_KEY")

    client = OpenAI(api_key=api_key, base_url=base_url.rstrip("/"), timeout=60.0)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are a concise assistant. Reply with a one-line JSON object.",
            },
            {
                "role": "user",
                "content": 'Return {"status":"ok","provider":"qingyuntop"}',
            },
        ],
        temperature=0,
        max_tokens=80,
    )
    return response.choices[0].message.content or ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Qingyuntop connectivity smoke test")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base URL")
    parser.add_argument("--model", default="deepseek-v3.1", help="Model for chat test")
    parser.add_argument("--list-models", action="store_true", help="List models instead of sending chat")
    parser.add_argument(
        "--write-files",
        action="store_true",
        help="When used with --list-models, write models_snapshot.json and models_openai.txt next to this script",
    )
    args = parser.parse_args()

    if args.list_models:
        payload = list_models(args.base_url)
        model_ids = sorted(
            item["id"]
            for item in payload.get("data", [])
            if "openai" in [str(v).lower() for v in item.get("supported_endpoint_types", [])]
        )
        if args.write_files:
            root = Path(__file__).resolve().parent
            (root / "models_snapshot.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (root / "models_openai.txt").write_text(
                "\n".join(model_ids) + "\n",
                encoding="utf-8",
            )
        print(json.dumps({"count": len(model_ids), "models": model_ids}, ensure_ascii=False, indent=2))
        return 0

    content = chat_once(args.base_url, args.model)
    print(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
