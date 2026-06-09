"""
测试 output_validator.py —— 规则扫描、PII 检测、降级回复。
"""

import pytest
from prompt_guard.output_validator import (
    Violation,
    ValidationResult,
    load_validation_rules,
    scan_forbidden_phrases,
    scan_forbidden_patterns,
    scan_pii,
    run_rule_scan,
    get_fallback_response,
    validate_output,
    clear_rules_cache,
)


@pytest.fixture(autouse=True)
def reset_rules_cache():
    """每个测试前清除规则缓存。"""
    clear_rules_cache()
    yield


class TestRuleLoading:
    """规则加载测试。"""

    def test_load_rules(self):
        rules = load_validation_rules()
        assert "forbidden_phrases" in rules
        assert "forbidden_patterns" in rules
        assert "pii_patterns" in rules
        assert "fallback_responses" in rules

    def test_forbidden_phrases_not_empty(self):
        rules = load_validation_rules()
        phrases = rules["forbidden_phrases"]
        assert len(phrases) >= 5
        assert any(p["phrase"] == "建议您购买" for p in phrases)

    def test_pii_patterns_not_empty(self):
        rules = load_validation_rules()
        patterns = rules["pii_patterns"]
        assert len(patterns) >= 3


class TestForbiddenPhraseScanning:
    """禁止短语扫描测试。"""

    def test_block_buy_recommendation(self):
        rules = load_validation_rules()
        violations = scan_forbidden_phrases("建议您购买我们的理财产品", rules)
        assert len(violations) >= 1
        assert violations[0].severity == "block"

    def test_block_return_guarantee(self):
        rules = load_validation_rules()
        violations = scan_forbidden_phrases("这个产品收益率保证年化5%", rules)
        assert len(violations) >= 1

    def test_block_steady_profit(self):
        rules = load_validation_rules()
        violations = scan_forbidden_phrases("这个绝对稳赚", rules)
        assert len(violations) >= 1

    def test_clean_response_no_violations(self):
        rules = load_validation_rules()
        violations = scan_forbidden_phrases(
            "您好，您储蓄卡当前余额为12,345.67元。请问还有其他需要帮您的吗？",
            rules
        )
        assert len(violations) == 0


class TestPiiDetection:
    """PII 泄露检测测试。"""

    def test_detect_id_number(self):
        rules = load_validation_rules()
        violations = scan_pii("张三的身份证号是110101199001011234", rules)
        assert len(violations) >= 1
        assert any("id_number" in v.rule_name for v in violations)

    def test_detect_phone_number(self):
        rules = load_validation_rules()
        violations = scan_pii("请拨打13800138000联系客服", rules)
        assert len(violations) >= 1
        assert any("phone_number" in v.rule_name for v in violations)

    def test_clean_text_no_pii(self):
        rules = load_validation_rules()
        violations = scan_pii("您好，您的账户余额为1,234.56元", rules)
        assert len(violations) == 0

    def test_detect_bank_card_fake(self):
        """无效的银行卡号不应该通过 Luhn 校验。"""
        rules = load_validation_rules()
        # 以6开头匹配模式，但 Luhn 校验不通过，且内部不含手机号模式
        violations = scan_pii("卡号 6000000000000000007 余额为1000元", rules)
        # 无效卡号 Luhn 校验不过 → 不触发告警
        assert len(violations) == 0


class TestFallbackResponses:
    """降级回复测试。"""

    def test_default_fallback(self):
        response = get_fallback_response("chitchat", "blocked")
        assert len(response) > 10
        assert "银行" in response or "客服" in response or "955" in response

    def test_per_intent_fallback(self):
        response = get_fallback_response("balance_query", "blocked")
        assert len(response) > 10
        # 余额查询的降级回复应该提到查询相关
        assert any(kw in response for kw in ["查询", "余额", "银行", "APP", "955"])

    def test_reflection_failed_fallback(self):
        response = get_fallback_response("financial_consult", "reflection_failed")
        assert len(response) > 10

    def test_model_error_fallback(self):
        response = get_fallback_response("balance_query", "model_error")
        assert len(response) > 10
        assert "稍后" in response or "繁忙" in response or "暂时" in response


class TestRuleScanIntegration:
    """规则扫描集成测试。"""

    def test_clean_response_passes(self):
        violations = run_rule_scan(
            "您好，您储蓄卡当前余额为12,345.67元。请问还有其他需要帮您的吗？"
        )
        assert len(violations) == 0

    def test_risky_response_blocked(self):
        violations = run_rule_scan(
            "建议您购买我们的高收益理财，收益率保证年化8%，绝对稳赚！"
        )
        assert len(violations) >= 2  # 建议您购买 + 收益率保证 + 稳赚

    def test_pii_response_blocked(self):
        violations = run_rule_scan(
            "张伟的身份证号：110101199001011234，手机号：13800138000"
        )
        # 应该有 PII 违规
        assert len(violations) >= 2

    def test_too_short_response(self):
        violations = run_rule_scan("好")
        assert any(v.rule_name == "response_too_short" for v in violations)


class TestValidateOutput:
    """完整校验流水线测试 (不含 ONNX 反思)。"""

    def test_valid_output_passes(self):
        result = validate_output(
            user_input="查询余额",
            generated_output="您好，您储蓄卡当前余额为12,345.67元。请问还有其他需要帮您的吗？",
            intent="balance_query",
            enable_reflection=False,
        )
        assert result.passed
        assert not result.used_fallback
        assert result.final_response == "您好，您储蓄卡当前余额为12,345.67元。请问还有其他需要帮您的吗？"

    def test_invalid_output_falls_back(self):
        result = validate_output(
            user_input="查询余额",
            generated_output="建议您购买我们的理财产品，收益率保证年化10%！",
            intent="balance_query",
            enable_reflection=False,
        )
        assert not result.passed
        assert result.used_fallback
        assert "建议您购买" not in result.final_response
