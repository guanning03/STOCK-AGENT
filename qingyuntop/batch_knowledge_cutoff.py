#!/usr/bin/env python3

import argparse
import concurrent.futures
import json
import os
import sys
import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from ask import DEFAULT_BASE_URL, chat_completion


DEFAULT_MODELS_FILE = Path(__file__).with_name("models_openai.txt")
DEFAULT_OUTPUT_FILE = Path(__file__).with_name("knowledge_cutoff_reply.md")
DEFAULT_QUESTION = "简洁的告诉我你的知识截止到什么时候。"
DEFAULT_SYSTEM_PROMPT = "You are a concise helpful assistant."
RETRYABLE_HTTP_CODES = {408, 409, 429, 500, 502, 503, 504}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ask every model in a file about its knowledge cutoff."
    )
    parser.add_argument(
        "--models-file",
        type=Path,
        default=DEFAULT_MODELS_FILE,
        help=f"Path to a newline-delimited model list. Default: {DEFAULT_MODELS_FILE}",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help=f"Unified output file. Default: {DEFAULT_OUTPUT_FILE}",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"OpenAI-compatible base URL. Default: {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--question",
        default=DEFAULT_QUESTION,
        help=f"Question to ask each model. Default: {DEFAULT_QUESTION}",
    )
    parser.add_argument(
        "--system-prompt",
        default=DEFAULT_SYSTEM_PROMPT,
        help="System prompt to send with each request.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.2,
        help="Sampling temperature. Default: 0.2",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.0,
        help="Optional delay between model requests. Default: 0.0",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=0,
        help="Retry count for retryable HTTP/network errors. Default: 0",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=2.0,
        help="Initial retry backoff in seconds. Default: 2.0",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=20.0,
        help="Per-request timeout in seconds. Default: 20.0",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Concurrent workers to use. Default: 4",
    )
    return parser.parse_args()


def load_models(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def extract_text(result: dict) -> str:
    try:
        content = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return json.dumps(result, ensure_ascii=False, indent=2)

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(part for part in parts if part)
    return str(content)


def write_header(output_file: Path, models_file: Path, args: argparse.Namespace, count: int) -> None:
    header = [
        "# Qingyuntop Knowledge Cutoff Replies",
        "",
        f"- Generated at: `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`",
        f"- Models file: `{models_file}`",
        f"- Base URL: `{args.base_url}`",
        f"- Question: `{args.question}`",
        f"- Total models: `{count}`",
        f"- Temperature: `{args.temperature}`",
        f"- Timeout seconds: `{args.timeout_seconds}`",
        f"- Max retries: `{args.max_retries}`",
        f"- Workers: `{args.workers}`",
        "",
    ]
    output_file.write_text("\n".join(header), encoding="utf-8")


def append_section(
    output_file: Path,
    index: int,
    model: str,
    status: str,
    body: str,
    usage: dict | None,
    elapsed_seconds: float,
) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        f"## {index}. {model}",
        "",
        f"- Status: `{status}`",
        f"- Finished at: `{timestamp}`",
        f"- Elapsed seconds: `{elapsed_seconds:.2f}`",
        "",
        "Reply:",
        "",
        "`````text",
        body.rstrip() or "(empty reply)",
        "`````",
        "",
    ]
    if usage is not None:
        lines.extend(
            [
                "Usage:",
                "",
                "`````json",
                json.dumps(usage, ensure_ascii=False, indent=2),
                "`````",
                "",
            ]
        )
    with output_file.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def request_with_retries(
    api_key: str,
    question: str,
    model: str,
    base_url: str,
    system_prompt: str,
    temperature: float,
    timeout_seconds: float,
    max_retries: int,
    retry_backoff_seconds: float,
) -> tuple[str, str, dict | None]:
    attempt = 0
    delay = retry_backoff_seconds
    while True:
        try:
            result = chat_completion(
                api_key=api_key,
                question=question,
                model=model,
                base_url=base_url,
                system_prompt=system_prompt,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
            )
            return "ok", extract_text(result), result.get("usage")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in RETRYABLE_HTTP_CODES and attempt < max_retries:
                time.sleep(delay)
                attempt += 1
                delay *= 2
                continue
            return f"http_error_{exc.code}", body, None
        except urllib.error.URLError as exc:
            if attempt < max_retries:
                time.sleep(delay)
                attempt += 1
                delay *= 2
                continue
            return "url_error", str(exc), None
        except Exception as exc:  # noqa: BLE001
            return exc.__class__.__name__, str(exc), None


def query_one_model(
    index: int,
    model: str,
    args: argparse.Namespace,
    api_key: str,
) -> tuple[int, str, str, str, dict | None, float]:
    start = time.monotonic()
    status, body, usage = request_with_retries(
        api_key=api_key,
        question=args.question,
        model=model,
        base_url=args.base_url,
        system_prompt=args.system_prompt,
        temperature=args.temperature,
        timeout_seconds=args.timeout_seconds,
        max_retries=args.max_retries,
        retry_backoff_seconds=args.retry_backoff_seconds,
    )
    elapsed_seconds = time.monotonic() - start
    if args.sleep_seconds > 0:
        time.sleep(args.sleep_seconds)
    return index, model, status, body, usage, elapsed_seconds


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("QINGYUNTOP_API_KEY")
    if not api_key:
        print("Missing environment variable: QINGYUNTOP_API_KEY", file=sys.stderr)
        return 1

    models = load_models(args.models_file)
    if not models:
        print(f"No models found in {args.models_file}", file=sys.stderr)
        return 1

    write_header(args.output_file, args.models_file, args, len(models))

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_model = {
            executor.submit(query_one_model, index, model, args, api_key): (index, model)
            for index, model in enumerate(models, start=1)
        }
        completed = 0
        for future in concurrent.futures.as_completed(future_to_model):
            index, model, status, body, usage, elapsed_seconds = future.result()
            append_section(
                output_file=args.output_file,
                index=index,
                model=model,
                status=status,
                body=body,
                usage=usage,
                elapsed_seconds=elapsed_seconds,
            )
            completed += 1
            print(
                f"[{completed}/{len(models)}] finished {index}. {model} status={status}",
                flush=True,
            )

    print(f"wrote {len(models)} results to {args.output_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
