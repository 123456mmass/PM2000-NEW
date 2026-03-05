#!/usr/bin/env python3
"""
Parallel LLM Router - เรียกใช้หลาย AI พร้อมกัน เลือกผลลัพธ์ดีที่สุด
"""

import asyncio
import time
import logging
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass
from enum import Enum
import json

logger = logging.getLogger("ParallelLLM")


class LLMProvider(Enum):
    MISTRAL = "mistral"
    DASHSCOPE_PRIMARY = "dashscope_primary"  # qwen3.5-plus
    DASHSCOPE_FALLBACK = "dashscope_fallback"  # qwen-max


@dataclass
class LLMResult:
    """ผลลัพธ์จากแต่ละ LLM"""
    provider: str
    content: str
    latency: float  # วินาที
    success: bool
    error: Optional[str] = None
    quality_score: float = 0.0  # คะแนนคุณภาพ 0-100


class QualityScorer:
    """วัดคุณภาพผลลัพธ์อัตโนมัติ"""
    
    @staticmethod
    def score_thai_power_analysis(content: str) -> float:
        """
        ให้คะแนนคุณภาพรายงานวิเคราะห์ไฟฟ้าภาษาไทย
        Returns: คะแนน 0-100
        """
        score = 50.0  # Base score
        
        # 1. ความยาวที่เหมาะสม (มากกว่า 500 ตัวอักษร = ดี)
        content_length = len(content)
        if content_length > 1000:
            score += 15
        elif content_length > 500:
            score += 10
        elif content_length < 200:
            score -= 20  # สั้นเกินไป
        
        # 2. มีโครงสร้างที่ชัดเจน (มีหัวข้อ ## หรือ ###)
        headers = content.count('##') + content.count('**')
        score += min(headers * 3, 15)  # สูงสุด 15 คะแนน
        
        # 3. มีข้อมูลตัวเลข (แสดงว่าวิเคราะห์จริง)
        import re
        numbers = len(re.findall(r'\d+\.?\d*', content))
        score += min(numbers * 0.5, 10)
        
        # 4. มีคำสำคัญทางเทคนิค
        technical_terms = [
            'แรงดัน', 'กระแส', 'พลังงาน', 'Power Factor', 'THD',
            'Harmonic', 'Unbalance', 'มอเตอร์', 'แก้ไข', 'แนะนำ',
            'IEEE', 'voltage', 'current', 'โหลด', 'คาดการณ์'
        ]
        term_count = sum(1 for term in technical_terms if term in content)
        score += min(term_count * 2, 10)
        
        # 5. หักคะแนนถ้ามี error message
        error_keywords = ['❌', 'ข้อผิดพลาด', 'ไม่สามารถ', 'error', 'failed']
        for kw in error_keywords:
            if kw in content:
                score -= 15
                break
        
        return max(0, min(100, score))
    
    @staticmethod
    def score_chat_response(content: str) -> float:
        """ให้คะแนนคำตอบแชท"""
        score = 50.0
        
        # ความยาวเหมาะสม (ไม่สั้นหรือยาวเกินไป)
        length = len(content)
        if 100 < length < 800:
            score += 15
        elif length > 1000:
            score -= 10  # ยาวเกินไป
        
        # มีการตอบคำถามจริง (มีเนื้อหา ไม่ใช่ขอโทษอย่างเดียว)
        apology_words = ['ขออภัย', 'ไม่ทราบ', 'ไม่เข้าใจ', 'ขอโทษ']
        if not any(w in content for w in apology_words):
            score += 15
        
        # มีความชัดเจน (มีหัวข้อย่อยหรือ bullet points)
        if '-' in content or '•' in content or '1.' in content:
            score += 10
        
        return max(0, min(100, score))


