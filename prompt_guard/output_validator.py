"""
输出校验器 —— 规则扫描 + ONNX 反思检查 + 降级回复。

校验流程:
  1. 规则扫描: 检查 forbidden_phrases / forbidden_patterns / PII 泄露
  2. ONNX 反思: 使用微调模型二次审查合规性
  3. 降级: 校验失败时替换为预设安全回复

所有规则从 validation_rules.json 加载，支持运营热更新。
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ============================================================================
# 0. 数据结构
# ============================================================================


@dataclass
class Violation:
    """违规记录。"""
    rule_name: str
    severity: str           # "block" | "warn"
    reason: str
    matched_text: str = ""


@dataclass
class ValidationResult:
    """校验结果。"""
    passed: bool = True
    violations: List[Violation] = field(default_factory=list)
    reflection_text: str = ""
    reflection_passed: bool = True
    final_response: str = ""
    used_fallback: bool = False
    fallback_reason: str = ""


# ============================================================================
# 1. 规则加载
# ============================================================================

_RULES_CACHE: Optional[Dict] = None
_RULES_PATH = Path(__file__).parent / "validation_rules.json"


def load_validation_rules(rules_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    加载校验规则 (带缓存)。

    Args:
        rules_path: 规则文件路径，默认使用包内置的 validation_rules.json

    Returns:
        规则字典
    """
    global _RULES_CACHE

    path = rules_path or _RULES_PATH

    if _RULES_CACHE is not None and rules_path is None:
        return _RULES_CACHE

    if not path.exists():
        logger.error(f"校验规则文件不存在: {path}")
        raise FileNotFoundError(f"校验规则文件不存在: {path}")

    with open(path, "r", encoding="utf-8") as f:
        rules = json.load(f)

    if rules_path is None:
        _RULES_CACHE = rules

    logger.debug(f"加载校验规则: {len(rules.get('forbidden_phrases', []))} 禁止短语, "
                 f"{len(rules.get('forbidden_patterns', []))} 禁止模式, "
                 f"{len(rules.get('pii_patterns', []))} PII 模式")
    return rules


def clear_rules_cache():
    """清除规则缓存 (用于测试/热更新)。"""
    global _RULES_CACHE
    _RULES_CACHE = None


# ============================================================================
# 2. 规则扫描
# ============================================================================


def scan_forbidden_phrases(text: str, rules: Dict) -> List[Violation]:
    """扫描禁止短语。"""
    violations = []
    for item in rules.get("forbidden_phrases", []):
        phrase = item["phrase"]
        if phrase in text:
            violations.append(Violation(
                rule_name=f"forbidden_phrase: {phrase}",
                severity=item.get("severity", "block"),
                reason=item.get("reason", ""),
                matched_text=phrase,
            ))
    return violations


def scan_forbidden_patterns(text: str, rules: Dict) -> List[Violation]:
    """扫描禁止正则模式。"""
    violations = []
    for item in rules.get("forbidden_patterns", []):
        try:
            m = re.search(item["pattern"], text)
            if m:
                violations.append(Violation(
                    rule_name=item["name"],
                    severity=item.get("severity", "block"),
                    reason=item.get("reason", ""),
                    matched_text=m.group(),
                ))
        except re.error as e:
            logger.warning(f"无效正则 '{item['name']}': {e}")
    return violations


def scan_pii(text: str, rules: Dict) -> List[Violation]:
    """扫描个人隐私信息泄露 (身份证号/手机号/银行卡号/邮箱)。"""
    violations = []
    for item in rules.get("pii_patterns", []):
        try:
            matches = re.findall(item["pattern"], text)
            if matches:
                # 对于银行卡号，额外进行 Luhn 算法校验
                if item["name"] == "full_bank_card":
                    valid_cards = [m for m in matches if _luhn_check(str(m))]
                    if not valid_cards:
                        continue

                violations.append(Violation(
                    rule_name=item["name"],
                    severity=item.get("severity", "block"),
                    reason=item.get("reason", ""),
                    matched_text=str(matches[0]),
                ))
        except re.error as e:
            logger.warning(f"无效 PII 正则 '{item['name']}': {e}")
    return violations


