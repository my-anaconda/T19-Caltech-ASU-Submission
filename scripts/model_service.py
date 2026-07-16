#!/usr/bin/env python3
"""
T19 ASU ICLAD 2026 - Local Vertex AI Express Mode model service (dev/test only)
==================================================================================
Implements the exact model_endpoint HTTP contract described in AGENT_GUIDE.md
(POST /generate, GET /health) so agent.py can be exercised end-to-end before
relying on the official benchmark's own model service.

This is a DEVELOPMENT CONVENIENCE, not part of the submission (only agent.py
gets submitted, per README.md's "Final Submission Package" section). Start
this, then point the official runner at it with --upstream-endpoint.

Usage:
    export EXPRESS_MODE_KEY="your_actual_api_key_here"
    python3 scripts/model_service.py --port 9000

    # In another terminal, from the official ICLAD26-ASU-Problems checkout:
    python3 scripts/run_block_benchmark.py --case Block1 \\
        --agent-path /path/to/T19-Caltech-ASU-Submission/agent.py \\
        --run-id t19-safe-floor \\
        --upstream-endpoint http://127.0.0.1:9000
"""

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

# Lightweight .env loader (no extra dependency) so EXPRESS_MODE_KEY can be
# provided once via a file instead of exporting it in every shell.
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _, _value = _line.partition("=")
        os.environ.setdefault(_key.strip(), _value.strip().strip('"').strip("'"))


class GeminiVertexWrapper:
    def __init__(self):
        self.api_key = os.environ.get("EXPRESS_MODE_KEY")
        if not self.api_key:
            print("[WARN] EXPRESS_MODE_KEY environment variable not set.", file=sys.stderr)
        if genai is None:
            raise RuntimeError("google-genai is not installed. Run: pip install google-genai")
        self.client = genai.Client(
            vertexai=True,
            api_key=self.api_key,
            http_options={"headers": {"X-Goog-User-Project": ""}},
        )

    def generate(self, model, prompt, max_output_tokens=2048):
        config = types.GenerateContentConfig(
            max_output_tokens=max_output_tokens,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
        response = self.client.models.generate_content(model=model, contents=prompt, config=config)

        usage = {
            "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
            "cache_write_tokens": 0, "thoughts_tokens": 0, "tool_use_prompt_tokens": 0,
            "total_tokens": 0, "usage_source": "provider",
        }
        if getattr(response, "usage_metadata", None):
            um = response.usage_metadata
            usage["input_tokens"] = um.prompt_token_count or 0
            usage["output_tokens"] = um.candidates_token_count or 0
            usage["total_tokens"] = (um.prompt_token_count or 0) + (um.candidates_token_count or 0)
        return response.text or "", usage


_state = {"client": None}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        print(f"[model_service] {fmt % a}", file=sys.stderr)

    def _send_json(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/generate":
            self._send_json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(length).decode("utf-8"))
            model = req["model"]
            prompt = req["prompt"]
            max_tokens = req.get("max_output_tokens", 2048)
        except Exception as e:
            self._send_json(400, {"error": f"bad request: {e}", "retryable": False})
            return

        try:
            text, usage = _state["client"].generate(model, prompt, max_tokens)
            self._send_json(200, {"text": text, "diagnostics": {}, "usage": usage})
        except Exception as e:
            self._send_json(500, {"error": str(e), "retryable": True, "provider": "vertexai"})


def main():
    parser = argparse.ArgumentParser(description="Local model_endpoint service for ASU agent testing")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()

    _state["client"] = GeminiVertexWrapper()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[model_service] Listening on http://127.0.0.1:{args.port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
