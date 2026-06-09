"""
通知模块
-------
负责将差评列表通过飞书 Webhook 发送到业务群。
消息格式为 Markdown，支持批量发送与截断保护。
"""

import base64
import hashlib
import hmac
import logging
import time
from datetime import datetime

import requests

from models import Review

logger = logging.getLogger(__name__)

# 单次消息最多展示的差评条数，防止消息过长
MAX_ALERTS_PER_MESSAGE = 10


def _generate_sign(secret: str) -> tuple[str, str]:
    """
    生成飞书签名校验所需的 timestamp 和 sign。

    算法：对 `timestamp + "\\n" + secret` 进行 HMAC-SHA256 加密，
          再对结果做 Base64 编码。

    Args:
        secret: 飞书机器人 Secret

    Returns:
        (timestamp, sign) 元组
    """
    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sign = base64.b64encode(hmac_code).decode("utf-8")
    return timestamp, sign


def _build_markdown(shop_name: str, reviews: list[Review]) -> str:
    """构造飞书 Markdown 消息内容。"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(reviews)
    displayed = reviews[:MAX_ALERTS_PER_MESSAGE]
    remaining = total - len(displayed)

    lines = [
        f"# 🚨 亚马逊差评告警",
        f"",
        f"**店铺**：{shop_name}",
        f"**扫描时间**：{timestamp}",
        f"**发现差评数量**：{total}",
        f"",
        f"---",
        f"",
    ]

    for idx, review in enumerate(displayed, start=1):
        stars = "⭐" * review.rating + "☆" * (5 - review.rating)
        summary = review.content[:120] + "..." if len(review.content) > 120 else review.content

        lines.extend([
            f"### {idx}. {review.product_name}",
            f"- **评分**：{stars} ({review.rating}/5)",
            f"- **评论标题**：{review.title}",
            f"- **作者**：{review.author} ｜ **日期**：{review.date}",
            f"- **内容摘要**：{summary}",
            f"- **链接**：[查看商品]({review.url})",
            f"",
        ])

    if remaining > 0:
        lines.append(f"> 📌 还有 **{remaining}** 条差评未展示，请登录后台查看完整列表。")

    return "\n".join(lines)


def send_feishu_alert(webhook_url: str, reviews: list[Review], shop_name: str, secret: str = "") -> bool:
    """
    发送飞书告警消息。

    Args:
        webhook_url: 飞书机器人 Webhook 地址
        reviews: 差评评论列表
        shop_name: 店铺名称
        secret: 飞书机器人 Secret（用于签名校验）

    Returns:
        bool: 发送是否成功
    """
    if not webhook_url:
        logger.error("飞书 Webhook 地址未配置，请在 config.py 中填写 FEISHU_WEBHOOK")
        return False

    if not reviews:
        logger.info("差评列表为空，跳过通知")
        return True

    markdown_content = _build_markdown(shop_name, reviews)

    payload: dict = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": "🚨 亚马逊差评告警"
                },
                "template": "red"
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": markdown_content
                    }
                }
            ]
        }
    }

    # 如果配置了 Secret，添加签名校验字段
    if secret:
        timestamp, sign = _generate_sign(secret)
        payload["timestamp"] = timestamp
        payload["sign"] = sign

    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        response.raise_for_status()
        result = response.json()

        if result.get("code") == 0:
            logger.info(f"飞书通知发送成功，共 {len(reviews)} 条差评")
            return True
        else:
            logger.error(f"飞书通知发送失败：{result}")
            return False

    except requests.exceptions.RequestException as e:
        logger.error(f"请求飞书 Webhook 失败：{e}")
        return False
