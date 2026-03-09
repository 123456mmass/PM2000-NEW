#!/usr/bin/env python3
"""
Predictive Maintenance Module with External AI Model
ใช้โมเดลภายนอกในการทำนายการบำรุงรักษา
"""

from typing import Dict, List, Optional
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging
import json
import os
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
import hashlib
import time

if os.getenv("PM2230_NO_RUST", "0") != "1":
    try:
        import pm2000_core
        HAS_RUST_CORE = True
    except ImportError:
        HAS_RUST_CORE = False
else:
    HAS_RUST_CORE = False

# ตั้งค่า Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Cache Configuration
CACHE_TTL_SECONDS = int(os.getenv("PM_CACHE_TTL_SECONDS", "300"))  # Default 5 minutes
MAX_CACHE_SIZE = int(os.getenv("PM_CACHE_MAX_SIZE", "100"))  # Maximum cache entries

# In-memory cache is now handled by pm2000_core if available
# Fallback local cache ONLY if Rust core is missing
_local_cache: Dict[str, tuple] = {}

def _round_for_cache(value):
    """ปัดค่า numeric ให้หยาบขึ้นเพื่อให้ cache hit ได้ง่ายขึ้น"""
    if isinstance(value, float):
        if abs(value) >= 100:
            return round(value, 0)
        elif abs(value) >= 1:
            return round(value, 1)
        else:
            return round(value, 2)
    return value


def create_data_hash(data: dict) -> str:
    """
    สร้าง hash จาก input data โดยไม่รวม timestamp
    ปัดค่า numeric ให้หยาบขึ้นเพื่อให้ cache hit ได้ง่ายขึ้น
    """
    data_copy = {
        k: _round_for_cache(v)
        for k, v in data.items()
        if k != 'timestamp'
    }
    return hashlib.md5(json.dumps(data_copy, sort_keys=True, default=str).encode()).hexdigest()


def get_from_cache(data_hash: str) -> Optional[str]:
    """
    ดึงข้อมูลจาก cache ถ้ายังไม่หมดอายุ (TTL)
    """
    if HAS_RUST_CORE:
        try:
            return pm2000_core.cache_get(data_hash)
        except Exception as e:
            logger.error(f"Rust cache_get error: {e}")

    # Fallback to local cache
    if data_hash in _local_cache:
        cached_result, cached_time = _local_cache[data_hash]
        if time.time() - cached_time < CACHE_TTL_SECONDS:
            logger.info(f"Local Cache HIT: {data_hash[:8]}...")
            return cached_result
        else:
            del _local_cache[data_hash]
    return None

def save_to_cache(data_hash: str, result: str) -> None:
    """
    บันทึกผลลัพธ์ลง cache พร้อมพารามิเตอร์ TTL
    """
    if HAS_RUST_CORE:
        try:
            pm2000_core.cache_set(data_hash, result, CACHE_TTL_SECONDS)
            logger.info(f"Rust Cache SAVE: {data_hash[:8]}... (TTL: {CACHE_TTL_SECONDS}s)")
            return
        except Exception as e:
            logger.error(f"Rust cache_set error: {e}")

    # Fallback to local cache
    if len(_local_cache) >= MAX_CACHE_SIZE:
        oldest_key = min(_local_cache.keys(), key=lambda k: _local_cache[k][1])
        del _local_cache[oldest_key]
    
    _local_cache[data_hash] = (result, time.time())
    logger.info(f"Local Cache SAVE: {data_hash[:8]}...")

def get_cache_stats() -> Dict[str, int]:
    """
    Returns cache statistics (for debugging/monitoring)
    """
    rust_size = 0
    if HAS_RUST_CORE:
        try:
            rust_size = pm2000_core.cache_size()
        except:
            pass

    current_time = time.time()
    valid_entries = sum(
        1 for _, cached_time in _local_cache.values()
        if current_time - cached_time < CACHE_TTL_SECONDS
    )
    total = rust_size + len(_local_cache)
    return {
        "total_entries": total,
        "valid_entries": rust_size + valid_entries,
        "expired_entries": len(_local_cache) - valid_entries
    }

