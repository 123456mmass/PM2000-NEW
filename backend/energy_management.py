#!/usr/bin/env python3
"""
Energy Management Module
วิเคราะห์การใช้พลังงานและแนะนำวิธีการประหยัดพลังงาน
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
CACHE_TTL_SECONDS = int(os.getenv("EM_CACHE_TTL_SECONDS", "300"))  # Default 5 minutes
MAX_CACHE_SIZE = int(os.getenv("EM_CACHE_MAX_SIZE", "100"))  # Maximum cache entries

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

class EnergyManagement:
    """
    Energy Management Module
    วิเคราะห์การใช้พลังงานและแนะนำวิธีการประหยัดพลังงาน
    """
    
    def __init__(self, config_path: str = "energy_config.json"):
        """
        Initialize Energy Management Module
        
        Args:
            config_path: Path to the configuration file
        """
        self.config_path = config_path
        self.config = self._load_config()
        self.api_endpoint = os.getenv("DASHSCOPE_API_BASE", "https://coding-intl.dashscope.aliyuncs.com/v1")
        self.api_key = os.getenv("DASHSCOPE_API_KEY")
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
            logger.info("Energy Management: Using Proxy mode")
        else:
            if not self.api_endpoint:
                logger.warning("DASHSCOPE_API_BASE not configured")
            if not self.api_key:
                logger.warning("DASHSCOPE_API_KEY not configured")
            if not self.mistral_api_key:
                logger.warning("MISTRAL_API_KEY not configured")
        
    def _load_config(self) -> Dict:
        """
        Load configuration from file
        
        Returns:
            Configuration dictionary
        """
        default_config = {
            "energy_tariffs": {
                "peak": {"start": "09:00", "end": "22:00", "rate": 4.5},
                "off_peak": {"start": "22:00", "end": "09:00", "rate": 2.5}
            },
            "efficiency_targets": {
                "power_factor": 0.95,
                "thd_voltage": 5.0,
                "thd_current": 8.0
            },
            "savings_potential": {
                "power_factor_improvement": 0.1,
                "thd_reduction": 0.1,
                "load_balancing": 0.05
            }
        }
        
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as file:
                    config = json.load(file)
                    # Merge with default config
                    for key in default_config:
                        if key not in config:
                            config[key] = default_config[key]
                    return config
            except Exception as e:
                logger.error(f"Error loading config: {e}")
                return default_config
        else:
            try:
                with open(self.config_path, 'w', encoding='utf-8') as file:
                    json.dump(default_config, file, indent=4)
                logger.info(f"Created new config file at {self.config_path}")
                return default_config
            except Exception as e:
                logger.error(f"Error creating config file: {e}")
                return default_config
    
    def _save_config(self):
        """
        Save configuration to file
        """
        try:
            with open(self.config_path, 'w', encoding='utf-8') as file:
                json.dump(self.config, file, indent=4)
            logger.info(f"Saved config to {self.config_path}")
        except Exception as e:
            logger.error(f"Error saving config: {e}")
    
    def _get_current_tariff(self, current_time: Optional[datetime] = None) -> Dict:
        """
        Get the current energy tariff based on time
        
        Args:
            current_time: Current time (defaults to now)
            
        Returns:
            Current tariff information
        """
        if current_time is None:
            current_time = datetime.now()
        
        current_time_str = current_time.strftime("%H:%M")
        
        for tariff_name, tariff_info in self.config["energy_tariffs"].items():
            start_time = tariff_info["start"]
            end_time = tariff_info["end"]
            
            if start_time <= end_time:
                # Same day (e.g., 09:00 - 22:00)
                if start_time <= current_time_str <= end_time:
                    return {"name": tariff_name, **tariff_info}
            else:
                # Cross midnight (e.g., 22:00 - 09:00)
                if current_time_str >= start_time or current_time_str <= end_time:
                    return {"name": tariff_name, **tariff_info}
        
        # Default to off-peak if no match
        return {"name": "off_peak", **self.config["energy_tariffs"]["off_peak"]}
    
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

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10),
           retry=retry_if_exception(should_retry))
    async def _call_ai_api(self, messages: List[Dict[str, str]]) -> str:
        """
        Call AI API (Proxy, Mistral, or DashScope)
        """
        # --- PROXY MODE ---
        if self.proxy_url:
            logger.info("Calling AI via Proxy (parallel mode)...")
            headers = {
                "Content-Type": "application/json",
                "X-App-Key": self.proxy_app_key
            }
            payload = {
                "messages": messages,
                "max_tokens": 1500,
                "temperature": 0.2,
                "mode": "parallel"
            }
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(f"{self.proxy_url}/proxy/ai", headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                logger.info(f"Proxy AI response: source={data.get('source')}, mode={data.get('mode')}")
                return data["content"]

        # --- DIRECT MODE ---
        try:
            if self.mistral_client:
                logger.info("Calling Mistral AI (Primary)...")
                return await self._call_mistral_api(messages)
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
                return content
        except Exception as e:
            logger.error(f"Error calling AI API: {e}")
            raise e

    def calculate_energy_cost(self, data: Dict) -> Dict:
        """
        Calculate energy cost based on current usage
        
        Args:
            data: Input data dictionary
            
        Returns:
            Energy cost calculation result
        """
        try:
            # Get current tariff
            current_tariff = self._get_current_tariff()
            
            # Calculate energy cost
            kwh_total = data.get("kWh_Total", 0)
            power_total = data.get("P_Total", 0)  # in kW
            
            # Estimate cost for the current hour
            hourly_energy = power_total * 1  # kWh for one hour
            hourly_cost = hourly_energy * current_tariff["rate"]
            
            # Calculate daily cost (assuming same usage pattern)
            daily_energy = power_total * 24
            daily_cost = daily_energy * current_tariff["rate"]
            
            return {
                "status": "success",
                "current_tariff": current_tariff["name"],
                "current_rate": current_tariff["rate"],
                "hourly_energy": round(hourly_energy, 3),
                "hourly_cost": round(hourly_cost, 2),
                "daily_energy": round(daily_energy, 3),
                "daily_cost": round(daily_cost, 2),
                "total_energy": round(kwh_total, 2),
                "estimated_monthly_cost": round(daily_cost * 30, 2)
            }
        except Exception as e:
            logger.error(f"Error calculating energy cost: {e}")
            return {
                "status": "error",
                "message": str(e)
            }
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10),
           retry=retry_if_exception(should_retry))
    async def analyze_efficiency_with_ai(self, data: Dict) -> Dict:
        """
        Analyze energy efficiency using AI
        
        Args:
            data: Input data dictionary
            
        Returns:
            Efficiency analysis result from AI
        """
        try:
            # Create cache key
            data_hash = create_data_hash(data)
            cache_key = f"em_{data_hash[:8]}"
            
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
            
            logger.info(f"Cache MISS: {cache_key}... - calling AI API for efficiency analysis")
            
            # Get target values from config
            targets = self.config["efficiency_targets"]
            
            # Calculate current values
            pf_total = data.get("PF_Total", 0)
            thdv_avg = np.mean([data.get("THDv_L1", 0), data.get("THDv_L2", 0), data.get("THDv_L3", 0)])
            thdi_avg = np.mean([data.get("THDi_L1", 0), data.get("THDi_L2", 0), data.get("THDi_L3", 0)])
            v_unb = data.get("V_unb", 0)
            i_unb = data.get("I_unb", 0)
            
            # Prepare prompt for AI
            prompt = f"""
