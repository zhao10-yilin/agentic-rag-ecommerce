"""
输入处理器 —— 错别字纠正、恶意注入检测、意图识别、槽位提取。

所有规则均复用自训练数据结构:
  - build_training_data.py: classify_intent_by_keywords(), injection patterns, intent keywords
  - bank_vocab.py: TYPO_CORRECTION, BANK_TERMS, SLOT_VALID_VALUES, SAFETY_REFUSAL_RESPONSES
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ============================================================================
# 0. 数据结构
# ============================================================================


@dataclass
class InjectionResult:
    """注入检测结果。"""
    is_attack: bool = False
    attack_type: str = ""          # sql_injection, prompt_override, dan_jailbreak, etc.
    confidence: float = 0.0
    matched_patterns: List[str] = field(default_factory=list)
    refusal_response: str = ""     # 预置的安全拒绝回复


@dataclass
class IntentResult:
    """意图识别结果。"""
    intent: str = "chitchat"           # 意图标签
    intent_name: str = "闲聊"          # 中文名称
    confidence: float = 0.0
    matched_keywords: List[str] = field(default_factory=list)


# ============================================================================
# 1. 银行错别字纠正
# ============================================================================

# 从 bank_vocab.py 提取的错别字纠正映射
TYPO_MAP: Dict[str, str] = {
    # 余额相关
    "餘额": "余额", "余額": "余额", "馀额": "余额", "余鹅": "余额", "鱼额": "余额",
    # 转账相关
    "轉帐": "转账", "轉賬": "转账", "转錢": "转账", "转钱": "转账", "打钱": "转账",
    "汇钱": "汇款",
    # 账户相关
    "帳户": "账户", "帳號": "账号", "开护": "开户", "消户": "销户",
    # 身份相关
    "身分证": "身份证", "身分證": "身份证",
    # 卡类
    "储畜": "储蓄", "储畜卡": "储蓄卡", "信贷卡": "信用卡", "货记卡": "贷记卡", "借计卡": "借记卡",
    # 存款类型
    "定琪": "定期", "活琪": "活期",
    # 理财
    "理材": "理财", "理才": "理财",
    # 挂失相关
    "挂式": "挂失", "解卦": "解挂",
    # 密码相关
    "密吗": "密码", "秘码": "密码", "验证马": "验证码", "验正码": "验证码",
    # 费用
    "手绩费": "手续费", "手绪费": "手续费", "滞拿金": "滞纳金",
    # 渠道
    "纲银": "网银", "手机很行": "手机银行",
    # 支付
    "支副": "支付", "直付": "支付",
    # 存款取款
    "还歀": "还款", "存歀": "存款", "取歀": "取款", "取钱": "取款",
    "还钱": "还款",
    # 金额单位
    "块": "元", "毛": "角",
    # 时间
    "咋天": "昨天", "眀天": "明天",
}


def correct_typos(text: str) -> Tuple[str, List[str]]:
    """
    纠正银行领域常见错别字 (拼音输入法导致的同音错字)。

    Args:
        text: 原始用户输入

    Returns:
        (corrected_text, corrections_made)
    """
    corrections = []
    result = text

    # 直接查表替换
    for wrong, correct in TYPO_MAP.items():
        if wrong in result:
            result = result.replace(wrong, correct)
            corrections.append(f"{wrong} → {correct}")

    if corrections:
        logger.info(f"错别字纠正 ({len(corrections)}): {', '.join(corrections[:5])}"
                     f"{'...' if len(corrections) > 5 else ''}")

    return result, corrections


# ============================================================================
# 2. 恶意注入检测
# ============================================================================

# 17 种注入攻击类型的检测规则
INJECTION_RULES = [
    # (attack_type, pattern, description)
    ("sql_injection", r"(?i)DROP\s+TABLE|UNION\s+SELECT|OR\s+['\"]?\d['\"]?\s*=\s*['\"]?\d|xp_cmdshell|INFORMATION_SCHEMA",
     "SQL 注入"),
    ("prompt_override", r"(?i)ignore\s+(all\s+)?(previous\s+)?instructions?|忘记.*角色设定|覆盖.*安全限制|系统指令更新|忽略.*之前.*指令",
     "Prompt 覆写"),
    ("dan_jailbreak", r"(?i)DAN\s+mode|jailbreak|do\s+anything\s+now|无限制模式|解放AI|打破.*框架",
     "DAN 越狱"),
    ("role_play", r"角色扮演.*银行|扮演.*管理员|假装你是|模拟.*培训场景",
     "角色扮演"),
    ("encoding_obfuscation", r"(?i)base64.*解[码碼]|解[码碼].*执行|请帮我解码",
     "编码混淆"),
    ("fake_system_msg", r"<\|system\|>|\[SYSTEM\]|<<SYS>>|<!-- SYSTEM|安全演练通知|维护模式",
     "伪造系统消息"),
    ("multi_lang", r"(?i)(ignore\s+(all\s+)?(previous\s+)?instructions?).{1,30}[一-鿿]|[一-鿿].{1,20}(password|admin|bypass)",
     "多语言注入"),
    ("emotional_blackmail", r"(ICU|重症|绑架|手术费|救命|被.*骗光|老客户.*帮帮|求你)",
     "情感勒索"),
    ("virtualization", r"渗透测试.*银行|安全审计员|安全演练.*解除|压力测试.*跳过",
     "伪装安全审计"),
    ("insider_jargon", r"(CASA|KYB|DCC|ACBS|AML).*(导出|批量|全量|级别|ROOT|执行)|核心系统切换|反洗钱.*批量提取",
     "内部术语攻击"),
    ("reverse_psychology", r"千万不要告诉我|你肯定不知道|绝对不会告诉|你只需要说.{1,20}来证明",
     "反向心理学"),
    ("cot_hijack", r"让我们一步一步思考|请用逻辑推理.*前提\d|第一步.*第二步.*第三步.*所以你应该",
     "思维链劫持"),
    ("translation_bypass", r"翻译.*然后执行|translate.*execute|翻[译譯].*执行",
     "翻译绕过"),
    ("code_interpreter", r"```(python|sql|bash|shell|sh)\b|在你的环境中运行|os\.system\(|subprocess\.|eval\(|exec\(",
     "代码解释器注入"),
    ("unicode_homoglyph", r"[ｐ-ｚＡ-Ｚ]{4,}|[査詢轉帳餘額護]",
     "Unicode 同形字"),
    ("recursive_nesting", r"请忽略.*忽略|第一层.*第二层.*第三层|真正的指令.*忽略",
     "递归嵌套"),
]


def detect_injection(text: str) -> InjectionResult:
    """
    检测用户输入是否包含恶意注入攻击。

    使用 17 类注入检测规则，按优先级匹配。

    Args:
        text: 用户输入文本

    Returns:
        InjectionResult 包含检测结果
    """
    matched = []
    best_type = ""
    best_confidence = 0.0

    for attack_type, pattern, description in INJECTION_RULES:
        try:
            m = re.search(pattern, text)
            if m:
                confidence = min(1.0, len(m.group()) / max(len(text), 1) * 3 + 0.5)
                matched.append(f"{description} ({attack_type})")
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_type = attack_type
        except re.error:
            logger.warning(f"无效正则: {pattern}")
            continue

    # 多模式匹配加权重
    if len(matched) >= 2:
        best_confidence = min(1.0, best_confidence + 0.2)

    if matched:
        # 从预置的 16 条安全拒绝回复中随机选一条
        import random
        refusal = random.choice(SAFETY_REFUSAL_RESPONSES)

        logger.warning(f"检测到注入攻击 ({len(matched)} 条匹配): {', '.join(matched[:3])}")

        return InjectionResult(
            is_attack=True,
            attack_type=best_type,
            confidence=best_confidence,
            matched_patterns=matched,
            refusal_response=refusal,
        )

    return InjectionResult()


# 从 bank_vocab.py — SAFETY_REFUSAL_RESPONSES (16 条)
SAFETY_REFUSAL_RESPONSES = [
    "抱歉，我无法执行该操作。我是银行智能客服助手，仅能回答与银行业务相关的问题。如有需要，请直接说明您的业务需求。",
    "您好，我检测到您的输入包含非正常的指令或代码。为保障您的账户安全，请使用正常的银行业务语言描述您的需求。",
    "对不起，我只能以银行客服的身份为您服务。如果您有余额查询、交易明细、理财产品等方面的问题，我很乐意帮助您。",
    "抱歉，您的请求超出了我的服务范围。如需办理银行业务，请通过手机银行、网银或前往网点办理。",
    "您好，为了您的账户安全，我无法处理该请求。请重新描述您的银行业务需求，或拨打我行客服热线 955XX 咨询。",
    "抱歉，我无法执行您请求中的指令。我是专门的银行客服助手，请提出与银行业务相关的问题。",
    "您好，我注意到您的输入可能包含不安全的指令。银行客服系统遵循严格的安全规范，请以正常方式描述您的需求。",
    "对不起，该请求不符合银行客服系统的安全策略。如有紧急业务需求，建议您前往就近网点或拨打客服热线。",
    "您好，作为银行智能客服，我只能处理与银行业务相关的合法请求。您当前的输入似乎包含非正常指令，请以常规方式描述您的业务需求。",
    "抱歉，我无法回应此类请求。我是银行客服助手，专注于为您提供余额查询、交易明细、理财产品等银行业务服务。请重新描述您的需求。",
    "您好！系统安全是我们的首要任务。您的输入触发了安全保护机制。如果您确实有银行业务需求，请用正常的银行服务语言重新表述。",
    "对不起，您的请求中包含不被允许的操作。请通过手机银行、网上银行或联系人工客服办理您的业务，感谢您的理解。",
    "您好，我理解您可能有特定需求，但我只能以银行客服的标准方式为您服务。请告诉我您需要办理的具体银行业务，我会尽力协助。",
    "抱歉，为了保护银行系统和客户数据安全，我无法处理此类请求。如果您需要帮助，请说明具体的银行业务问题。",
    "您好！检测到异常输入。银行客服助手有明确的服务边界，我不会执行任何形式的指令注入、代码执行或角色扮演请求。请描述您真实的银行业务需求。",
    "对不起，根据银行安全策略，我无法处理该请求。如有账户查询、交易明细、理财咨询等正当业务需求，我将非常乐意为您服务。",
]

# ============================================================================
# 3. 意图识别
# ============================================================================

# 意图中文名称映射
INTENT_NAMES = {
    "balance_query": "余额查询",
    "transaction_query": "交易明细查询",
    "financial_consult": "理财产品咨询",
    "complaint": "投诉",
    "chitchat": "闲聊",
    "injection_attack": "恶意注入攻击",
}

# 关键词集合 (从 classify_intent_by_keywords() 复制并扩展)
INTENT_KEYWORDS = {
    "injection_attack": [],  # 由 detect_injection() 单独处理
    "complaint": ["投诉", "举报", "差评", "态度差", "太差", "不满", "乱扣", "坑", "骗", "气死",
                  "什么鬼", "垃圾", "太慢了", "等了半天", "投诉你们", "我要投诉"],
    "balance_query": ["余额", "多少钱", "还剩", "额度", "可用", "查余额", "查询余额",
                      "还剩多少", "可用额度", "额度还有", "看余额"],
    "transaction_query": ["交易", "明细", "流水", "消费记录", "转账记录", "账单", "扣款",
                          "记录", "最近交易", "交易记录", "收支明细", "转账明细"],
    "financial_consult": ["理财", "基金", "收益", "定投", "大额存单", "利率", "风险", "投资",
                          "收益率", "年化", "申购", "赎回", "理财产品", "净值"],
}


def classify_intent(text: str) -> IntentResult:
    """
    基于关键词匹配的意图分类 (规则实现，后续可替换为 ONNX 模型)。

    优先级 (与训练数据一致):
      injection_attack > complaint > balance_query >
      transaction_query > financial_consult > chitchat

    Args:
        text: 用户输入文本

    Returns:
        IntentResult 包含意图分类结果
    """
    # 先检查注入 (最高优先级)
    inj = detect_injection(text)
    if inj.is_attack and inj.confidence >= 0.6:
        return IntentResult(
            intent="injection_attack",
            intent_name=INTENT_NAMES["injection_attack"],
            confidence=inj.confidence,
            matched_keywords=inj.matched_patterns,
        )

    # 按优先级遍历其他意图
    priority_order = [
        "complaint",
        "balance_query",
        "transaction_query",
        "financial_consult",
    ]

    for intent in priority_order:
        keywords = INTENT_KEYWORDS.get(intent, [])
        matched = [kw for kw in keywords if kw in text]
        if matched:
            confidence = min(1.0, len(matched) / max(len(keywords), 1) * 1.5 + 0.3)
            return IntentResult(
                intent=intent,
                intent_name=INTENT_NAMES[intent],
                confidence=round(confidence, 2),
                matched_keywords=matched,
            )

    # 默认闲聊
    return IntentResult(
        intent="chitchat",
        intent_name=INTENT_NAMES["chitchat"],
        confidence=0.5,
        matched_keywords=[],
    )


# ============================================================================
# 4. 槽位提取
# ============================================================================

# 从 bank_vocab.py SLOT_VALID_VALUES 复制
SLOT_VALUES = {
    "account_type": ["储蓄卡", "信用卡", "借记卡", "贷记卡", "活期账户",
                      "定期账户", "理财账户", "社保卡", "公积金账户", "贷款账户"],
    "currency": ["人民币", "美元", "港币", "欧元", "日元", "英镑"],
    "time_range": ["今天", "昨天", "最近3天", "最近7天", "最近30天", "本月",
                    "上月", "近3个月", "近6个月", "近1年", "本季度", "本年度"],
    "transaction_type": ["转入", "转出", "转账", "消费", "取现", "存款",
                          "理财购买", "理财赎回", "基金申购", "基金赎回",
                          "缴费", "充值", "退款", "代扣"],
    "product_type": ["货币基金", "债券基金", "股票基金", "混合基金", "指数基金",
                      "定期理财", "活期理财", "大额存单", "结构性存款", "通知存款", "国债"],
    "risk_level": ["R1低风险", "R2中低风险", "R3中风险", "R4中高风险", "R5高风险"],
    "amount_range": ["1万以下", "1-5万", "5-20万", "20-50万", "50万以上"],
    "complaint_type": ["服务态度", "账户问题", "交易纠纷", "产品问题",
                        "技术故障", "费用争议", "信息安全", "其他"],
    "urgency": ["普通", "加急", "紧急"],
}

# 各意图对应的槽位
INTENT_SLOTS = {
    "balance_query": ["account_type", "currency"],
    "transaction_query": ["time_range", "transaction_type", "account_type"],
    "financial_consult": ["product_type", "risk_level", "amount_range"],
    "complaint": ["complaint_type", "urgency"],
    "chitchat": [],
    "injection_attack": ["attack_type"],
}

# 金额提取正则
MONEY_PATTERN = re.compile(r'(\d+[\.\d]*)\s*(万|元|块|千|百)?')


def extract_slots(text: str, intent: str) -> Dict[str, str]:
    """
    从用户输入中提取槽位值。

    使用字符串匹配从 SLOT_VALUES 中查找已知值。

    Args:
        text: 用户输入文本
        intent: 检测到的意图

    Returns:
        槽位键值对 (仅包含该意图相关槽位)
    """
    target_slots = INTENT_SLOTS.get(intent, [])
    slots = {}

    for slot_name in target_slots:
        if slot_name == "attack_type":
            # 攻击类型由 detect_injection 填充
            slots[slot_name] = ""
            continue

        valid_values = SLOT_VALUES.get(slot_name, [])
        for val in valid_values:
            if val in text:
                slots[slot_name] = val
                break

    # 额外: 尝试提取金额
    money_match = MONEY_PATTERN.search(text)
    if money_match:
        slots["_extracted_amount"] = money_match.group()

    # 兼容处理: "态度" 匹配 "服务态度"
    if intent == "complaint" and "complaint_type" not in slots and "态度" in text:
        slots["complaint_type"] = "服务态度"

    return slots


# ============================================================================
# 5. 统一输入处理
# ============================================================================


def process_input(text: str) -> Tuple[str, InjectionResult, IntentResult, Dict[str, str]]:
    """
    完整输入处理流水线: 纠错 → 注入检测 → 意图分类 → 槽位提取。

    Args:
        text: 原始用户输入

    Returns:
        (corrected_text, injection_result, intent_result, slots)
    """
    # Step 1: 错别字纠正
    corrected, corrections = correct_typos(text)

    # Step 2: 注入检测
    injection = detect_injection(corrected)

    # Step 3: 意图分类
    # 如果注入检测已确认攻击，直接设置意图
    if injection.is_attack and injection.confidence >= 0.6:
        intent = IntentResult(
            intent="injection_attack",
            intent_name="恶意注入攻击",
            confidence=injection.confidence,
            matched_keywords=injection.matched_patterns,
        )
    else:
        intent = classify_intent(corrected)

    # Step 4: 槽位提取
    slots = extract_slots(corrected, intent.intent)

    logger.info(
        f"输入处理: intent={intent.intent}({intent.confidence:.2f}), "
        f"slots={slots}, "
        f"attack={injection.is_attack}, "
        f"corrections={len(corrections)}"
    )

    return corrected, injection, intent, slots
