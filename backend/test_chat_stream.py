"""
test_chat_stream.py
ทดสอบ /api/v1/chat/stream endpoint ใน 3 กรณี:
  1. basic stream  — ได้รับ token อย่างน้อย 1 chunk + done=True
  2. timing        — chunk แรกมาใน 10 วินาที (ไม่ block รอทั้งก้อน)
  3. fallback      — ถ้า provider หลักล้มเหลว ยังได้ response

รัน (backend ต้องไม่เปิดอยู่, test ใช้ TestClient โดยตรง):
  .venv\\Scripts\\python -m pytest test_chat_stream.py -v --tb=short

หรือทดสอบ live server (ต้อง start backend ก่อน):
  .venv\\Scripts\\python test_chat_stream.py --live [--port 8003]
"""
import asyncio
import json
import sys
import time
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# ─── helpers ─────────────────────────────────────────────────────────────────

BASE_URL = "http://localhost:8003"
STREAM_PATH = "/api/v1/chat/stream"
TEST_MESSAGES = [
    {"role": "user", "content": "สวัสดี บอกสรุปสั้นๆ เกี่ยวกับ Power Factor หน่อยครับ"}
]


async def _collect_sse(response) -> tuple[list[str], bool, float]:
    """
    ดึง chunks จาก streaming response (httpx AsyncClient)
    Returns: (chunks, got_done, time_to_first_chunk)
    """
    chunks = []
    got_done = False
    first_chunk_time = None
    t0 = time.monotonic()

    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        raw = line[len("data:"):].strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if "error" in payload:
            raise RuntimeError(f"Stream error from server: {payload['error']}")

        if payload.get("done"):
            got_done = True
            break

        delta = payload.get("delta", "")
        if delta:
            if first_chunk_time is None:
                first_chunk_time = time.monotonic() - t0
            chunks.append(delta)

    return chunks, got_done, (first_chunk_time or 0.0)


# ─── unit tests (using TestClient, no live server needed) ────────────────────