คุณคือผู้เชี่ยวชาญด้านวิศวกรรมไฟฟ้าที่คอยวิเคราะห์ประสิทธิภาพพลังงานจาก Power Meter (รุ่น PM2230)

โปรดวิเคราะห์ข้อมูลด้านล่างและให้คำแนะนำในการประหยัดพลังงาน โดยอ้างอิงตามมาตรฐานสากล (เช่น IEEE 519 สำหรับ Harmonics และ IEEE 1159 สำหรับ Power Quality) ให้มีโครงสร้างชัดเจนและกระชับ เป็นภาษาไทย

## หัวข้อรายงาน:
รายงานฉบับนี้วิเคราะห์ประสิทธิภาพพลังงานจากข้อมูลค่าเฉลี่ยของ Power Meter รุ่น PM2230
วันที่-เวลา: {datetime.now().strftime('%d/%m/%Y เวลา %H:%M:%S น.')}

--- (ใช้เส้นคั่น)

## รูปแบบที่ต้องการ:
1. **สรุปภาพรวมประสิทธิภาพพลังงาน** (สั้น กระชับ)
2. **การประเมินสถานะปัจจุบัน** (แรงดัน, Harmonic, Power Factor, Unbalance)
3. **การวิเคราะห์ศักยภาพการประหยัดพลังงาน** (ระบุสาเหตุและผลกระทบ)
4. **คำแนะนำเชิงเทคนิค** (ระบุลำดับความสำคัญ 1, 2, 3...)
***

