import hashlib
import hmac
import base64
import json
import logging
import os
import asyncio
import csv
from datetime import datetime
from typing import Dict, List, Any

import httpx
from fastapi import APIRouter, Request, HTTPException

from core import state
from ai_analyzer import generate_line_chat_response

router = APIRouter(prefix="/api/line", tags=["LINE Webhook"])
logger = logging.getLogger("PM2230_API")

LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.getenv("LINE_USER_ID", "") # Original User ID for fallback push

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"

def verify_signature(body: bytes, signature: str) -> bool:
    """Verifies that the request came from LINE Platform."""
    if not LINE_CHANNEL_SECRET:
        logger.warning("LINE_CHANNEL_SECRET not set, skipping verification (NOT RECOMMENDED)")
        return True
    
    hash_obj = hmac.new(
        LINE_CHANNEL_SECRET.encode('utf-8'),
        body,
        hashlib.sha256
    )
    expected_signature = base64.b64encode(hash_obj.digest()).decode('utf-8')
    return hmac.compare_digest(expected_signature, signature)

def _load_recent_faults_simple(limit: int = 5) -> List[Dict]:
    """Simplified version of loading faults for AI context."""
    faults = []
    try:
        if os.path.exists(state.fault_log_filename):
            with open(state.fault_log_filename, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                all_rows = list(reader)
                for row in reversed(all_rows):
                    if len(faults) >= limit:
                        break
                    faults.append(row)
    except Exception as e:
        logger.error(f"Error loading faults in webhook: {e}")
    return faults

async def _line_reply(reply_token: str, text: str) -> bool:
    """Sends a reply message via LINE Reply API."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}]
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(LINE_REPLY_URL, headers=headers, json=payload)
            if resp.status_code == 200:
                return True
            else:
                logger.error(f"LINE Reply failed: {resp.status_code} {resp.text}")
                return False
    except Exception as e:
        logger.error(f"LINE Reply exception: {e}")
        return False

async def _line_push(user_id: str, text: str):
    """Fallback: sends a push message via LINE Push API."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {
        "to": user_id,
        "messages": [{"type": "text", "text": text}]
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(LINE_PUSH_URL, headers=headers, json=payload)
            if resp.status_code != 200:
                logger.error(f"LINE Push fallback failed: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.error(f"LINE Push exception: {e}")

import re

def _strip_markdown(text: str) -> str:
    """Remove markdown formatting for LINE readability."""
    text = re.sub(r'#{1,6}\s*', '', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'^[-*]\s+', '• ', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

async def _process_and_reply(user_msg: str, reply_token: str, user_id: str):
    """Processes user message with lightweight AI and replies to LINE."""
    current_data = state.cached_data if state.cached_data else {}
    recent_faults = _load_recent_faults_simple()

    try:
        ai_response = await asyncio.wait_for(
            generate_line_chat_response(user_msg, current_data, recent_faults),
            timeout=25.0
        )
        ai_response = _strip_markdown(ai_response)
    except asyncio.TimeoutError:
        ai_response = "⏳ AI ประมวลผลนานเกินไป ลองใหม่อีกครั้งนะครับ"
    except Exception as e:
        logger.error(f"Error in LINE Chat process: {e}")
        ai_response = "❌ เกิดข้อผิดพลาด กรุณาลองใหม่ครับ"

    if len(ai_response) > 4900:
        ai_response = ai_response[:4900] + "\n\n... (ข้อมูลยาวเกินไป)"

    success = await _line_reply(reply_token, ai_response)
    if not success:
        logger.info(f"Falling back to Push API for user {user_id}")
        await _line_push(user_id, ai_response)

@router.post("/webhook")
async def line_webhook(request: Request):
    """Endpoint for LINE Messaging API Webhook."""
    body = await request.body()
    signature = request.headers.get("X-Line-Signature", "")

    if not verify_signature(body, signature):
        logger.warning("Invalid LINE Webhook signature")
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        data = json.loads(body)
        events = data.get("events", [])
        for event in events:
            if event["type"] == "message" and event["message"]["type"] == "text":
                user_msg = event["message"]["text"]
                reply_token = event["replyToken"]
                user_id = event["source"]["userId"]
                
                logger.info(f"Received LINE message from {user_id}: {user_msg}")
                
                # Execute AI process in background
                asyncio.create_task(_process_and_reply(user_msg, reply_token, user_id))
                
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error processing LINE webhook: {e}")
        return {"status": "error", "message": str(e)}

async def set_line_webhook(webhook_url: str) -> bool:
    """สั่ง LINE Platform ให้ส่ง Webhook มาที่ URL นี้อัตโนมัติ"""
    url = "https://api.line.me/v2/bot/channel/webhook/endpoint"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {"endpoint": webhook_url}
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.put(url, headers=headers, json=payload)
            if resp.status_code == 200:
                logger.info(f"✅ Auto-updated LINE Webhook to: {webhook_url}")
                return True
            else:
                logger.error(f"❌ Failed to auto-update LINE Webhook: {resp.status_code} {resp.text}")
                return False
    except Exception as e:
        logger.error(f"❌ Error during auto-updating LINE Webhook: {e}")
        return False