def scan_length(text: str, rules: Dict) -> List[Violation]:
    """检查回复长度。"""
    violations = []
    max_len = rules.get("max_response_length", 600)
    min_len = rules.get("min_response_length", 3)

    if len(text) > max_len:
        violations.append(Violation(
            rule_name="response_too_long",
            severity="warn",
            reason=f"回复过长 ({len(text)} > {max_len})",
        ))
    if len(text) < min_len:
        violations.append(Violation(
            rule_name="response_too_short",
            severity="block",
            reason=f"回复过短 ({len(text)} < {min_len})",
        ))
    return violations


def scan_chinese_ratio(text: str, rules: Dict) -> List[Violation]:
    """检查中文比例 (过滤大量英文/乱码输出)。允许含数字和标点的正常中文回复。"""
    violations = []
    if not rules.get("require_chinese", True):
        return violations

    # 移除数字、金额、标点和空格后再计算中文比例
    clean = re.sub(r"[\d,\.，。！？、：；（）\s\-+¥$%]", "", text)
    chinese_chars = len(re.findall(r"[一-鿿]", clean))
    total_chars = max(len(clean.strip()), 1)
    ratio = chinese_chars / total_chars if total_chars > 0 else 0

    max_eng = rules.get("max_english_ratio", 0.3)
    if ratio < (1 - max_eng) and total_chars > 20:
        violations.append(Violation(
            rule_name="low_chinese_ratio",
            severity="warn",
            reason=f"中文字符占比过低 ({ratio:.1%})",
        ))
    return violations


def run_rule_scan(generated_text: str, rules: Optional[Dict] = None) -> List[Violation]:
    """
    执行全部规则扫描。

    Args:
        generated_text: 模型生成的回复文本
        rules: 校验规则 (为 None 时自动加载)

    Returns:
        违规列表
    """
    if rules is None:
        rules = load_validation_rules()

    all_violations = []
    all_violations.extend(scan_forbidden_phrases(generated_text, rules))
    all_violations.extend(scan_forbidden_patterns(generated_text, rules))
    all_violations.extend(scan_pii(generated_text, rules))
    all_violations.extend(scan_length(generated_text, rules))
    all_violations.extend(scan_chinese_ratio(generated_text, rules))

    for v in all_violations:
        logger.warning(f"违规: [{v.severity}] {v.rule_name}: {v.reason}")

    return all_violations


# ============================================================================
# 3. Luhn 算法 (银行卡号校验)
# ============================================================================


def _luhn_check(card_num: str) -> bool:
    """Luhn 算法校验银行卡号有效性。"""
    digits = [int(d) for d in card_num if d.isdigit()]
    if len(digits) < 16:
        return False
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d2 = d * 2
            checksum += d2 if d2 < 10 else d2 - 9
        else:
            checksum += d
    return checksum % 10 == 0


# ============================================================================
# 4. ONNX 反思检查
# ============================================================================


def run_reflection_check(
    user_input: str,
    generated_output: str,
    model_dir: Optional[str] = None,
    timeout: float = 10.0,
) -> Tuple[str, bool, str]:
    """
    使用微调的 ONNX 模型对生成结果进行合规反思。

    Args:
        user_input: 原始用户输入
        generated_output: 模型生成的回复
        model_dir: ONNX 模型目录
        timeout: 超时时间 (秒)

    Returns:
        (reflection_text, is_approved, reason)
    """
    rules = load_validation_rules()
    reflection_cfg = rules.get("reflection", {})

    if not reflection_cfg.get("enabled", True):
        logger.info("反思检查已禁用")
        return "", True, "reflection disabled"

    actual_model_dir = model_dir or reflection_cfg.get("model_dir", "./qwen2_onnx_int8")
    max_tokens = reflection_cfg.get("max_tokens", 128)
    actual_timeout = timeout or reflection_cfg.get("timeout_seconds", 10)

    try:
        from .onnx_inference import load_onnx_model, generate_reflection

        session, tokenizer = load_onnx_model(actual_model_dir)

        reflection_text, metadata = generate_reflection(
            session, tokenizer,
            user_input=user_input,
            assistant_output=generated_output,
            max_tokens=max_tokens,
            timeout=actual_timeout,
        )

        logger.info(f"反思完成: {metadata}")

        # 解析反思结果
        approval_prefix = reflection_cfg.get("approval_prefix", "APPROVED")
        refusal_prefix = reflection_cfg.get("refusal_prefix", "REFUSAL")

        if reflection_text.startswith(approval_prefix):
            return reflection_text, True, "approved by reflection model"
        elif reflection_text.startswith(refusal_prefix):
            reason = reflection_text[len(refusal_prefix):].strip().lstrip(":")
            return reflection_text, False, reason or "refused by reflection model"
        else:
            # 没有明确的前缀，视为审核通过但记录警告
            logger.warning(f"反思输出未包含预期前缀: {reflection_text[:80]}")
            return reflection_text, True, "unclear reflection (defaulting to approved)"

    except FileNotFoundError as e:
        logger.warning(f"ONNX 模型不可用，跳过反思检查: {e}")
        return "", True, f"model unavailable: {e}"
    except ImportError as e:
        logger.warning(f"ONNX 依赖不可用，跳过反思检查: {e}")
        return "", True, f"dependency missing: {e}"
    except Exception as e:
        logger.error(f"反思检查异常: {e}")
        return "", True, f"reflection error: {e}"