## ข้อมูลปัจจุบัน (สรุปค่าเฉลี่ย):
- แรงดันเฉลี่ย: {data.get('V_LN_avg', 0)} V
- กระแสเฉลี่ย: {data.get('I_avg', 0)} A
- ความถี่: {data.get('Freq', 0)} Hz
- Power Factor: {pf_total}
- THD Voltage เฉลี่ย: {thdv_avg:.2f}%
- THD Current เฉลี่ย: {thdi_avg:.2f}%
- Voltage Unbalance: {v_unb:.2f}%
- Current Unbalance: {i_unb:.2f}%
- กำลังไฟฟ้ารวม: {data.get('P_Total', 0)} kW
- พลังงานสะสม: {data.get('kWh_Total', 0)} kWh

## เกณฑ์ประเมินและผลกระทบ (อ้างอิง วสท. และ กฟภ./กฟน.):
- **Voltage Unbalance**: ปกติ < 2%, เตือน 2-5%, อันตราย > 5% (อ้างอิง วสท. ผลกระทบ: มอเตอร์ร้อนเกินไป, ฉนวนเสื่อมสภาพเร็วขึ้น)
- **Harmonic Distortion (THDv/THDi)**: ปกติ THDv < 5%, เตือน 5-8%, อันตราย > 8% (อ้างอิงระบบจำหน่าย กฟภ. วสท. ผลกระทบ: เครื่องใช้ไฟฟ้าผิดปกติ, หม้อแปลงร้อน)
- **Power Factor**: ดี > 0.9, ปานกลาง 0.85-0.9, ต่ำ < 0.85 (อ้างอิง กฟภ./กฟน. เสี่ยงโดนปรับ kVARh)
- **Current Unbalance**: ปกติ < 2%, เตือน 2-5%, อันตราย > 5% (อ้างอิง วสท. ผลกระทบ: สายนิวทรัลมีความร้อนสูง, Breaker ทริป)

