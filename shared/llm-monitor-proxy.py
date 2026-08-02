"""Transparent LLM proxy: monitors Nebula proxy traffic, logs to JSONL."""
from __future__ import annotations
import asyncio, json, os, sys, time
from datetime import datetime, timezone

import httpx

PROXY_LLM_URL = (os.environ.get("NEBULA_PROXY_URL", "https://api.nebula.gg")
                 .rstrip("/") + "/internal/proxy/llm")

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = int(os.environ.get("LLM_PROXY_PORT", "9090"))
LOG_FILE = os.environ.get("LLM_PROXY_LOG") or "/home/nebula/nebula-llm-usage.jsonl"

client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))


def _detect_provider(model: str) -> str:
    m = model.lower()
    if "gpt" in m or "o1" in m or "o3" in m or "chatgpt" in m:
        return "openai"
    if "claude" in m:
        return "anthropic"
    if "gemini" in m:
        return "google"
    if "deepseek" in m:
        return "deepseek"
    if "mistral" in m or "mixtral" in m:
        return "mistral"
    if "llama" in m or "codellama" in m:
        return "meta"
    return "unknown"


def _parse_headers(data: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in data.split(b"\r\n")[1:]:
        if not line or b":" not in line:
            break
        k, _, v = line.decode(errors="replace").partition(":")
        headers[k.strip().lower()] = v.strip()
    return headers


def _append_log(entry: dict) -> None:
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


async def _write_response(writer: asyncio.StreamWriter, resp: httpx.Response) -> None:
    body = resp.content
    status_line = f"HTTP/1.1 {resp.status_code} {resp.reason_phrase or 'OK'}"
    ct = resp.headers.get("content-type", "application/json")
    payload = f"{status_line}\r\nContent-Type: {ct}\r\nContent-Length: {len(body)}\r\n\r\n"
    writer.write(payload.encode())
    writer.write(body)
    await writer.drain()


def _forward_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


async def _forward_and_respond(
    writer: asyncio.StreamWriter,
    request_data: bytes,
    path: str,
    *,
    url: str | None = None,
) -> None:
    """Forward request to upstream and write response to writer."""
    headers = _parse_headers(request_data)
    headers.pop("host", None)
    headers.pop("content-length", None)
    body_start = request_data.find(b"\r\n\r\n") + 4
    body = request_data[body_start:]

    target = url or _forward_url(PROXY_LLM_URL, path)
    resp = await client.post(target, headers=headers, content=body)
    await _write_response(writer, resp)


async def handle_request(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    # Read HTTP request
    request_data = b""
    try:
        while True:
            chunk = await asyncio.wait_for(reader.read(65536), timeout=5.0)
            if not chunk:
                break
            request_data += chunk
            if b"\r\n\r\n" in request_data:
                break
    except asyncio.TimeoutError:
        pass

    if not request_data:
        writer.close()
        return

    try:
        request_line = request_data.split(b"\r\n")[0].decode()
        method, path, _ = request_line.split(" ")
    except Exception:
        writer.close()
        return

    # Extract body
    body_start = request_data.find(b"\r\n\r\n") + 4
    body_raw = request_data[body_start:]
    try:
        req_body = json.loads(body_raw) if body_raw.strip() else {}
    except json.JSONDecodeError:
        req_body = {}

    model = req_body.get("model", "unknown")
    provider = _detect_provider(model)

    t0 = time.monotonic()
    try:
        headers = _parse_headers(request_data)
        headers.pop("host", None)
        headers.pop("content-length", None)

        resp = await client.post(
            _forward_url(PROXY_LLM_URL, path), headers=headers, content=body_raw
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        usage = {}
        try:
            rj = resp.json()
            usage = rj.get("usage", {})
        except Exception:
            pass

        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "provider": provider,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "cost": usage.get("cost", 0),
            "duration_ms": elapsed_ms,
            "status": "ok" if resp.status_code < 400 else "error",
        }
        _append_log(log_entry)

        await _write_response(writer, resp)
    except Exception as e:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        _append_log({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "provider": provider,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost": 0,
            "duration_ms": elapsed_ms,
            "status": "error",
            "error": str(e),
        })
        try:
            writer.write(
                b"HTTP/1.1 502 Bad Gateway\r\nContent-Type: text/plain\r\n"
                b"Content-Length: 12\r\n\r\nProxy error"
            )
            await writer.drain()
        except Exception:
            pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def main() -> None:
    server = await asyncio.start_server(handle_request, LISTEN_HOST, LISTEN_PORT)
    addr = server.sockets[0].getsockname()
    print(
        f"LLM monitor proxy on http://{addr[0]}:{addr[1]} -> {PROXY_LLM_URL}\n"
        f"Log: {LOG_FILE}",
        file=sys.stderr,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
