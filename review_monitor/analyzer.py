"""
差评分析模块
-----------
基于规则判断单条评论是否为"差评"。
当前实现：星级 + 关键词双维度判定。
V4 可扩展为 NLP 模型语义分析。
"""

from models import Review


def is_negative_review(review: Review, negative_keywords: list[str]) -> bool:
    """
    判断一条评论是否为差评。

    判定规则（满足任意一条即视为差评）：
        1. 评分 <= 2 星
        2. 评论标题或正文中包含任一负面关键词（大小写不敏感）

    Args:
        review: 评论对象
        negative_keywords: 负面关键词列表

    Returns:
        bool: True 表示差评，False 表示非差评
    """
    # 规则1：星级判定
    if review.rating <= 2:
        return True

    # 规则2：关键词判定
    text = f"{review.title} {review.content}".lower()
    for keyword in negative_keywords:
        if keyword.lower() in text:
            return True

    return False