วิเคราะห์ประสิทธิภาพพลังงานและให้คำแนะนำการประหยัดพลังงานเชิงเทคนิคที่ปฏิบัติได้จริง
"""

            messages = [
                {
                    "role": "system",
                    "content": "You are a helpful electrical engineering assistant specializing in energy efficiency analysis. Always respond in Thai language with technical accuracy.\nCRITICAL INSTRUCTION: When advising on power quality, YOU MUST explicitly reference the following Thai Standards:\n- Voltage Sag/Swell: PEA/MEA standard allows ±10% variation from 230V.\n- THDv: EIT standard limits THDv to 5%.\n- Power Factor (PF): PEA/MEA requires PF >= 0.85 to avoid kVARh penalty.\n- Voltage Unbalance: EIT standard critical limit is 2-5%.\nFORMATTING: Use Markdown syntax. DO NOT use HTML tags like <br>."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]

            # Call AI API
            content = await self._call_ai_api(messages)
            
            # Save to cache
            save_to_cache(cache_key, json.dumps({
                "status": "success",
                "analysis": content,
                "is_cached": False,
                "cache_key": cache_key
            }))
            
            return {
                "status": "success",
                "analysis": content,
                "is_cached": False,
                "cache_key": cache_key
            }
        except Exception as e:
            logger.error(f"Error analyzing efficiency with AI: {e}")
            return {
                "status": "error",
                "message": str(e),
                "is_cached": False,
                "cache_key": cache_key
            }

    def analyze_efficiency(self, data: Dict) -> Dict:
        """
        Analyze energy efficiency and identify savings opportunities
        
        Args:
            data: Input data dictionary
            
        Returns:
            Efficiency analysis result
        """
        try:
            # Get target values from config
            targets = self.config["efficiency_targets"]
            savings_potential = self.config["savings_potential"]
            
            # Calculate current values
            pf_total = data.get("PF_Total", 0)
            thdv_avg = np.mean([data.get("THDv_L1", 0), data.get("THDv_L2", 0), data.get("THDv_L3", 0)])
            thdi_avg = np.mean([data.get("THDi_L1", 0), data.get("THDi_L2", 0), data.get("THDi_L3", 0)])
            v_unb = data.get("V_unb", 0)
            i_unb = data.get("I_unb", 0)
            
            # Calculate efficiency scores (0-100)
            pf_score = min(100, max(0, (pf_total / targets["power_factor"]) * 100))
            thdv_score = min(100, max(0, 100 - (thdv_avg / targets["thd_voltage"]) * 100))
            thdi_score = min(100, max(0, 100 - (thdi_avg / targets["thd_current"]) * 100))
            v_unb_score = min(100, max(0, 100 - v_unb))
            i_unb_score = min(100, max(0, 100 - i_unb))
            
            overall_score = np.mean([pf_score, thdv_score, thdi_score, v_unb_score, i_unb_score])
            
            # Calculate potential savings
            power_total = data.get("P_Total", 0)
            current_tariff = self._get_current_tariff()
            
            # Power factor improvement
            pf_improvement_kwh = power_total * (1 - pf_total) * savings_potential["power_factor_improvement"]
            pf_improvement_cost = pf_improvement_kwh * current_tariff["rate"] * 24 * 30
            
            # THD reduction
            thd_improvement_kwh = power_total * savings_potential["thd_reduction"]
            thd_improvement_cost = thd_improvement_kwh * current_tariff["rate"] * 24 * 30
            
            # Load balancing
            unbalance_improvement_kwh = power_total * savings_potential["load_balancing"]
            unbalance_improvement_cost = unbalance_improvement_kwh * current_tariff["rate"] * 24 * 30
            
            total_potential_savings = pf_improvement_cost + thd_improvement_cost + unbalance_improvement_cost
            
            # Generate recommendations
            recommendations = []
            
            if pf_total < targets["power_factor"]:
                recommendations.append({
                    "issue": "Low Power Factor",
                    "current": round(pf_total, 3),
                    "target": targets["power_factor"],
                    "action": "Install power factor correction capacitors",
                    "potential_savings": round(pf_improvement_cost, 2)
                })
            
            if thdv_avg > targets["thd_voltage"]:
                recommendations.append({
                    "issue": "High Voltage THD",
                    "current": round(thdv_avg, 2),
                    "target": targets["thd_voltage"],
                    "action": "Install harmonic filters or use equipment with better power quality",
                    "potential_savings": round(thd_improvement_cost, 2)
                })
            
            if thdi_avg > targets["thd_current"]:
                recommendations.append({
                    "issue": "High Current THD",
                    "current": round(thdi_avg, 2),
                    "target": targets["thd_current"],
                    "action": "Install harmonic filters or use equipment with better power quality",
                    "potential_savings": round(thd_improvement_cost, 2)
                })
            
            if v_unb > 2.0:
                recommendations.append({
                    "issue": "Voltage Unbalance",
                    "current": round(v_unb, 2),
                    "target": 2.0,
                    "action": "Redistribute single-phase loads across phases",
                    "potential_savings": round(unbalance_improvement_cost, 2)
                })
            
            return {
                "status": "success",
                "efficiency_scores": {
                    "power_factor": round(pf_score, 1),
                    "voltage_thd": round(thdv_score, 1),
                    "current_thd": round(thdi_score, 1),
                    "voltage_unbalance": round(v_unb_score, 1),
                    "current_unbalance": round(i_unb_score, 1),
                    "overall": round(overall_score, 1)
                },
                "current_values": {
                    "power_factor": round(pf_total, 3),
                    "voltage_thd": round(thdv_avg, 2),
                    "current_thd": round(thdi_avg, 2),
                    "voltage_unbalance": round(v_unb, 2),
                    "current_unbalance": round(i_unb, 2)
                },
                "targets": targets,
                "potential_savings": round(total_potential_savings, 2),
                "recommendations": recommendations
            }
        except Exception as e:
            logger.error(f"Error analyzing efficiency: {e}")
            return {
                "status": "error",
                "message": str(e)
            }
    
    def get_energy_savings_tips(self) -> Dict:
        """
        Get general energy savings tips
        
        Returns:
            Energy savings tips
        """
        tips = [
            {
                "title": "Optimize Power Factor",
                "description": "Improve power factor to reduce reactive power and lower energy losses.",
                "actions": [
                    "Install power factor correction capacitors",
                    "Use high-efficiency motors",
                    "Avoid operating equipment at low loads"
                ]
            },
            {
                "title": "Reduce Harmonics",
                "description": "Minimize harmonic distortion to improve power quality and efficiency.",
                "actions": [
                    "Install harmonic filters",
                    "Use equipment with active PFC",
                    "Avoid using non-linear loads during peak hours"
                ]
            },
            {
                "title": "Balance Loads",
                "description": "Distribute loads evenly across phases to reduce unbalance.",
                "actions": [
                    "Monitor phase currents regularly",
                    "Redistribute single-phase loads",
                    "Use automatic load balancers"
                ]
            },
            {
                "title": "Peak Shaving",
                "description": "Reduce energy consumption during peak hours to lower costs.",
                "actions": [
                    "Schedule high-power equipment for off-peak hours",
                    "Use energy storage systems",
                    "Implement demand response programs"
                ]
            },
            {
                "title": "Regular Maintenance",
                "description": "Perform regular maintenance to keep equipment running efficiently.",
                "actions": [
                    "Clean and lubricate motors regularly",
                    "Check electrical connections for tightness",
                    "Monitor equipment performance and replace worn components"
                ]
            }
        ]
        
        return {
            "status": "success",
            "tips": tips
        }
    
    async def close(self) -> None:
        """Release the underlying HTTP client. Called by the lifespan shutdown hook."""
        try:
            await self.client.aclose()
        except Exception as e:
            logger.warning(f"EnergyManagement.close(): {e}")

    def update_config(self, new_config: Dict) -> Dict:
        """
        Update configuration
        
        Args:
            new_config: New configuration dictionary
            
        Returns:
            Update result
        """
        try:
            # Validate and update config
            for key in new_config:
                if key in self.config:
                    if isinstance(new_config[key], dict):
                        self.config[key].update(new_config[key])
                    else:
                        self.config[key] = new_config[key]
            
            self._save_config()
            return {
                "status": "success",
                "message": "Configuration updated successfully"
            }
        except Exception as e:
            logger.error(f"Error updating config: {e}")
            return {
                "status": "error",
                "message": str(e)
            }

# Example usage
if __name__ == "__main__":
    # Initialize the energy management module
    em = EnergyManagement()
    
    # Example data
    example_data = {
        "P_Total": 10.5,
        "kWh_Total": 1500.0,
        "PF_Total": 0.85,
        "THDv_L1": 4.5,
        "THDv_L2": 4.7,
        "THDv_L3": 4.6,
        "THDi_L1": 7.5,
        "THDi_L2": 7.7,
        "THDi_L3": 7.6,
        "V_unb": 3.2,
        "I_unb": 2.8
    }
    
    # Calculate energy cost
    cost_result = em.calculate_energy_cost(example_data)
    print("Energy Cost:", cost_result)
    
    # Analyze efficiency
    efficiency_result = em.analyze_efficiency(example_data)
    print("Efficiency Analysis:", efficiency_result)
    
    # Get energy savings tips
    tips_result = em.get_energy_savings_tips()
    print("Energy Savings Tips:", tips_result)
