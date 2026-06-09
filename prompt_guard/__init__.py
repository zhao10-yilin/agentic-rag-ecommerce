"""
prompt_guard —— 银行智能客服生产级提示词防护套件

统一入口:
    from prompt_guard import process
    result = process(user_input="帮我查一下储蓄卡余额", user_id="U00001")

管道流程:
    1. input_processor:  错别字纠正 → 注入检测 → 意图分类 → 槽位提取
    2. context_builder:  拉取用户/产品上下文 → 选择模板 → 组装 Qwen2 Prompt
    3. ONNX 推理:        加载微调模型 → 自回归生成
    4. output_validator: 规则扫描 → ONNX 反思 → 降级处理
    5. 返回结果

返回格式:
    {
        "intent":           str,     # 意图标签
        "intent_name":      str,     # 意图中文名
        "slots":            dict,    # 提取的槽位
        "is_attack":        bool,    # 是否检测到注入攻击
        "attack_type":      str,     # 攻击类型
        "corrected_input":  str,     # 纠错后的输入
        "prompt":           str,     # 组装后的完整 Prompt
        "generated_response": str,   # 模型原始输出
        "reflection_result": str,    # 反思结果 (APPROVED / REFUSAL)
        "final_response":   str,     # 最终输出 (校验通过=生成文本, 否则=降级回复)
        "used_fallback":    bool,    # 是否使用了降级回复
        "violations":       list,    # 违规列表
        "metadata":         dict,    # 耗时/用户等元信息
    }
"""

import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .input_processor import (
    InjectionResult,
    IntentResult,
    process_input,
)
from .context_builder import (
    build_context,
    SYSTEM_PROMPT,
)
from .output_validator import (
    ValidationResult,
    get_fallback_response,
    validate_output,
)

logger = logging.getLogger(__name__)

# 默认 ONNX 模型路径 (相对于项目根目录)
DEFAULT_MODEL_DIR = str(Path(__file__).parent.parent / "qwen2_onnx_int8")


def process(
    user_input: str,
    user_id: str,
    model_dir: Optional[str] = None,
    max_new_tokens: int = 256,
    enable_reflection: bool = True,
    skip_inference: bool = False,
) -> Dict[str, Any]:
    """
    完整的智能客服请求处理。

    Args:
        user_input:    用户输入文本
        user_id:       用户标识 (如 "U00001")
        model_dir:     ONNX 模型目录 (默认 ./qwen2_onnx_int8)
        max_new_tokens: 最大生成 token 数
        enable_reflection: 是否启用 ONNX 反思检查
        skip_inference: 跳过 ONNX 推理 (仅测试预处理+组装)

    Returns:
        结果字典，包含 intent / final_response / metadata 等字段。

    Raises:
        ValueError: 输入为空
    """
    t_total_start = time.perf_counter()

    # ── 0. 输入校验 ──
    if not user_input or not user_input.strip():
        raise ValueError("用户输入不能为空")

    if not user_id or not user_id.strip():
        raise ValueError("用户 ID 不能为空")

    user_input = user_input.strip()
    meta: Dict[str, Any] = {
        "user_id": user_id,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    # ── 1. 输入处理 ──
    t1 = time.perf_counter()
    corrected_input, injection, intent, slots = process_input(user_input)
    meta["input_processing_ms"] = round((time.perf_counter() - t1) * 1000)

    # ── 2. 注入攻击 → 直接拒绝 ──
    if injection.is_attack:
        logger.warning(f"拦截注入攻击: {injection.attack_type} (confidence={injection.confidence:.2f})")
        return {
            "intent": "injection_attack",
            "intent_name": intent.intent_name,
            "slots": slots,
            "is_attack": True,
            "attack_type": injection.attack_type,
            "corrected_input": corrected_input,
            "prompt": "",
            "generated_response": "",
            "reflection_result": "REFUSAL",
            "final_response": injection.refusal_response or get_fallback_response("injection_attack", "blocked"),
            "used_fallback": True,
            "violations": [],
            "metadata": meta,
        }

    # ── 3. 构建 Prompt ──
    t2 = time.perf_counter()
    prompt, ctx_meta = build_context(corrected_input, intent.intent, user_id, slots)
    meta.update(ctx_meta)
    meta["context_build_ms"] = round((time.perf_counter() - t2) * 1000)

    # ── 4. ONNX 推理 ──
    generated_response = ""
    generation_meta = {}

    if not skip_inference:
        try:
            from .onnx_inference import load_onnx_model, generate

            actual_model_dir = model_dir or DEFAULT_MODEL_DIR
            session, tokenizer = load_onnx_model(actual_model_dir)

            t3 = time.perf_counter()
            generated_response, generation_meta = generate(
                session, tokenizer, prompt,
                max_new_tokens=max_new_tokens,
                temperature=0.0,
            )
            meta["generation_ms"] = round((time.perf_counter() - t3) * 1000)
            meta["generation"] = generation_meta

        except FileNotFoundError as e:
            logger.error(f"ONNX 模型不可用: {e}")
            return {
                "intent": intent.intent,
                "intent_name": intent.intent_name,
                "slots": slots,
                "is_attack": False,
                "attack_type": "",
                "corrected_input": corrected_input,
                "prompt": prompt,
                "generated_response": "",
                "reflection_result": "",
                "final_response": get_fallback_response(intent.intent, "model_error"),
                "used_fallback": True,
                "violations": [],
                "metadata": meta,
            }
        except ImportError as e:
            logger.error(f"ONNX 依赖未安装: {e}")
            return {
                "intent": intent.intent,
                "intent_name": intent.intent_name,
                "slots": slots,
                "is_attack": False,
                "attack_type": "",
                "corrected_input": corrected_input,
                "prompt": prompt,
                "generated_response": "",
                "reflection_result": "",
                "final_response": get_fallback_response(intent.intent, "model_error"),
                "used_fallback": True,
                "violations": [],
                "metadata": meta,
            }
    else:
        logger.info("跳过 ONNX 推理 (skip_inference=True)")
        generated_response = "[SKIPPED - no inference]"

    # ── 5. 输出校验 ──
    if skip_inference:
        # 未生成内容，跳过校验
        validation = ValidationResult(
            passed=True,
            final_response=generated_response,
        )
    else:
        validation = validate_output(
            user_input=corrected_input,
            generated_output=generated_response,
            intent=intent.intent,
            enable_reflection=enable_reflection,
            model_dir=model_dir or DEFAULT_MODEL_DIR,
        )

    meta["total_ms"] = round((time.perf_counter() - t_total_start) * 1000)
    meta["used_fallback"] = validation.used_fallback

    # ── 6. 组装结果 ──
    return {
        "intent": intent.intent,
        "intent_name": intent.intent_name,
        "slots": slots,
        "is_attack": injection.is_attack,
        "attack_type": injection.attack_type,
        "corrected_input": corrected_input,
        "prompt": prompt,
        "generated_response": generated_response,
        "reflection_result": "APPROVED" if validation.reflection_passed else "REFUSAL",
        "final_response": validation.final_response,
        "used_fallback": validation.used_fallback,
        "violations": [
            {"name": v.rule_name, "severity": v.severity, "reason": v.reason}
            for v in validation.violations
        ],
        "metadata": meta,
    }


# 便捷导出
__all__ = [
    "process",
    "process_input",
    "build_context",
    "validate_output",
    "SYSTEM_PROMPT",
    "InjectionResult",
    "IntentResult",
    "ValidationResult",
]
