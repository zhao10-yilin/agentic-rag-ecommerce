"""Prompt 模板加载器 —— 从 YAML 文件加载各意图的 prompt 模板。"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 模板缓存
_TEMPLATE_CACHE: Dict[str, Dict[str, Any]] = {}

# 模板目录
_TEMPLATE_DIR = Path(__file__).parent


def load_template(intent: str) -> Dict[str, Any]:
    """
    加载指定意图的 YAML 模板。

    Args:
        intent: 意图标识 (如 "balance_query")

    Returns:
        模板字典，包含 intent, intent_name, system_prompt_additions,
        template, few_shot_examples, output_requirements 等字段

    Raises:
        FileNotFoundError: 模板文件不存在
        ValueError: YAML 格式错误
    """
    if intent in _TEMPLATE_CACHE:
        return _TEMPLATE_CACHE[intent]

    try:
        import yaml
    except ImportError:
        raise ImportError("请安装 PyYAML: pip install pyyaml")

    template_path = _TEMPLATE_DIR / f"{intent}.yaml"

    if not template_path.exists():
        raise FileNotFoundError(f"模板文件不存在: {template_path}")

    with open(template_path, "r", encoding="utf-8") as f:
        template = yaml.safe_load(f)

    if not isinstance(template, dict):
        raise ValueError(f"模板格式错误 (应为 YAML 字典): {template_path}")

    required_fields = ["intent", "intent_name", "template"]
    for field in required_fields:
        if field not in template:
            raise ValueError(f"模板缺少必要字段 '{field}': {template_path}")

    _TEMPLATE_CACHE[intent] = template
    logger.debug(f"加载模板: {intent} ({template.get('intent_name', '?')})")
    return template


def list_templates() -> list:
    """列出所有可用的模板。"""
    templates = []
    for f in sorted(_TEMPLATE_DIR.glob("*.yaml")):
        templates.append(f.stem)
    return templates


def clear_cache():
    """清除模板缓存 (用于测试)。"""
    _TEMPLATE_CACHE.clear()
