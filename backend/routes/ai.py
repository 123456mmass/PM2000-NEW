from collections import deque
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
import logging
import copy
import os
import json
import hashlib
import numpy as np
import asyncio
from typing import Dict
from datetime import datetime

from core import state
from core.security import ai_rate_limit, rate_limit
from services.modbus_service import get_latest_data

# AI models & logic
from ai_analyzer import (
    generate_power_summary,
    generate_english_report,
    generate_chat_response,
    stream_chat_response,
    generate_power_summary_parallel,
    clear_all_cache,
    robust_ai_call
)
from predictive_maintenance_external import create_data_hash

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger("PM2230_API")
AI_SUMMARY_SAMPLES = max(1, int(os.getenv("AI_SUMMARY_SAMPLES", "2")))
AI_SUMMARY_INTERVAL_SECONDS = max(0.0, float(os.getenv("AI_SUMMARY_INTERVAL_SECONDS", "0.15")))
AI_PARALLEL_DEFAULT_STRATEGY = os.getenv("AI_PARALLEL_DEFAULT_STRATEGY", "race").lower()
if AI_PARALLEL_DEFAULT_STRATEGY not in {"quality", "fastest", "ensemble", "race"}:
    AI_PARALLEL_DEFAULT_STRATEGY = "race"
AI_CHAT_FAULT_LIMIT = max(1, int(os.getenv("AI_CHAT_FAULT_LIMIT", "3")))

async def get_aggregated_data(samples: int = AI_SUMMARY_SAMPLES, interval: float = AI_SUMMARY_INTERVAL_SECONDS) -> Dict:
    """รวบรวมข้อมูลตามจำนวนตัวอย่างที่กำหนดแล้วหาค่าเฉลี่ย เพื่อลดความผันผวนของข้อมูล"""
    if samples <= 1:
        latest_data = copy.deepcopy(get_latest_data())
        if not latest_data:
            return {
                "timestamp": datetime.now().isoformat(),
                "status": "ERROR",
                "is_aggregated": False,
                "samples_count": 0,
            }

        latest_data["timestamp"] = datetime.now().isoformat()
        latest_data["is_aggregated"] = False
        latest_data["samples_count"] = 1
        return latest_data

    data_list = []
    logger.info(f"AI: Starting data aggregation ({samples} samples)...")

    for i in range(samples):
        data_list.append(get_latest_data())
        if interval > 0 and i < samples - 1:
            await asyncio.sleep(interval)

    if not data_list:
        return get_latest_data()

    avg_data = copy.deepcopy(data_list[0])

    numeric_fields = [
        'V_LN1', 'V_LN2', 'V_LN3', 'V_LN_avg', 'V_LL12', 'V_LL23', 'V_LL31', 'V_LL_avg',
        'I_L1', 'I_L2', 'I_L3', 'I_N', 'I_avg', 'Freq',
        'P_L1', 'P_L2', 'P_L3', 'P_Total', 'S_L1', 'S_L2', 'S_L3', 'S_Total',
        'Q_L1', 'Q_L2', 'Q_L3', 'Q_Total',
        'THDv_L1', 'THDv_L2', 'THDv_L3', 'THDi_L1', 'THDi_L2', 'THDi_L3',
        'V_unb', 'U_unb', 'I_unb', 'PF_L1', 'PF_L2', 'PF_L3', 'PF_Total'
    ]

    for field in numeric_fields:
        vals = [s.get(field, 0) for s in data_list if isinstance(s.get(field), (int, float))]
        if vals:
            avg_data[field] = round(sum(vals) / len(vals), 3)

    avg_data['timestamp'] = datetime.now().isoformat()
    avg_data['status'] = data_list[-1].get('status', 'ERROR') if data_list else 'ERROR'
    avg_data['is_aggregated'] = True
    avg_data['samples_count'] = len(data_list)

    logger.info(f"AI: Data aggregation complete ({len(data_list)} samples)")
    return avg_data


def load_recent_faults(limit: int = AI_CHAT_FAULT_LIMIT):
    recent_faults = []
    if not os.path.exists(state.fault_log_filename):
        return recent_faults

    try:
        with open(state.fault_log_filename, 'r', encoding='utf-8') as f:
            header_line = f.readline()
            if not header_line:
                return recent_faults

            header = header_line.strip().split(',')
            last_lines = deque(f, maxlen=limit)
            for line in last_lines:
                values = line.strip().split(',')
                record = {header[i]: values[i] if i < len(values) else "" for i in range(len(header))}
                recent_faults.append(record)
    except Exception as e:
        logger.error(f"Error reading fault log for chat: {e}")

    return recent_faults


