import os
import time
import logging
import asyncio
import httpx
import json
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, Request, HTTPException, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pm2000-proxy")

# Keys & Auth
ALLOWED_APP_KEYS = [k.strip() for k in os.getenv("ALLOWED_APP_KEYS", "").split(",") if k.strip()]
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_AGENT_ID = os.getenv("MISTRAL_AGENT_ID", "")
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_API_BASE = os.getenv("DASHSCOPE_API_BASE", "https://coding-intl.dashscope.aliyuncs.com/v1")
DASHSCOPE_MODEL = os.getenv("DASHSCOPE_MODEL", "qwen-plus")
DASHSCOPE_FALLBACK_MODEL = os.getenv("DASHSCOPE_FALLBACK_MODEL", "qwen-max")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")

# Limiter
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="PM2000 AI Proxy", docs_url=None, redoc_url=None)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# --- Models ---

class ChatRequest(BaseModel):
    messages: List[Dict[str, str]]
    max_tokens: int = 600
    temperature: float = 0.4
    model: Optional[str] = None
    mode: str = "single"  # "single" = Mistral primary | "parallel" = race both AIs
    stream: bool = False
    top_p: float = 1.0

class LinePushRequest(BaseModel):
    text: str
    user_id: Optional[str] = None

class LineReplyRequest(BaseModel):
    reply_token: str
    text: str

# --- Auth Dependency ---

async def verify_app_key(x_app_key: str = Header(...)):
    if not x_app_key or x_app_key not in ALLOWED_APP_KEYS:
        logger.warning(f"Unauthorized access attempt with key: {x_app_key}")
        raise HTTPException(status_code=403, detail="Invalid APP_KEY")
    return x_app_key

# --- AI Call Helpers (matched to ai_analyzer.py patterns) ---

async def _call_dashscope(messages, max_tokens=600, temperature=0.4, use_fallback=False):
    """Call DashScope using OpenAI-compatible endpoint (same as ai_analyzer.py)."""
    model = DASHSCOPE_FALLBACK_MODEL if use_fallback else DASHSCOPE_MODEL
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature
    }
    
    timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{DASHSCOPE_API_BASE}/chat/completions",
            headers=headers,
            json=payload
        )
        if resp.status_code != 200:
            logger.error(f"DashScope API Error: {resp.status_code} - {resp.text}")
        resp.raise_for_status()
        result = resp.json()
        
        if not result.get("choices") or len(result["choices"]) == 0:
            raise ValueError("DashScope: no choices in response")
        
        content = result["choices"][0]["message"]["content"]
        if not content:
            raise ValueError("DashScope returned empty content")
        return content

async def _call_mistral(messages):
    """Call Mistral Agent via raw httpx (using /v1/conversations endpoint)."""
    if not MISTRAL_API_KEY:
        raise ValueError("MISTRAL_API_KEY not configured")
    
    # Merge system prompts into first user message (agent_id conflict fix)
    system_content = "\n".join([m["content"] for m in messages if m["role"] == "system"])
    chat_history = [m.copy() for m in messages if m["role"] in ["user", "assistant"]]
    
    if system_content:
        for m in chat_history:
            if m["role"] == "user":
                m["content"] = f"## System Instructions & Context:\n{system_content}\n\n## User Input:\n{m['content']}"
                break
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MISTRAL_API_KEY}"
    }
    payload = {
        "agent_id": MISTRAL_AGENT_ID,
        "agent_version": 2,
        "inputs": chat_history
    }
    
    timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            "https://api.mistral.ai/v1/conversations",
            headers=headers,
            json=payload
        )
        if resp.status_code != 200:
            logger.error(f"Mistral API Error: {resp.status_code} - {resp.text}")
        resp.raise_for_status()
        result = resp.json()
        
        # Mistral Agent conversation schema is slightly different
        outputs = result.get("outputs", [])
        if not outputs:
            raise ValueError("Mistral returned no outputs")
        
        content = outputs[0].get("content")
        if not content:
            raise ValueError("Mistral returned empty content")
        return content