# ============================================================================
# 5. 降级回复
# ============================================================================


def get_fallback_response(intent: str, reason: str = "blocked") -> str:
    """
    获取降级/预设回复。

    优先级: per_intent > 通用 fallback > 硬编码默认

    Args:
        intent: 意图标签
        reason: 降级原因 (blocked / reflection_failed / timeout / model_error)

    Returns:
        预设的安全回复文本
    """
    rules = load_validation_rules()
    fallbacks = rules.get("fallback_responses", {})

    # 1. 按意图获取
    per_intent = fallbacks.get("per_intent", {})
    if intent in per_intent and reason in ("blocked", "reflection_failed"):
        return per_intent[intent]

    # 2. 按原因获取
    if reason in fallbacks:
        return fallbacks[reason]

    # 3. 默认回复
    return fallbacks.get("default",
        "抱歉，我暂时无法回答这个问题。请拨打我行客服热线 955XX 获取人工帮助。")


# ============================================================================
# 6. 统一校验入口
# ============================================================================


def validate_output(
    user_input: str,
    generated_output: str,
    intent: str,
    enable_reflection: bool = True,
    model_dir: Optional[str] = None,
) -> ValidationResult:
    """
    完整的输出校验流水线。

    流程:
      规则扫描 → (可选) ONNX 反思 → 判断是否通过 → 降级处理

    Args:
        user_input: 原始用户输入
        generated_output: 模型生成的回复
        intent: 意图标签
        enable_reflection: 是否启用 ONNX 反思
        model_dir: ONNX 模型路径

    Returns:
        ValidationResult 包含校验结果和最终回复
    """
    result = ValidationResult()
    rules = load_validation_rules()

    # ── Step 1: 规则扫描 ──
    violations = run_rule_scan(generated_output, rules)
    result.violations = violations

    # 检查是否有 block 级别的违规
    blocking = [v for v in violations if v.severity == "block"]
    if blocking:
        logger.warning(f"规则扫描发现 {len(blocking)} 个阻塞级违规，触发降级")
        result.passed = False
        result.used_fallback = True
        result.fallback_reason = f"规则扫描阻塞: {[v.rule_name for v in blocking]}"
        result.final_response = get_fallback_response(intent, "blocked")
        return result

    # ── Step 2: ONNX 反思 ──
    if enable_reflection:
        reflection_text, reflection_ok, reflection_reason = run_reflection_check(
            user_input, generated_output,
            model_dir=model_dir,
        )
        result.reflection_text = reflection_text
        result.reflection_passed = reflection_ok

        if not reflection_ok:
            logger.warning(f"反思检查未通过: {reflection_reason}")
            result.passed = False
            result.used_fallback = True
            result.fallback_reason = f"反思未通过: {reflection_reason}"
            result.final_response = get_fallback_response(intent, "reflection_failed")
            return result

    # ── 全部通过 ──
    result.passed = True
    result.final_response = generated_output
    return result