@router.post("/ai-summary")
@ai_rate_limit
async def get_ai_summary(request: Request):
    aggregated_data = await get_aggregated_data()
    result = await generate_power_summary(aggregated_data)

    if result.get("is_cached"):
        logger.info(f"AI Summary returned from cache (key: {result.get('cache_key')})")
    else:
        logger.info(f"AI Summary generated fresh (key: {result.get('cache_key')})")

    return {
        "summary": result.get("summary", ""),
        "is_cached": result.get("is_cached", False),
        "cache_key": result.get("cache_key", ""),
        "is_aggregated": aggregated_data.get("is_aggregated", False),
        "samples": aggregated_data.get("samples_count", 1),
    }


@router.delete("/ai-summary")
@rate_limit
async def clear_ai_summary_cache(request: Request):
    count = clear_all_cache()
    return {"message": "Cache cleared successfully", "entries_removed": count}


@router.post("/ai-summary-parallel")
@ai_rate_limit
async def get_ai_summary_parallel(request: Request):
    query_params = dict(request.query_params)
    strategy = query_params.get("strategy", AI_PARALLEL_DEFAULT_STRATEGY)
    valid_strategies = ["quality", "fastest", "ensemble", "race"]
    if strategy not in valid_strategies:
        strategy = AI_PARALLEL_DEFAULT_STRATEGY

    aggregated_data = await get_aggregated_data()
    result = await generate_power_summary_parallel(aggregated_data, selection_strategy=strategy)

    response = {
        "summary": result.get("summary", ""),
        "is_cached": result.get("is_cached", False),
        "cache_key": result.get("cache_key", ""),
        "is_aggregated": aggregated_data.get("is_aggregated", False),
        "samples": aggregated_data.get("samples_count", 1),
        "parallel_mode": result.get("parallel_mode", False),
        "selected_provider": result.get("provider", "unknown"),
        "strategy": strategy,
    }

    if result.get("quality_score") is not None:
        response["quality_score"] = result["quality_score"]
    if result.get("latency") is not None:
        response["latency_seconds"] = result["latency"]
    if result.get("all_providers"):
        response["providers_compared"] = len(result["all_providers"])
        response["all_results"] = result["all_providers"]

    logger.info(
        f"Parallel AI Summary: {result.get('provider')} selected "
        f"(strategy={strategy}, latency={result.get('latency', 0):.2f}s)"
    )

    return response

@router.post("/ai-report/english")
@ai_rate_limit
async def get_ai_english_report(request: Request):
    latest_data = get_latest_data()
    result = await generate_english_report(latest_data)

    if result.get("is_cached"):
        logger.info(f"AI English Report returned from cache (key: {result.get('cache_key')})")
    else:
        logger.info(f"AI English Report generated fresh (key: {result.get('cache_key')})")

    return {
        "summary": result.get("summary", ""),
        "is_cached": result.get("is_cached", False),
        "cache_key": result.get("cache_key", "")
    }