def cleanup_expired_cache() -> int:
    """
    ลบ expired entries ออกจาก local fallback cache
    Rust cache handles TTL automatically.
    Returns number of entries removed
    """
    current_time = time.time()
    expired_keys = [
        key for key, (_, cached_time) in _local_cache.items()
        if current_time - cached_time >= CACHE_TTL_SECONDS
    ]
    for key in expired_keys:
        del _local_cache[key]
    if expired_keys:
        logger.info(f"Cache cleanup: removed {len(expired_keys)} expired entries")
    return len(expired_keys)

def clear_all_cache() -> int:
    """
    ล้างข้อมูล cache ทั้งหมด (สำหรับ forced refresh)
    """
    count = 0
    if HAS_RUST_CORE:
        try:
            count = pm2000_core.cache_size()
            pm2000_core.cache_clear()
        except:
            pass
    
    count += len(_local_cache)
    _local_cache.clear()
    logger.info(f"Cache CLEAR ALL: removed {count} entries")
    return count

class ExternalPredictiveMaintenance:
    """
    Predictive Maintenance Module with External AI Model
    ใช้โมเดลภายนอกในการทำนายการบำรุงรักษา
    """
    
    def __init__(self, api_endpoint: str = None, api_key: str = None):
        """
        Initialize External Predictive Maintenance Module
        
        Args:
            api_endpoint: Endpoint ของ API ภายนอก
            api_key: API key สำหรับการเข้าถึง API ภายนอก
        """
        self.api_endpoint = api_endpoint or os.getenv("DASHSCOPE_API_BASE", "https://coding-intl.dashscope.aliyuncs.com/v1")
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        self.mistral_api_key = os.getenv("MISTRAL_API_KEY")
        self.mistral_agent_id = os.getenv("MISTRAL_AGENT_ID", "ag_019cba5df5fe7113ab3b3164627ec5db")
        self.proxy_url = os.getenv("PROXY_URL", "").rstrip("/")
        self.proxy_app_key = os.getenv("PROXY_APP_KEY", "")
        self.client = httpx.AsyncClient()
        self.mistral_client = None
        
        # Skip SDK init if using Proxy mode
        if not self.proxy_url and self.mistral_api_key and "your_key" not in self.mistral_api_key:
            try:
                from mistralai import Mistral
                self.mistral_client = Mistral(api_key=self.mistral_api_key)
            except Exception as e:
                logger.error(f"Failed to initialize Mistral client: {e}")
        
        if self.proxy_url:
            logger.info("Predictive Maintenance: Using Proxy mode")
        else:
            if not self.api_endpoint:
                logger.warning("DASHSCOPE_API_BASE not configured")
            if not self.api_key:
                logger.warning("DASHSCOPE_API_KEY not configured")
            if not self.mistral_api_key:
                logger.warning("MISTRAL_API_KEY not configured")
    
    def should_retry(self, exception):
        """
        Determine if we should retry based on the exception type.
        Don't retry 4xx client errors (except 429 Rate Limit).
        """
        if isinstance(exception, httpx.HTTPStatusError):
            status_code = exception.response.status_code
            # Don't retry 4xx errors except 429 (Rate Limit)
            if 400 <= status_code < 500 and status_code != 429:
                return False
            # Retry 5xx server errors
            if 500 <= status_code < 600:
                return True
            return False
        # Retry network errors
        return True
    
    def return_ai_error(self, retry_state):
        exception = retry_state.outcome.exception()
        err_msg = str(exception)
        if hasattr(exception, "response") and exception.response is not None:
            try:
                err_msg += f" - {exception.response.text}"
            except:
                pass
        return f"❌ เกิดข้อผิดพลาดเชื่อมต่อ AI (ลอง {retry_state.attempt_number} ครั้ง): {err_msg}"
    
    async def _call_mistral_api(self, messages: List[Dict[str, str]]) -> str:
        """
        Internal helper to call Mistral AI Agents using the official SDK.
        Handles 'agent_id' instruction conflict by merging system prompts into user messages.
        """
        if not self.mistral_client:
            raise ValueError("Mistral client not initialized (check MISTRAL_API_KEY)")

        # Mistral Agent Conflict Fix:
        # 1. If agent_id is used, 'instructions' parameter cannot be passed (422 error).
        # 2. 'inputs' must be 'user' or 'assistant' only.
        # 3. SOLUTION: Extract 'system' content and prepend it to the FIRST 'user' message.
        
        system_content = "\n".join([m["content"] for m in messages if m["role"] == "system"])
        chat_history = [m.copy() for m in messages if m["role"] in ["user", "assistant"]]
        
        # Prepend system context to the first user message
        if system_content:
            for m in chat_history:
                if m["role"] == "user":
                    m["content"] = f"## System Instructions & Context:\n{system_content}\n\n## User Input:\n{m['content']}"
                    break
        
        try:
            # Call without 'instructions' to avoid conflict with agent_id
            response = await self.mistral_client.beta.conversations.start_async(
                agent_id=self.mistral_agent_id,
                inputs=chat_history
            )
            
            # Structure based on SDK response attribute access
            if not hasattr(response, "outputs") or not response.outputs:
                result = response.model_dump() if hasattr(response, "model_dump") else {}
                if not result.get("outputs"):
                    raise ValueError(f"Mistral SDK returned unexpected format: {response}")
                content = result["outputs"][0].get("text") or result["outputs"][0].get("content")
            else:
                content = response.outputs[0].text if hasattr(response.outputs[0], "text") else getattr(response.outputs[0], "content", None)

            if not content:
                raise ValueError("Mistral SDK returned empty content")
                
            return content

        except Exception as e:
            logger.error(f"Mistral SDK Error: {e}")
            raise e

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10),
           retry=retry_if_exception(should_retry))
    async def predict_maintenance(self, data: Dict) -> Dict:
        """
        ทำนายการบำรุงรักษาด้วยโมเดลภายนอก
        
        Args:
            data: ข้อมูลปัจจุบันจากอุปกรณ์
            
        Returns:
            ผลลัพธ์การทำนาย
        """
        if not self.proxy_url and not self.mistral_client and (not self.api_endpoint or not self.api_key):
            return {
                "status": "error",
                "message": "No AI model configured",
                "maintenance_needed": False,
                "confidence": 0.0
            }
        
        # สร้าง cache key
        data_hash = create_data_hash(data)
        cache_key = f"pm_{data_hash[:8]}"
        
        # Check cache first
        cached_result = get_from_cache(cache_key)
        if cached_result is not None:
            try:
                result = json.loads(cached_result)
                return {
                    **result,
                    "is_cached": True,
                    "cache_key": cache_key
                }
            except Exception as e:
                logger.error(f"Error parsing cached result: {e}")
                # Remove corrupted cache entry
                if HAS_RUST_CORE:
                    pm2000_core.cache_delete(cache_key)
                if cache_key in _local_cache:
                    del _local_cache[cache_key]
        
        logger.info(f"Cache MISS: {cache_key}... - calling AI API")
        
        # เตรียมข้อมูลสำหรับส่งไปยัง AI API
        anomalies = []
        thdv_avg = (data.get("THDv_L1", 0) + data.get("THDv_L2", 0) + data.get("THDv_L3", 0)) / 3
        if thdv_avg > 5:
            anomalies.append(f"⚠️ THD Voltage สูง ({thdv_avg:.2f}%)")
        
        voltage_unbalance = data.get("V_unb", 0)
        if voltage_unbalance > 3:
            anomalies.append(f"⚠️ Voltage Unbalance ({voltage_unbalance:.2f}%)")
        
        power_factor = data.get("PF_Total", 1.0)
        if power_factor < 0.85:
            anomalies.append(f"⚠️ PF ต่ำ ({power_factor:.3f})")
        
        anomaly_text = "\n".join(anomalies) if anomalies else "✅ ปกติ (ไม่มี Anomaly Alert)"
        
        # เตรียมข้อมูลสำหรับส่งไปยัง AI API
        prompt = f"""
คุณคือผู้เชี่ยวชาญด้านวิศวกรรมไฟฟ้าที่คอยวิเคราะห์ข้อมูลจาก Power Meter (รุ่น PM2230)

โปรดวิเคราะห์ข้อมูลด้านล่างและทำนายความต้องการในการบำรุงรักษา (Predictive Maintenance) โดยอ้างอิงตามมาตรฐานสากล (เช่น IEEE 519 สำหรับ Harmonics และ IEEE 1159 สำหรับ Power Quality) ให้มีโครงสร้างชัดเจนและกระชับ เป็นภาษาไทย

## หัวข้อรายงาน:
รายงานฉบับนี้วิเคราะห์จากข้อมูลค่าเฉลี่ยของ Power Meter รุ่น PM2230
วันที่-เวลา: {datetime.now().strftime('%d/%m/%Y เวลา %H:%M:%S น.')}

--- (ใช้เส้นคั่น)

## รูปแบบที่ต้องการ:
1. **สรุปภาพรวม** (สั้น กระชับ)
2. **การประเมินสถานะ** (แรงดัน, Harmonic, Power Factor)
3. **การทำนายความต้องการบำรุงรักษา** (ระบุสาเหตุที่เป็นไปได้และผลกระทบ)
4. **คำแนะนำ** (ระบุลำดับความสำคัญ 1, 2, 3...)
***

## รายการแจ้งเตือนเบื้องต้นจากระบบ (Anomaly Detection):
{anomaly_text}

## ข้อมูลปัจจุบัน (สรุปค่าเฉลี่ย):
- แรงดันเฉลี่ย: {data.get('V_LN_avg', 0)} V
- กระแสเฉลี่ย: {data.get('I_avg', 0)} A
- ความถี่: {data.get('Freq', 0)} Hz
- Power Factor: {data.get('PF_Total', 0)}
- THD Voltage เฉลี่ย: {thdv_avg:.2f}%
- Voltage Unbalance: {voltage_unbalance:.2f}%
- กำลังไฟฟ้ารวม: {data.get('P_Total', 0)} kW
- พลังงานสะสม: {data.get('kWh_Total', 0)} kWh

## เกณฑ์ประเมินและผลกระทบ (อ้างอิง วสท. และ กฟภ./กฟน.):
- **Voltage Unbalance**: ปกติ < 2%, เตือน 2-5%, อันตราย > 5% (อ้างอิง วสท. ผลกระทบ: มอเตอร์ร้อนเกินไป, ฉนวนเสื่อมสภาพเร็วขึ้น)
- **Harmonic Distortion (THDv/THDi)**: ปกติ THDv < 5%, เตือน 5-8%, อันตราย > 8% (อ้างอิงระบบจำหน่าย กฟภ. วสท. ผลกระทบ: เครื่องใช้ไฟฟ้าผิดปกติ, หม้อแปลงร้อน)
- **Power Factor**: ดี > 0.9, ปานกลาง 0.85-0.9, ต่ำ < 0.85 (อ้างอิง กฟภ./กฟน. เสี่ยงโดนปรับ kVARh)

ทำนายความต้องการในการบำรุงรักษาและให้คำแนะนำการแก้ไขเชิงเทคนิคที่ปฏิบัติได้จริง
"""

        messages = [
            {
                "role": "system",
                "content": "You are a helpful electrical engineering assistant specializing in predictive maintenance analysis. Always respond in Thai language with technical accuracy.\nCRITICAL INSTRUCTION: When advising on power quality, YOU MUST explicitly reference the following Thai Standards:\n- Voltage Sag/Swell: PEA/MEA standard allows ±10% variation from 230V.\n- THDv: EIT standard limits THDv to 5%.\n- Power Factor (PF): PEA/MEA requires PF >= 0.85 to avoid kVARh penalty.\n- Voltage Unbalance: EIT standard critical limit is 2-5%.\nFORMATTING: Use Markdown syntax. DO NOT use HTML tags like <br>."
            },
            {
                "role": "user",
                "content": prompt
            }
        ]

        try:
            # --- PROXY MODE ---
            if self.proxy_url:
                logger.info("Calling AI via Proxy (parallel mode)...")
                proxy_headers = {
                    "Content-Type": "application/json",
                    "X-App-Key": self.proxy_app_key
                }
                proxy_payload = {
                    "messages": messages,
                    "max_tokens": 1500,
                    "temperature": 0.2,
                    "mode": "parallel"
                }
                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.post(f"{self.proxy_url}/proxy/ai", headers=proxy_headers, json=proxy_payload)
                    resp.raise_for_status()
                    proxy_data = resp.json()
                    content = proxy_data["content"]
                    ai_source = proxy_data.get("source", "proxy")
                    logger.info(f"Proxy AI response: source={ai_source}")
            # --- DIRECT MODE ---
            elif self.mistral_client:
                logger.info("Calling Mistral AI (Primary)...")
                content = await self._call_mistral_api(messages)
                ai_source = "Mistral"
            else:
                logger.info("Calling DashScope AI (Fallback)...")
                payload = {
                    "messages": messages,
                    "model": os.getenv("DASHSCOPE_MODEL", "qwen3.5-plus"),
                    "temperature": 0.2,
                    "max_tokens": 1500
                }
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                }
                response = await self.client.post(
                    f"{self.api_endpoint}/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=30.0
                )
                response.raise_for_status()
                result = response.json()
                if not result.get("choices") or len(result["choices"]) == 0:
                    raise ValueError("Invalid DashScope API response: no choices")
                content = result["choices"][0]["message"]["content"]
                if not content:
                    raise ValueError("DashScope returned empty content")
                ai_source = "DashScope"

            # บันทึกผลลัพธ์ลง cache
            save_to_cache(cache_key, json.dumps({
                "status": "success",
                "maintenance_needed": "ต้องการการบำรุงรักษา" in content or "อันตราย" in content or "เตือน" in content,
                "confidence": 0.9 if ("ต้องการการบำรุงรักษา" in content or "อันตราย" in content) else 0.7 if "เตือน" in content else 0.3,
                "message": content,
                "details": {
                    "model": ai_source,
                    "tokens_used": 0
                }
            }))

            return {
                "status": "success",
                "maintenance_needed": "ต้องการการบำรุงรักษา" in content or "อันตราย" in content or "เตือน" in content,
                "confidence": 0.9 if ("ต้องการการบำรุงรักษา" in content or "อันตราย" in content) else 0.7 if "เตือน" in content else 0.3,
                "message": content,
                "details": {
                    "model": ai_source,
                    "tokens_used": 0
                },
                "is_cached": False,
                "cache_key": cache_key
            }
        except Exception as e:
            logger.error(f"Error calling AI API: {e}")
            return {
                "status": "error",
                "message": str(e),
                "maintenance_needed": False,
                "confidence": 0.0,
                "is_cached": False,
                "cache_key": cache_key
            }
    
    async def close(self):
        """ปิดการเชื่อมต่อ"""
        await self.client.aclose()

# ตัวอย่างการใช้งาน
async def example_usage():
    # Initialize the external predictive maintenance module
    pm = ExternalPredictiveMaintenance(
        api_endpoint="https://api.example.com/predictive-maintenance",
        api_key="your_api_key_here"
    )
    
    # Example data
    example_data = {
        "V_LN_avg": 230.0,
        "I_avg": 10.0,
        "Freq": 50.0,
        "PF_Total": 0.95,
        "THDv_L1": 2.0,
        "THDv_L2": 2.1,
        "THDv_L3": 2.2,
        "THDi_L1": 5.0,
        "THDi_L2": 5.1,
        "THDi_L3": 5.2,
        "V_unb": 1.5,
        "I_unb": 1.2,
        "P_Total": 10.5,
        "kWh_Total": 1500.0
    }
    
    # Make prediction
    result = await pm.predict_maintenance(example_data)
    print("Prediction Result:", result)
    
    # Close the client
    await pm.close()

if __name__ == "__main__":
    import asyncio
    asyncio.run(example_usage())