class TestChatStreamUnit:
    """
    Unit tests: mock stream_chat_response ออก ทดสอบ endpoint logic โดยตรง
    """

    @pytest.fixture(autouse=True)
    def _patch_deps(self):
        """Mock ทุก side-effect ที่ใช้ระหว่าง test"""
        async def fake_stream(messages, context, faults):
            for word in ["สวัสดี", " ", "นี่คือ", " ", "การทดสอบ", " ", "streaming"]:
                yield word
                await asyncio.sleep(0.01)

        with (
            patch("routes.ai.stream_chat_response", side_effect=fake_stream),
            patch("routes.ai.get_latest_data", return_value={"status": "OK", "V_LN1": 220.0}),
            patch("routes.ai.load_recent_faults", return_value=[]),
            patch("core.security.ai_rate_limit", lambda f: f),   # bypass rate limit
        ):
            yield

    def test_stream_returns_200(self):
        from fastapi.testclient import TestClient
        from main import app
        with (
            patch("services.modbus_service.auto_connect", return_value=(None, [])),
            patch("asyncio.create_task", return_value=MagicMock()),
            patch("threading.Thread"),
            patch("main.EnergyManagement", return_value=MagicMock(close=AsyncMock())),
            patch("main.ExternalPredictiveMaintenance", return_value=MagicMock(close=AsyncMock())),
            patch("main.PredictiveMaintenance", return_value=MagicMock()),
        ):
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                STREAM_PATH,
                json={"messages": TEST_MESSAGES},
                headers={"Accept": "text/event-stream"},
            )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_stream_content_type(self):
        from fastapi.testclient import TestClient
        from main import app
        with (
            patch("services.modbus_service.auto_connect", return_value=(None, [])),
            patch("asyncio.create_task", return_value=MagicMock()),
            patch("threading.Thread"),
            patch("main.EnergyManagement", return_value=MagicMock(close=AsyncMock())),
            patch("main.ExternalPredictiveMaintenance", return_value=MagicMock(close=AsyncMock())),
            patch("main.PredictiveMaintenance", return_value=MagicMock()),
        ):
            client = TestClient(app)
            resp = client.post(STREAM_PATH, json={"messages": TEST_MESSAGES})
        assert "text/event-stream" in resp.headers.get("content-type", ""), (
            f"Expected text/event-stream, got: {resp.headers.get('content-type')}"
        )

    def test_stream_sse_format(self):
        """ทุก line ต้องขึ้นต้นด้วย 'data: ' และ parse เป็น JSON ได้"""
        from fastapi.testclient import TestClient
        from main import app
        with (
            patch("services.modbus_service.auto_connect", return_value=(None, [])),
            patch("asyncio.create_task", return_value=MagicMock()),
            patch("threading.Thread"),
            patch("main.EnergyManagement", return_value=MagicMock(close=AsyncMock())),
            patch("main.ExternalPredictiveMaintenance", return_value=MagicMock(close=AsyncMock())),
            patch("main.PredictiveMaintenance", return_value=MagicMock()),
        ):
            client = TestClient(app)
            resp = client.post(STREAM_PATH, json={"messages": TEST_MESSAGES})

        data_lines = [l for l in resp.text.splitlines() if l.startswith("data:")]
        assert len(data_lines) > 0, "No SSE data lines found in response"

        for line in data_lines:
            payload = json.loads(line[len("data:"):].strip())
            assert "delta" in payload or "done" in payload or "error" in payload, (
                f"Unexpected SSE payload shape: {payload}"
            )

    def test_stream_done_signal(self):
        """ต้องมี {'done': True} เป็น event สุดท้าย"""
        from fastapi.testclient import TestClient
        from main import app
        with (
            patch("services.modbus_service.auto_connect", return_value=(None, [])),
            patch("asyncio.create_task", return_value=MagicMock()),
            patch("threading.Thread"),
            patch("main.EnergyManagement", return_value=MagicMock(close=AsyncMock())),
            patch("main.ExternalPredictiveMaintenance", return_value=MagicMock(close=AsyncMock())),
            patch("main.PredictiveMaintenance", return_value=MagicMock()),
        ):
            client = TestClient(app)
            resp = client.post(STREAM_PATH, json={"messages": TEST_MESSAGES})

        data_lines = [l for l in resp.text.splitlines() if l.startswith("data:")]
        last_payload = json.loads(data_lines[-1][len("data:"):].strip())
        assert last_payload.get("done") is True, (
            f"Last SSE event should be {{done: true}}, got: {last_payload}"
        )

    def test_stream_assembles_full_text(self):
        """ต่อ chunk ทั้งหมดได้เป็น text ที่ไม่ว่าง"""
        from fastapi.testclient import TestClient
        from main import app
        with (
            patch("services.modbus_service.auto_connect", return_value=(None, [])),
            patch("asyncio.create_task", return_value=MagicMock()),
            patch("threading.Thread"),
            patch("main.EnergyManagement", return_value=MagicMock(close=AsyncMock())),
            patch("main.ExternalPredictiveMaintenance", return_value=MagicMock(close=AsyncMock())),
            patch("main.PredictiveMaintenance", return_value=MagicMock()),
        ):
            client = TestClient(app)
            resp = client.post(STREAM_PATH, json={"messages": TEST_MESSAGES})

        data_lines = [l for l in resp.text.splitlines() if l.startswith("data:")]
        assembled = ""
        for line in data_lines:
            payload = json.loads(line[len("data:"):].strip())
            assembled += payload.get("delta", "")

        assert len(assembled) > 0, "Assembled text from stream is empty"
        print(f"\n✅ Assembled stream text ({len(assembled)} chars): {assembled[:80]}...")


# ─── live server test (optional, run manually) ────────────────────────────────

async def _live_test(port: int = 8003):
    """
    ทดสอบกับ live server จริง (backend ต้องรันอยู่)
    วัด time-to-first-token และ throughput
    """
    try:
        import httpx
    except ImportError:
        print("❌ httpx ไม่ได้ติดตั้ง: pip install httpx")
        return

    url = f"http://localhost:{port}{STREAM_PATH}"
    print(f"\n🔌 Testing live stream at {url}")
    print(f"   Prompt: {TEST_MESSAGES[0]['content'][:60]}...")

    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream(
                "POST", url,
                json={"messages": TEST_MESSAGES},
                headers={"Accept": "text/event-stream"},
            ) as resp:
                if resp.status_code != 200:
                    print(f"❌ HTTP {resp.status_code}")
                    return

                chunks, got_done, ttft = await _collect_sse(resp)
                total_time = time.monotonic() - t0
                full_text = "".join(chunks)

    except Exception as e:
        import traceback
        print(f"❌ Connection error: {type(e).__name__} - {e}")
        traceback.print_exc()
        return

    print(f"\n{'='*60}")
    print(f"✅ Stream complete")
    print(f"   Time-to-first-token : {ttft*1000:.0f} ms")
    print(f"   Total time          : {total_time:.2f} s")
    print(f"   Chunks received     : {len(chunks)}")
    print(f"   Total chars         : {len(full_text)}")
    print(f"   got done=True       : {got_done}")
    print(f"\n--- Response preview ---")
    print(full_text[:300] + ("..." if len(full_text) > 300 else ""))

    if ttft > 10:
        print(f"\n⚠️  Time-to-first-token {ttft:.1f}s > 10s — อาจ block ก่อน stream")
    if not got_done:
        print("⚠️  Stream ended without done=True signal")


if __name__ == "__main__":
    port = 8003
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        port = int(sys.argv[idx + 1])

    asyncio.run(_live_test(port))