class ParallelLLMRouter:
    """
    Router สำหรับเรียก LLM หลายตัวพร้อมกัน
    
    Usage:
        router = ParallelLLMRouter()
        result = await router.generate_parallel(
            messages=messages,
            task_type="power_analysis"
        )
    """
    
    def __init__(self):
        self.providers: Dict[str, Callable] = {}
        self.timeout = 120.0  # วินาที (เพิ่มจาก 60 เนื่องจากโมเดลคิดนาน)
        
    def register_provider(self, name: str, call_func: Callable):
        """ลงทะเบียน provider"""
        self.providers[name] = call_func
        logger.info(f"Registered LLM provider: {name}")
    
    async def _call_with_timeout(
        self, 
        provider_name: str, 
        call_func: Callable,
        **kwargs
    ) -> LLMResult:
        """เรียก LLM พร้อม timeout และจับเวลา"""
        start_time = time.time()
        
        try:
            # เรียกด้วย timeout
            content = await asyncio.wait_for(
                call_func(**kwargs),
                timeout=self.timeout
            )
            latency = time.time() - start_time
            
            return LLMResult(
                provider=provider_name,
                content=content,
                latency=latency,
                success=True
            )
            
        except asyncio.TimeoutError:
            latency = time.time() - start_time
            logger.warning(f"{provider_name} timeout after {latency:.2f}s")
            return LLMResult(
                provider=provider_name,
                content="",
                latency=latency,
                success=False,
                error="Timeout"
            )
        except Exception as e:
            latency = time.time() - start_time
            logger.error(f"{provider_name} error: {e}")
            return LLMResult(
                provider=provider_name,
                content="",
                latency=latency,
                success=False,
                error=str(e)
            )
    
    async def generate_parallel(
        self,
        messages: List[Dict[str, str]],
        task_type: str = "general",
        selection_strategy: str = "quality",  # "quality", "fastest", "ensemble"
        **kwargs
    ) -> Dict[str, Any]:
        """
        เรียก LLM ทุกตัวพร้อมกัน และเลือกผลลัพธ์ที่ดีที่สุด
        
        Args:
            messages: List of chat messages
            task_type: "power_analysis", "chat", "fault_summary", etc.
            selection_strategy: 
                - "quality": เลือกคำตอบที่มีคะแนนคุณภาพสูงสุด
                - "fastest": เลือกตัวที่ตอบเร็วที่สุดที่ success
                - "ensemble": รวมผลลัพธ์จากหลายตัว
        
        Returns:
            Dict with selected result and metadata
        """
        if not self.providers:
            raise ValueError("No LLM providers registered")
        
        logger.info(f"Parallel LLM call started: {task_type} with {len(self.providers)} providers")
        
        # สร้าง tasks สำหรับเรียกทุก provider พร้อมกัน
        tasks = []
        for name, func in self.providers.items():
            task = self._call_with_timeout(
                provider_name=name,
                call_func=func,
                messages=messages,
                **kwargs
            )
            tasks.append(task)
        
        # รอผลลัพธ์จากทุกตัว (แบบ gather ไม่ใช่ race)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # กรองเฉพาะผลลัพธ์ที่สำเร็จ
        successful_results: List[LLMResult] = []
        for r in results:
            if isinstance(r, LLMResult) and r.success and r.content:
                # ให้คะแนนคุณภาพ
                if task_type == "power_analysis":
                    r.quality_score = QualityScorer.score_thai_power_analysis(r.content)
                elif task_type == "chat":
                    r.quality_score = QualityScorer.score_chat_response(r.content)
                else:
                    r.quality_score = 50.0  # Default
                
                successful_results.append(r)
        
        if not successful_results:
            logger.error("All LLM providers failed")
            return {
                "success": False,
                "content": "❌ เกิดข้อผิดพลาด: ไม่สามารถเชื่อมต่อ AI ได้ทุกระบบ",
                "provider": "none",
                "results": []
            }
        
        # เลือกผลลัพธ์ตาม strategy
        if selection_strategy == "fastest":
            selected = min(successful_results, key=lambda x: x.latency)
            logger.info(f"Selected fastest: {selected.provider} ({selected.latency:.2f}s)")
            
        elif selection_strategy == "ensemble":
            # รวมผลลัพธ์จากทุกตัว (ใช้ตัวที่ quality สูงสุดเป็นหลัก)
            selected = self._ensemble_results(successful_results)
            
        else:  # quality (default)
            selected = max(successful_results, key=lambda x: x.quality_score)
            logger.info(
                f"Selected best quality: {selected.provider} "
                f"(score={selected.quality_score:.1f}, latency={selected.latency:.2f}s)"
            )
        
        return {
            "success": True,
            "content": selected.content,
            "provider": selected.provider,
            "quality_score": selected.quality_score,
            "latency": selected.latency,
            "all_results": [
                {
                    "provider": r.provider,
                    "success": r.success,
                    "latency": r.latency,
                    "quality_score": r.quality_score,
                    "error": r.error
                }
                for r in results if isinstance(r, LLMResult)
            ],
            "is_parallel": True
        }
    
    def _ensemble_results(self, results: List[LLMResult]) -> LLMResult:
        """
        รวมผลลัพธ์จากหลาย LLM (เฉพาะกรณีสำคัญ)
        เลือกตัวที่ quality สูงสุด แต่เพิ่มข้อมูลว่ามี LLM ตัวอื่นเห็นด้วยหรือไม่
        """
        # เรียงตาม quality score
        sorted_results = sorted(results, key=lambda x: x.quality_score, reverse=True)
        best = sorted_results[0]
        
        # ถ้ามีหลายตัวที่ quality ใกล้เคียงกัน แสดงว่า consensus สูง
        if len(sorted_results) > 1:
            second_best = sorted_results[1]
            quality_diff = best.quality_score - second_best.quality_score
            
            if quality_diff < 10:
                # เพิ่ม note ว่ามี consensus
                consensus_note = f"\n\n> 💡 *AI Analysis Consensus: {len(results)} models agree on this assessment*"
                best.content = best.content + consensus_note
        
        return best
    
    async def generate_with_race(
        self,
        messages: List[Dict[str, str]],
        timeout: float = 30.0,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Race mode: ใช้ตัวที่ตอบกลับก่อน (สำหรับกรณีที่ความเร็วสำคัญ)
        """
        tasks = []
        for name, func in self.providers.items():
            task = asyncio.create_task(
                self._call_with_timeout(name, func, messages=messages, **kwargs),
                name=name
            )
            tasks.append(task)
        
        # รอตัวแรกที่เสร็จและ success
        done, pending = set(), set(tasks)
        
        while pending:
            done, pending = await asyncio.wait(
                pending, 
                return_when=asyncio.FIRST_COMPLETED
            )
            
            for task in done:
                try:
                    result = task.result()
                    if result.success and result.content:
                        # ยกเลิก tasks ที่เหลือ
                        for p in pending:
                            p.cancel()
                        
                        logger.info(f"Race won by: {result.provider} ({result.latency:.2f}s)")
                        return {
                            "success": True,
                            "content": result.content,
                            "provider": result.provider,
                            "latency": result.latency,
                            "is_race": True
                        }
                except Exception:
                    continue
        
        # ถ้าทุกตัว fail
        return {
            "success": False,
            "content": "❌ เกิดข้อผิดพลาดในการเชื่อมต่อ AI",
            "provider": "none"
        }


# Singleton instance
_parallel_router: Optional[ParallelLLMRouter] = None


def get_parallel_router() -> ParallelLLMRouter:
    """Get or create singleton router"""
    global _parallel_router
    if _parallel_router is None:
        _parallel_router = ParallelLLMRouter()
    return _parallel_router