@router.post("/ai-fault-summary")
@ai_rate_limit
async def get_ai_fault_summary(request: Request):
    if not os.path.exists(state.fault_log_filename):
        return {
            "summary": "❌ ไม่พบไฟล์ประวัติการเกิด Fault (ยังไม่มีข้อมูลฟอลต์ในระบบ)",
            "is_cached": False,
            "cache_key": ""
        }
        
    try:
        fault_records = []
        with open(state.fault_log_filename, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            if len(lines) <= 1:
                return {
                    "summary": "❌ ไฟล์ประวัติการเกิด Fault ว่างเปล่า",
                    "is_cached": False,
                    "cache_key": ""
                }
            
            header = lines[0].strip().split(',')
            last_records = lines[-10:] if len(lines) > 10 else lines[1:]
            
            for line in last_records:
                values = line.strip().split(',')
                record = {header[i]: values[i] if i < len(values) else "" for i in range(len(header))}
                fault_records.append(record)
        
        data_str = json.dumps(fault_records, sort_keys=True)
        cache_key = f"ai_flt_{hashlib.md5(data_str.encode()).hexdigest()[:8]}"
        
        from ai_analyzer import get_from_cache, save_to_cache
        cached_result = get_from_cache(cache_key)
        if cached_result is not None:
            logger.info(f"Cache HIT: {cache_key}... (fault summary parallel)")
            return {"summary": cached_result, "is_cached": True, "cache_key": cache_key}
        
        logger.info(f"Cache MISS: {cache_key}... - calling PARALLEL AI API for fault summary")
        
        prompt = f"""คุณคือผู้เชี่ยวชาญด้านวิศวกรรมไฟฟ้าที่คอยวิเคราะห์สาเหตุการเกิด Fault จาก Power Meter (รุ่น PM2230)

ด้านล่างนี้คือข้อมูลประวัติการเกิดความผิดปกติทางไฟฟ้า (Fault Records) จำนวน {len(fault_records)} รายการล่าสุด
โปรดวิเคราะห์ข้อมูลเหล่านี้และเขียนสรุปสาเหตุ/รูปแบบของการเกิด Fault โดยอ้างอิงตามมาตรฐานสากล (เช่น IEEE 1159 สำหรับ Power Quality) เพื่อให้วิศวกรซ่อมบำรุงเข้าใจง่าย เป็นภาษาไทย

## หัวข้อรายงาน:
รายงานฉบับนี้วิเคราะห์จากข้อมูลประวัติการเกิด Fault ของ Power Meter รุ่น PM2230
วันที่-เวลา: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (เวลาปัจจุบันที่วิเคราะห์)

---

## รูปแบบที่ต้องการ:
1. **ภาพรวมของเหตุการณ์ผิดปกติ** (เช่น เกิด Voltage Sag ถี่แค่ไหน, Phase ไหนมีปัญหาบ่อยสุด)
2. **การประเมินสาเหตุที่เป็นไปได้** (วิเคราะห์จากตัวเลข เช่น กระแสไม่สมดุลอาจเกิดจากโหลดเกิน, แรงดันตกอาจเกิดจากการสตาร์ทมอเตอร์)
3. **ผลกระทบที่อาจเกิดขึ้นต่ออุปกรณ์**
4. **คำแนะนำสำหรับการแก้ไขหรือตรวจสอบเพิ่มเติม**
***

## ข้อกำหนดในการวิเคราะห์ Fault:
- วิเคราะห์หาสาเหตุที่เป็นไปได้จากข้อมูลตัวเลข (เช่น แรงดันต่ำพร้อมกระแสสูงอาจหมายถึงการ Overload หรือ Starting)
- ระบุผลกระทบต่ออุปกรณ์ตามประเภทปัญหา:
  - **Voltage Unbalance**: มอเตอร์ร้อนเกินไป, ฉนวนเสื่อมสภาพเร็วขึ้น, ประสิทธิภาพมอเตอร์ลดลง, อุปกรณ์อิเล็กทรอนิกส์เสียหาย
  - **Voltage Sag/Dip**: เซนเซอร์/PLC/อุปกรณ์อิเล็กทรอนิกส์รีเซ็ต, มอเตอร์หยุดทำงานชั่วคราว, สูญเสียผลผลิตในกระบวนการผลิต
  - **Current Unbalance**: สายนิวทรัลมีความร้อนสูงเสี่ยงต่อการไหม้, อุปกรณ์ป้องกัน/Breaker ทำงานผิดปกติ, มอเตอร์เสียหายเร็วขึ้น
  - **Overload/Overcurrent**: สายไฟร้อนเกินไป, หม้อแปลงโอเวอร์โหลด, Breaker ทริป
- ตอบกลับเป็นภาษาไทยที่อ่านง่าย ใช้ markdown format (##, **, -, 1.)
- เน้นข้อความสำคัญด้วย **ตัวหนา**
- ไม่ต้องใช้ HTML tags เช่น <br>

## ข้อมูล Fault Records:
{json.dumps(fault_records, ensure_ascii=False, indent=2)}

วิเคราะห์สาเหตุและรูปแบบของการเกิด Fault จากข้อมูลด้านบน"""

        messages = [
            {"role": "system", "content": "You are an expert electrical engineer specializing in power quality analysis and fault diagnosis. Always respond in Thai language. FORMATTING: Use Markdown syntax. DO NOT use HTML tags like <br>."},
            {"role": "user", "content": prompt}
        ]
        try:
            content = await robust_ai_call(messages)
            
            save_to_cache(cache_key, content)
            
            return {"summary": content, "is_cached": False, "cache_key": cache_key, "provider": "robust_single"}
        except Exception as e:
            logger.error(f"Error in robust_ai_call for fault summary: {e}")
            return {"summary": "❌ ไม่สามารถเชื่อมต่อ AI ได้", "is_cached": False, "cache_key": cache_key}
        
    except Exception as e:
        logger.error(f"Error in get_ai_fault_summary (parallel): {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat")
@ai_rate_limit
async def ai_chat(request: Request):
    body = None
    try:
        body = await request.json()
        messages = body.get("messages", [])
        
        current_context = state.cached_data if state.cached_data else {}
        recent_faults = load_recent_faults()
        response_text = await generate_chat_response(messages, current_context, recent_faults)
        return {"response": response_text}

    except Exception as e:
        logger.error(f"Error in ai_chat: {e}", exc_info=True)
        if body is not None:
            logger.error(f"Request body: {body}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/stream")
@ai_rate_limit
async def ai_chat_stream(request: Request):
    body = None
    try:
        body = await request.json()
        messages = body.get("messages", [])
        current_context = state.cached_data if state.cached_data else {}
        recent_faults = load_recent_faults()

        async def event_stream():
            try:
                async for chunk in stream_chat_response(messages, current_context, recent_faults):
                    payload = json.dumps({"delta": chunk}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"
            except Exception as e:
                logger.error(f"Error while streaming chat response: {e}", exc_info=True)
                error_payload = json.dumps({"error": str(e)}, ensure_ascii=False)
                yield f"data: {error_payload}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception as e:
        logger.error(f"Error initializing chat stream: {e}", exc_info=True)
        if body is not None:
            logger.error(f"Request body: {body}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/predictive-maintenance")
@rate_limit
async def get_predictive_maintenance(request: Request):
    """ทำนายการบำรุงรักษาด้วย AI"""
    try:
        data = get_latest_data()
        if state.pm_model is None:
            raise HTTPException(status_code=500, detail="Predictive Maintenance model not initialized")
        
        result = state.pm_model.predict_maintenance(data)
        return result
    except Exception as e:
        logger.error(f"Error in get_predictive_maintenance: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/external-predictive-maintenance")
@rate_limit
async def get_external_predictive_maintenance(request: Request):
    """ทำนายการบำรุงรักษาด้วยโมเดลภายนอก (Parallel Mode)"""
    try:
        from predictive_maintenance_external import get_from_cache, save_to_cache
        
        data = get_latest_data()
        data_hash = create_data_hash(data)
        cache_key = f"pm_{data_hash[:8]}"
        
        cached_result = get_from_cache(cache_key)
        if cached_result is not None:
            try:
                result = json.loads(cached_result)
                logger.info(f"Cache HIT: {cache_key}... (predictive parallel)")
                return {**result, "is_cached": True, "cache_key": cache_key}
            except Exception as e:
                logger.error(f"Error parsing cached result: {e}")
        
        logger.info(f"Cache MISS: {cache_key}... - calling PARALLEL AI API for predictive maintenance")
        
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
        
        prompt = f"""คุณคือผู้เชี่ยวชาญด้านวิศวกรรมไฟฟ้าที่คอยวิเคราะห์ข้อมูลจาก Power Meter (รุ่น PM2230)

โปรดวิเคราะห์ข้อมูลด้านล่างและทำนายความต้องการในการบำรุงรักษา (Predictive Maintenance) โดยอ้างอิงตามมาตรฐานสากล (เช่น IEEE 519 สำหรับ Harmonics และ IEEE 1159 สำหรับ Power Quality) ให้มีโครงสร้างชัดเจนและกระชับ เป็นภาษาไทย

## หัวข้อรายงาน:
รายงานฉบับนี้วิเคราะห์จากข้อมูลค่าเฉลี่ยของ Power Meter รุ่น PM2230
วันที่-เวลา: {data.get('timestamp', 'N/A')}

---

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

## เกณฑ์ประเมินและผลกระทบ (อ้างอิง IEEE):
- **Voltage Unbalance**: ปกติ < 2%, เตือน 2-3%, อันตราย > 3% (ผลกระทบ: มอเตอร์ร้อนเกินไป, ฉนวนเสื่อมสภาพเร็วขึ้น, ประสิทธิภาพมอเตอร์ลดลง, อุปกรณ์อิเล็กทรอนิกส์เสียหาย)
- **Harmonic Distortion (THDv/THDi)**: ปกติ THDv < 5%, เตือน 5-8%, อันตราย > 8% (ผลกระทบ: เครื่องใช้ไฟฟ้า/PLC/Drive ผิดปกติ, หม้อแปลง/สายไฟร้อนเกินไป, สูญเสียพลังงานสูงขึ้น)
- **Power Factor**: ดี > 0.9, ปานกลาง 0.85-0.9, ต่ำ < 0.85

ทำนายความต้องการในการบำรุงรักษาและให้คำแนะนำการแก้ไขเชิงเทคนิคที่ปฏิบัติได้จริง"""

        messages = [
            {
                "role": "system",
                "content": "You are a helpful electrical engineering assistant specializing in predictive maintenance analysis. Always respond in Thai language with technical accuracy. FORMATTING: Use Markdown syntax. DO NOT use HTML tags like <br>."
            },
            {"role": "user", "content": prompt}
        ]
        
        content = await robust_ai_call(messages)
        
        result = {
            "status": "success",
            "maintenance_needed": "ต้องการการบำรุงรักษา" in content or "อันตราย" in content or "เตือน" in content,
            "confidence": 0.9 if ("ต้องการการบำรุงรักษา" in content or "อันตราย" in content) else 0.7 if "เตือน" in content else 0.3,
            "message": content,
            "provider": "single-fallback-proxy",
            "details": {
                "model": "single-fallback-proxy",
                "tokens_used": 0
            },
            "is_cached": False,
            "cache_key": cache_key
        }
        
        save_to_cache(cache_key, json.dumps(result))
        
        return result
            
    except Exception as e:
        logger.error(f"Error in get_external_predictive_maintenance (parallel): {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/predictive-maintenance/train")
@rate_limit
async def train_predictive_maintenance(request: Request):
    try:
        if state.pm_model is None:
            raise HTTPException(status_code=500, detail="Predictive Maintenance model not initialized")
        
        historical_data = []
        import csv
        if os.path.exists(state.log_filename):
            with open(state.log_filename, mode='r', encoding='utf-8') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    try:
                        data = {
                            "V_LN_avg": float(row.get("V_LN_avg", 0)),
                            "I_avg": float(row.get("I_avg", 0)),
                            "Freq": float(row.get("Freq", 0)),
                            "PF_Total": float(row.get("PF_Total", 0)),
                            "THDv_L1": float(row.get("THDv_L1", 0)),
                            "THDv_L2": float(row.get("THDv_L2", 0)),
                            "THDv_L3": float(row.get("THDv_L3", 0)),
                            "THDi_L1": float(row.get("THDi_L1", 0)),
                            "THDi_L2": float(row.get("THDi_L2", 0)),
                            "THDi_L3": float(row.get("THDi_L3", 0))
                        }
                        historical_data.append(data)
                    except Exception as e:
                        logger.warning(f"Error parsing row: {e}")
                        continue
        
        if not historical_data:
            raise HTTPException(status_code=400, detail="No historical data available for training")
        
        result = state.pm_model.train_model(historical_data)
        return result
    except Exception as e:
        logger.error(f"Error in train_predictive_maintenance: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/energy-cost")
@rate_limit
async def get_energy_cost(request: Request):
    try:
        data = get_latest_data()
        if state.em_model is None:
            raise HTTPException(status_code=500, detail="Energy Management model not initialized")
        
        result = state.em_model.calculate_energy_cost(data)
        return result
    except Exception as e:
        logger.error(f"Error in get_energy_cost: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/energy-efficiency")
@rate_limit
async def get_energy_efficiency(request: Request):
    try:
        data = get_latest_data()
        if state.em_model is None:
            raise HTTPException(status_code=500, detail="Energy Management model not initialized")
        
        result = state.em_model.analyze_efficiency(data)
        return result
    except Exception as e:
        logger.error(f"Error in get_energy_efficiency: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/energy-efficiency-ai")
@rate_limit
async def get_energy_efficiency_ai(request: Request):
    try:
        data = get_latest_data()
        
        data_copy = {k: v for k, v in data.items() if k != 'timestamp'}
        data_hash = hashlib.md5(json.dumps(data_copy, sort_keys=True, default=str).encode()).hexdigest()
        cache_key = f"em_{data_hash[:8]}"
        
        from energy_management import get_from_cache, save_to_cache
        cached_result = get_from_cache(cache_key)
        if cached_result is not None:
            try:
                result = json.loads(cached_result)
                logger.info(f"Cache HIT: {cache_key}... (parallel endpoint)")
                return {**result, "is_cached": True, "cache_key": cache_key}
            except Exception as e:
                logger.error(f"Error parsing cached result: {e}")
        
        logger.info(f"Cache MISS: {cache_key}... - calling PARALLEL AI API for efficiency analysis")
        
        pf_total = data.get("PF_Total", 0)
        thdv_avg = np.mean([data.get("THDv_L1", 0), data.get("THDv_L2", 0), data.get("THDv_L3", 0)])
        thdi_avg = np.mean([data.get("THDi_L1", 0), data.get("THDi_L2", 0), data.get("THDi_L3", 0)])
        v_unb = data.get("V_unb", 0)
        i_unb = data.get("I_unb", 0)
        
        prompt = f"""คุณคือผู้เชี่ยวชาญด้านวิศวกรรมไฟฟ้าที่คอยวิเคราะห์ประสิทธิภาพพลังงานจาก Power Meter (รุ่น PM2230)

โปรดวิเคราะห์ข้อมูลด้านล่างและให้คำแนะนำในการประหยัดพลังงาน โดยอ้างอิงตามมาตรฐานสากล (เช่น IEEE 519 สำหรับ Harmonics และ IEEE 1159 สำหรับ Power Quality) ให้มีโครงสร้างชัดเจนและกระชับ เป็นภาษาไทย

## หัวข้อรายงาน:
รายงานฉบับนี้วิเคราะห์ประสิทธิภาพพลังงานจากข้อมูลค่าเฉลี่ยของ Power Meter รุ่น PM2230
วันที่-เวลา: {data.get('timestamp', 'N/A')}

---

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

## เกณฑ์ประเมินและผลกระทบ (อ้างอิง IEEE):
- **Voltage Unbalance**: ปกติ < 2%, เตือน 2-3%, อันตราย > 3% (ผลกระทบ: มอเตอร์ร้อนเกินไป, ฉนวนเสื่อมสภาพเร็วขึ้น, ประสิทธิภาพมอเตอร์ลดลง, อุปกรณ์อิเล็กทรอนิกส์เสียหาย)
- **Harmonic Distortion (THDv/THDi)**: ปกติ THDv < 5%, เตือน 5-8%, อันตราย > 8% (ผลกระทบ: เครื่องใช้ไฟฟ้า/PLC/Drive ผิดปกติ, หม้อแปลง/สายไฟร้อนเกินไป, สูญเสียพลังงานสูงขึ้น)
- **Power Factor**: ดี > 0.9, ปานกลาง 0.85-0.9, ต่ำ < 0.85 (ผลกระทบ: กระแสสูงขึ้น, สูญเสียพลังงาน, ค่าไฟฟ้าสูงขึ้น)
- **Current Unbalance**: ปกติ < 2%, เตือน 2-3%, อันตราย > 3% (ผลกระทบ: สายนิวทรัลมีความร้อนสูงเสี่ยงต่อการไหม้, อุปกรณ์ป้องกัน/Breaker ทำงานผิดปกติ)

วิเคราะห์ประสิทธิภาพพลังงานและให้คำแนะนำการประหยัดพลังงานเชิงเทคนิคที่ปฏิบัติได้จริง"""

        messages = [
            {
                "role": "system",
                "content": "You are a helpful electrical engineering assistant specializing in energy efficiency analysis. Always respond in Thai language with technical accuracy. FORMATTING: Use Markdown syntax. DO NOT use HTML tags like <br>."
            },
            {"role": "user", "content": prompt}
        ]
        
        content = await robust_ai_call(messages)
        
        result = {
            "status": "success",
            "analysis": content,
            "provider": "single-fallback-proxy",
            "is_cached": False,
            "cache_key": cache_key
        }
        
        save_to_cache(cache_key, json.dumps(result))
        
        return result
            
    except Exception as e:
        logger.error(f"Error in get_energy_efficiency_ai: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/energy-tips")
@rate_limit
async def get_energy_tips(request: Request):
    try:
        if state.em_model is None:
            raise HTTPException(status_code=500, detail="Energy Management model not initialized")
        
        result = state.em_model.get_energy_savings_tips()
        return result
    except Exception as e:
        logger.error(f"Error in get_energy_tips: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/energy-config")
@rate_limit
async def update_energy_config(request: Request):
    try:
        if state.em_model is None:
            raise HTTPException(status_code=500, detail="Energy Management model not initialized")
        
        body = await request.json()
        result = state.em_model.update_config(body)
        return result
    except Exception as e:
        logger.error(f"Error in update_energy_config: {e}")
        raise HTTPException(status_code=500, detail=str(e))



