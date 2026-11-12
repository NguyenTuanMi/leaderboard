#!/usr/bin/env python3
"""Generate CARLA leaderboard routes from a natural-language scenario description.

The model supplies scenario choices and weather, while route geometry is copied
from an existing route. This keeps generated coordinates valid for the selected
CARLA town and makes the tool usable with local or remote OpenAI-compatible APIs.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from scenario_generator.config.inference_params import *  # noqa: F401,F403
from scenario_generator.scenario_generation.scenario_helper import GenerationError

def _endpoint_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def _open_model_request(request: urllib.request.Request, timeout: float):
    """Open local model requests without inherited HTTP(S) proxy settings.

    Remote endpoints retain the environment proxy policy. This mirrors the
    networking behavior used by the supplied OpenAI/httpx client.
    """
    host = urlparse(request.full_url).hostname
    if host in ("localhost", "127.0.0.1", "::1"):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)


def call_model(
    prompt: str,
    endpoint: str,
    model: str,
    api_key: str | None,
    timeout: float,
    max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_completion_tokens": max_completion_tokens,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        _endpoint_url(endpoint),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + (api_key if api_key else "not-needed"),
        },
        method="POST",
    )
    try:
        with _open_model_request(request, timeout) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise GenerationError(f"Model request failed with HTTP {error.code}: {body}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise GenerationError(f"Could not reach model endpoint {_endpoint_url(endpoint)}: {error}") from error
    except json.JSONDecodeError as error:
        raise GenerationError("Model endpoint returned invalid JSON") from error

    try:
        content = response_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise GenerationError("Model response has no choices[0].message.content") from error
    return _parse_model_json(content)


def _parse_model_json(content: Any) -> dict[str, Any]:
    if not isinstance(content, str):
        raise GenerationError("Model message content must be a JSON string")
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        result = json.loads(content)
    except json.JSONDecodeError as error:
        raise GenerationError(f"Model content is not valid JSON: {error}") from error
    if not isinstance(result, dict):
        raise GenerationError("Model JSON root must be an object")
    return result


def call_model_llm(prompt: str, model_name: str, api_key: str | None) -> dict[str, Any]:
    """Call a model through Simon Willison's `llm` library (https://github.com/simonw/llm).

    Models, aliases and stored keys (`llm keys set openai`) are shared with the
    `llm` command line tool; plugins add local and other providers.
    """
    try:
        import llm
    except ImportError as error:
        raise GenerationError("The llm backend needs the package: pip install llm") from error
    try:
        model = llm.get_model(model_name)
    except llm.UnknownModelError as error:
        raise GenerationError(f"llm does not know model '{model_name}' (see `llm models`)") from error
    if api_key:
        model.key = api_key
    options = {"json_object": True} if "json_object" in model.Options.model_fields else {}
    try:
        text = model.prompt(prompt, system=SYSTEM_PROMPT, stream=False, options=options).text()
    except llm.ModelError as error:
        raise GenerationError(f"llm model call failed: {error}") from error
    return _parse_model_json(text)

def _ask_model(args: argparse.Namespace, prompt: str) -> dict[str, Any]:
    if args.backend == "llm":
        return call_model_llm(prompt, args.model, args.api_key)
    return call_model(
        prompt, args.endpoint, args.model, args.api_key, args.timeout, args.max_completion_tokens
    )

def request_routes(args: argparse.Namespace, prompt: str) -> list[dict[str, Any]]:
    """Ask the model for the raw route objects (not yet validated)."""
    routes = _ask_model(args, prompt).get("routes")
    if not isinstance(routes, list) or len(routes) != args.count:
        raise GenerationError(f"Model must return exactly {args.count} route objects")
    return routes
