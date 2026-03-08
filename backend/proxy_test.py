import asyncio
import httpx
import json

PROXY_URL = "https://proxy.pichat.me"
PROXY_APP_KEY = "pm2k_SsTGuEcA8C3s1KLzPXpRPNUNvqivQbS8"

async def test_stream():
    payload = {
        "messages": [{"role": "user", "content": "hello"}],
        "temperature": 0.4,
        "max_tokens": 500,
        "mode": "single",
        "stream": True,
        "top_p": 0.9
    }
    headers = {
        "Content-Type": "application/json",
        "X-App-Key": PROXY_APP_KEY
    }

    print(f"Connecting to {PROXY_URL}/proxy/ai...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        async with client.stream("POST", f"{PROXY_URL}/proxy/ai", headers=headers, json=payload) as response:
            print(f"Status: {response.status_code}")
            
            async for line in response.aiter_lines():
                print(f"RAW LINE: {repr(line)}")

if __name__ == "__main__":
    asyncio.run(test_stream())
