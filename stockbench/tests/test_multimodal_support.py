import json
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from stockbench.core.features import build_features_for_prompt
from stockbench.llm.llm_client import LLMClient, LLMConfig


def test_build_features_for_prompt_preserves_news_image_inputs():
    bars_day = pd.DataFrame(
        [
            {"date": "2025-03-03", "open": 100.0, "close": 101.0},
            {"date": "2025-03-04", "open": 101.0, "close": 102.0},
            {"date": "2025-03-05", "open": 102.0, "close": 103.0},
        ]
    )
    news_items = [
        {
            "title": "Apple launches new device",
            "description": "Investors react to the launch event.",
            "image": "https://example.com/apple-device.jpg",
            "source": "Example News",
        }
    ]
    config = {
        "news": {
            "enabled": True,
            "top_k_event_count": 5,
            "include_image_inputs": True,
            "max_image_count_per_symbol": 1,
        }
    }

    result = build_features_for_prompt(
        bars_day=bars_day,
        snapshot={"symbol": "AAPL", "price": 103.0, "ts_utc": "2025-03-05T00:00:00Z"},
        news_items=news_items,
        position_state={"current_position_value": 1000.0, "holding_days": 3, "shares": 10.0},
        details={"ticker": "AAPL"},
        config=config,
        include_price=True,
        exclude_fundamental=True,
    )

    news_events = result["features"]["news_events"]
    assert news_events["top_k_events"] == ["Apple launches new device - Investors react to the launch event."]
    assert news_events["image_inputs"] == [
        {
            "symbol": "AAPL",
            "event_index": 1,
            "title": "Apple launches new device",
            "source": "Example News",
            "image_url": "https://example.com/apple-device.jpg",
        }
    ]


def test_llm_client_builds_multimodal_message_from_json_prompt(tmp_path):
    client = LLMClient(cache_dir=str(tmp_path))
    cfg = LLMConfig(model="gpt-4o", supports_image_input=True, max_input_images=1)

    payload = {
        "symbols": {
            "AAPL": {
                "features": {
                    "news_events": {
                        "top_k_events": ["Apple launches new device"],
                        "image_inputs": [
                            {
                                "symbol": "AAPL",
                                "event_index": 1,
                                "title": "Apple launches new device",
                                "image_url": "https://example.com/apple-device.jpg",
                            }
                        ],
                    }
                }
            },
            "MSFT": {
                "features": {
                    "news_events": {
                        "top_k_events": ["Microsoft signs major deal"],
                        "image_inputs": [
                            {
                                "symbol": "MSFT",
                                "event_index": 1,
                                "title": "Microsoft signs major deal",
                                "image_url": "https://example.com/microsoft-deal.jpg",
                            }
                        ],
                    }
                }
            },
        }
    }
    prompt_text = json.dumps(payload, ensure_ascii=False) + "\n\nRetry note: keep the answer concise."

    content = client._build_user_message_content(cfg, prompt_text)

    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert "Retry note" in content[0]["text"]

    image_parts = [part for part in content if part.get("type") == "image_url"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"] == "https://example.com/apple-device.jpg"