async def _call_mistral_stream(messages, max_tokens=600, temperature=0.4, top_p=1.0):
    """Stream response from Mistral AI."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MISTRAL_API_KEY}"
    }

    # Similar agent conflict fix as above
    system_content = "\n".join([m["content"] for m in messages if m["role"] == "system"])
    chat_history = [m.copy() for m in messages if m["role"] in ["user", "assistant"]]
    if system_content:
        for m in chat_history:
            if m["role"] == "user":
                m["content"] = f"## System Instructions & Context:\n{system_content}\n\n## User Input:\n{m['content']}"
                break

    payload = {
        "agent_id": MISTRAL_AGENT_ID,
        "agent_version": 2,
        "inputs": chat_history,
        "stream": True
    }
    
    timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", "https://api.mistral.ai/v1/conversations", headers=headers, json=payload) as response:
            if response.status_code != 200:
                error_text = (await response.aread()).decode("utf-8", errors="replace")
                logger.error(f"Mistral Stream Error: {response.status_code} - {error_text}")
                yield f"data: {json.dumps({'error': 'Mistral API Error'})}\n\n"
                return

            async for line in response.aiter_lines():
                yield f"{line}\n"

async def _call_dashscope_stream(messages, max_tokens=600, temperature=0.4, top_p=1.0, use_fallback=False):
    """Stream response from DashScope."""
    model = DASHSCOPE_FALLBACK_MODEL if use_fallback else DASHSCOPE_MODEL
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
        "X-DashScope-SSE": "enable" 
    }
    payload = {
        "model": model,
        "input": {"messages": messages},
        "parameters": {
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "incremental_output": True
        }
    }
    
    timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", f"{DASHSCOPE_API_BASE}/services/aigc/text-generation/generation", headers=headers, json=payload) as response:
            if response.status_code != 200:
                error_text = (await response.aread()).decode("utf-8", errors="replace")
                logger.error(f"DashScope Stream Error: {response.status_code} - {error_text}")
                yield f"data: {json.dumps({'error': 'DashScope API Error'})}\n\n"
                return

            async for line in response.aiter_lines():
                yield f"{line}\n"

# --- Endpoints ---

@app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": time.time()}

@app.post("/handshake")
@limiter.limit("5/minute")
async def handshake(request: Request, app_key: str = Depends(verify_app_key)):
    return {"status": "ok", "message": "Handshake successful"}

from fastapi.responses import StreamingResponse

@app.post("/proxy/ai")
@limiter.limit("30/minute")
async def proxy_ai(request: Request, body: ChatRequest, app_key: str = Depends(verify_app_key)):
    """Routes AI calls. mode=single: Mistral primary. mode=parallel: race both AIs."""
    
    if body.stream:
        return await _stream_ai(body)
    elif body.mode == "parallel":
        return await _parallel_ai(body)
    else:
        return await _single_ai(body)

async def _parallel_ai(body: ChatRequest):
    """Race Mistral and DashScope simultaneously, pick first success."""
    tasks = []
    
    if MISTRAL_API_KEY and "your_" not in MISTRAL_API_KEY:
        tasks.append(_safe_call("mistral", _call_mistral(body.messages)))
    if DASHSCOPE_API_KEY:
        tasks.append(_safe_call("dashscope", _call_dashscope(body.messages, body.max_tokens, body.temperature)))
    
    if not tasks:
        raise HTTPException(status_code=502, detail="No AI providers configured")
    
    logger.info(f"Proxy: Parallel mode — racing {len(tasks)} AI providers...")
    results = await asyncio.gather(*tasks)
    
    # Filter successful results
    successes = [(src, content) for src, content in results if content is not None]
    
    if not successes:
        raise HTTPException(status_code=502, detail="All AI providers failed in parallel mode")
    
    # Pick longest response (usually better quality for summaries)
    best_source, best_content = max(successes, key=lambda x: len(x[1]))
    logger.info(f"Proxy: Parallel winner = {best_source} ({len(best_content)} chars)")
    return {"content": best_content, "source": best_source, "mode": "parallel"}

async def _safe_call(source: str, coro):
    """Wrap an AI call to return (source, content) or (source, None) on error."""
    try:
        content = await coro
        return (source, content)
    except Exception as e:
        logger.warning(f"Parallel {source} failed: {e}")
        return (source, None)

async def _stream_ai(body: ChatRequest):
    """Handle streaming requests (Web Chat SSE)."""
    # 1. Try Mistral Stream
    if MISTRAL_API_KEY and "your_" not in MISTRAL_API_KEY:
        try:
            logger.info("Proxy: Streaming Mistral AI (Primary)...")
            return StreamingResponse(
                _call_mistral_stream(body.messages, body.max_tokens, body.temperature, body.top_p),
                media_type="text/event-stream"
            )
        except Exception as e:
            logger.warning(f"Mistral stream failed: {e}. Falling back to DashScope...")

    # 2. Try DashScope Stream
    if DASHSCOPE_API_KEY:
        try:
            logger.info(f"Proxy: Streaming DashScope ({DASHSCOPE_MODEL})...")
            return StreamingResponse(
                _call_dashscope_stream(body.messages, body.max_tokens, body.temperature, body.top_p),
                media_type="text/event-stream"
            )
        except Exception as e:
            logger.warning(f"DashScope stream failed: {e}")
            raise HTTPException(status_code=502, detail="Streaming failed for all providers")

    raise HTTPException(status_code=502, detail="No AI providers configured for streaming")

async def _single_ai(body: ChatRequest):
    """Mistral primary, DashScope fallback."""
    # 1. Try Mistral (Primary — European model, less censorship)
    if MISTRAL_API_KEY and "your_" not in MISTRAL_API_KEY:
        try:
            logger.info("Proxy: Calling Mistral AI (Primary)...")
            content = await _call_mistral(body.messages)
            return {"content": content, "source": "mistral", "mode": "single"}
        except Exception as e:
            logger.warning(f"Mistral failed: {e}. Falling back to DashScope...")

    # 2. Try DashScope (Fallback)
    if DASHSCOPE_API_KEY:
        try:
            logger.info(f"Proxy: Calling DashScope ({DASHSCOPE_MODEL})...")
            content = await _call_dashscope(body.messages, body.max_tokens, body.temperature)
            return {"content": content, "source": "dashscope", "mode": "single"}
        except Exception as e:
            logger.warning(f"DashScope failed: {e}")
            try:
                logger.info(f"Proxy: Trying DashScope fallback ({DASHSCOPE_FALLBACK_MODEL})...")
                content = await _call_dashscope(body.messages, body.max_tokens, body.temperature, use_fallback=True)
                return {"content": content, "source": "dashscope_fallback", "mode": "single"}
            except Exception as fe:
                logger.warning(f"DashScope fallback also failed: {fe}")

    raise HTTPException(status_code=502, detail="All AI providers failed")

@app.post("/proxy/line/push")
@limiter.limit("60/minute")
async def proxy_line_push(request: Request, body: LinePushRequest, app_key: str = Depends(verify_app_key)):
    """Routes LINE Push notifications."""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        raise HTTPException(status_code=500, detail="LINE_CHANNEL_ACCESS_TOKEN not configured")

    target_user = body.user_id or os.getenv("LINE_USER_ID", "")
    if not target_user:
        raise HTTPException(status_code=400, detail="No target user ID")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {
        "to": target_user,
        "messages": [{"type": "text", "text": body.text[:5000]}]
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload)
        if resp.status_code != 200:
            logger.error(f"LINE Push Error: {resp.status_code} - {resp.text}")
            raise HTTPException(status_code=resp.status_code, detail=f"LINE API Error: {resp.text}")
        return {"status": "ok"}

@app.post("/proxy/line/reply")
@limiter.limit("60/minute")
async def proxy_line_reply(request: Request, body: LineReplyRequest, app_key: str = Depends(verify_app_key)):
    """Routes LINE Reply messages."""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        raise HTTPException(status_code=500, detail="LINE_CHANNEL_ACCESS_TOKEN not configured")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {
        "replyToken": body.reply_token,
        "messages": [{"type": "text", "text": body.text[:5000]}]
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post("https://api.line.me/v2/bot/message/reply", headers=headers, json=payload)
        if resp.status_code != 200:
            logger.error(f"LINE Reply Error: {resp.status_code} - {resp.text}")
            raise HTTPException(status_code=resp.status_code, detail=f"LINE API Error: {resp.text}")
        return {"status": "ok"}

class LineWebhookRequest(BaseModel):
    webhook_url: str

@app.put("/proxy/line/webhook")
@limiter.limit("5/minute")
async def proxy_line_webhook(request: Request, body: LineWebhookRequest, app_key: str = Depends(verify_app_key)):
    """Updates LINE webhook URL via LINE Platform API."""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        raise HTTPException(status_code=500, detail="LINE_CHANNEL_ACCESS_TOKEN not configured")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {"endpoint": body.webhook_url}

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.put("https://api.line.me/v2/bot/channel/webhook/endpoint", headers=headers, json=payload)
        if resp.status_code != 200:
            logger.error(f"LINE Webhook Update Error: {resp.status_code} - {resp.text}")
            raise HTTPException(status_code=resp.status_code, detail=f"LINE API Error: {resp.text}")
        logger.info(f"LINE Webhook updated to: {body.webhook_url}")
        return {"status": "ok", "webhook_url": body.webhook_url}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
