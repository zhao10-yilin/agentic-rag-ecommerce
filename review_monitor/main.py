"""
亚马逊差评监控 Agent — 入口脚本
-------------------------------
运行流程：
    1. 获取评论列表（MVP为模拟数据）
    2. 分析并筛选出差评
    3. 通过飞书机器人发送告警通知

使用方法：
    python -m review_monitor.main

注意事项：
    首次运行前，请务必在 config.py 中填写 FEISHU_WEBHOOK 地址。
"""

import logging

from analyzer import is_negative_review
from config import FEISHU_SECRET, FEISHU_WEBHOOK, NEGATIVE_KEYWORDS, SHOP_NAME
from fetcher import fetch_reviews
from notifier import send_feishu_alert

# 配置日志输出格式
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def main():
    logger.info("=" * 40)
    logger.info("亚马逊差评监控 Agent 启动")
    logger.info("=" * 40)

    # Step 1: 获取评论
    logger.info("Step 1/3: 正在获取评论数据...")
    reviews = fetch_reviews()
    logger.info(f"获取到 {len(reviews)} 条评论")

    # Step 2: 分析差评
    logger.info("Step 2/3: 正在分析差评...")
    negative_reviews = [
        review for review in reviews
        if is_negative_review(review, NEGATIVE_KEYWORDS)
    ]
    logger.info(f"发现 {len(negative_reviews)} 条差评")

    # 控制台展示摘要（方便调试）
    if negative_reviews:
        logger.info("差评摘要：")
        for idx, review in enumerate(negative_reviews, start=1):
            logger.info(
                f"  [{idx}] ⭐{review.rating} | {review.product_name} | "
                f"{review.title} — {review.content[:40]}..."
            )
    else:
        logger.info("本次扫描未发现差评，运营情况良好 👍")

    # Step 3: 发送通知
    logger.info("Step 3/3: 正在发送通知...")
    if negative_reviews:
        if not FEISHU_WEBHOOK:
            logger.warning("⚠️ 未配置飞书 Webhook，跳过通知发送。请在 config.py 中填写地址。")
        else:
            success = send_feishu_alert(FEISHU_WEBHOOK, negative_reviews, SHOP_NAME, FEISHU_SECRET)
            if success:
                logger.info("✅ 告警通知已发送至飞书")
            else:
                logger.error("❌ 告警通知发送失败，请检查网络或 Webhook 配置")
    else:
        logger.info("无差评，无需发送通知")

    logger.info("=" * 40)
    logger.info("本次扫描结束")
    logger.info("=" * 40)


if __name__ == "__main__":
    main()
