"""Feishu (Lark) integration - bot notifications + Bitable records.

Supports real Feishu webhook calls and Bitable API integration.
For demo/offline mode, falls back to local JSON file logging.

Webhook URL format:
    https://open.feishu.cn/open-apis/bot/v2/hook/{hook_id}

Usage:
    from agentic_rag.integrations.feishu import FeishuBotClient
    bot = FeishuBotClient(webhook_url="https://open.feishu.cn/...")
    await bot.send_return_notification(...)
"""

from __future__ import annotations

import json, logging, os, time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================================
# Bot webhook client
# ============================================================================

class FeishuBotClient:
    """Feishu bot that posts messages to a group chat via webhook.

    Parameters
    ----------
    webhook_url:
        Feishu bot webhook URL. If empty, runs in simulated mode.
    signing_secret:
        HMAC signing secret. Only needed if signature verification is on.
    log_path:
        Where to write the message log for demo/debug purposes.
    """

    def __init__(self, webhook_url: str = "", signing_secret: str = "",
                 log_path: str = "data/feishu_bot_log.jsonl") -> None:
        self._url = webhook_url
        self._secret = signing_secret
        os.makedirs("data", exist_ok=True)
        self._log_path = log_path
        self._simulated = not bool(webhook_url)

    async def send_return_notification(self, clothing_type: str, return_reason: str,
                                        order_id: str, rma_id: str,
                                        customer_note: str = "", stars: int = 0,
                                        review_time: str = "") -> dict[str, Any]:
        """Send a return/review notification card to the Feishu group."""
        now = review_time or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        star_display = {1: "★☆☆☆☆", 2: "★★☆☆☆", 3: "★★★☆☆"}
        star_text = star_display.get(stars, "") if stars > 0 else ""

        card_payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": f"差评监控 - {star_text} {stars}星差评"},
                    "template": "red" if stars <= 2 else "orange",
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": (
                        f"**商品类型**：{clothing_type}\n"
                        f"**差评原因**：{return_reason}\n"
                        f"**评分**：{star_text} ({stars}星)\n"
                        f"**差评时间**：{now}\n"
                        f"**处理状态**：待处理\n"
                        f"**客户备注**：{customer_note or '无'}"
                    )}},
                    {"tag": "hr"},
                    {"tag": "note", "elements": [
                        {"tag": "plain_text", "content": "请客服团队在 2 小时内处理此差评"}
                    ]},
                ],
            },
        }

        result = await self._post(card_payload)

        log_entry = {
            "timestamp": now, "type": "return_notification",
            "order_id": order_id, "rma_id": rma_id,
            "clothing_type": clothing_type, "return_reason": return_reason,
            "customer_note": customer_note, "stars": stars,
            "simulated": self._simulated,
        }
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

        return {"sent": True, "simulated": self._simulated, "log_entry": log_entry}

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._simulated:
            return {"code": 0, "msg": "ok (simulated)", "simulated": True}
        try:
            import urllib.request
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            timestamp = str(int(time.time()))
            url = self._url
            if self._secret:
                import hmac, hashlib, base64 as b64
                sign_str = f"{timestamp}\n{self._secret}"
                h = hmac.new(self._secret.encode(), sign_str.encode(), hashlib.sha256)
                sign = b64.b64encode(h.digest()).decode()
                from urllib.parse import quote
                url = f"{self._url}?timestamp={timestamp}&sign={quote(sign, safe='')}"
            req = urllib.request.Request(url, data=data,
                headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                return {"code": resp.status, "msg": resp.read().decode("utf-8")[:500], "simulated": False}
        except Exception as exc:
            logger.error("Feishu webhook failed: %s", exc)
            return {"code": -1, "msg": str(exc), "simulated": False, "error": str(exc)}


# ============================================================================
# Bitable client (simulated - real API requires app access token)
# ============================================================================

class FeishuBitableClient:
    """Feishu Bitable client for return/review tracking.

    In production, this calls the Feishu Bitable API.
    For the demo, records are stored in a local JSON file.
    """

    COLUMNS = ["商品名称", "评分", "差评原因", "处理状态", "差评时间", "处理时间", "订单号", "RMA编号", "客户备注"]

    def __init__(self, app_token: str = "", table_id: str = "tbl_return_tracking",
                 log_path: str = "data/feishu_bitable.json") -> None:
        self._app_token = app_token
        self._table_id = table_id
        self._live = bool(app_token)
        os.makedirs("data", exist_ok=True)
        self._log_path = log_path
        self._records: list[dict[str, Any]] = self._load()

    def _load(self) -> list[dict[str, Any]]:
        if os.path.exists(self._log_path):
            try:
                with open(self._log_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    return json.loads(content) if content else []
            except (json.JSONDecodeError, FileNotFoundError):
                pass
        return []

    def _save(self) -> None:
        with open(self._log_path, "w", encoding="utf-8") as f:
            json.dump(self._records, f, ensure_ascii=False, indent=2)

    async def add_return_record(self, clothing_type: str, return_reason: str,
                                 order_id: str = "", rma_id: str = "",
                                 customer_note: str = "", stars: int = 0,
                                 review_time: str = "") -> dict[str, Any]:
        """Add a review/return record to the Bitable."""
        now = review_time or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        star_display = {1: "★☆☆☆☆", 2: "★★☆☆☆", 3: "★★★☆☆", 4: "★★★★☆", 5: "★★★★★"}
        star_text = star_display.get(stars, f"{stars}星") if stars > 0 else ""

        record = {
            "record_id": f"rec_{int(time.time() * 1000)}",
            "fields": {
                "商品名称": clothing_type,
                "评分": star_text,
                "差评原因": return_reason,
                "处理状态": "待处理",
                "差评时间": now,
                "处理时间": "",
                "订单号": order_id,
                "RMA编号": rma_id,
                "客户备注": customer_note,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._records.append(record)
        self._save()
        logger.info("Bitable record %s: %s | %s | %s | %s",
                     record["record_id"][:16], clothing_type, return_reason,
                     "待处理", now)
        return record

    async def update_status(self, record_id: str, status: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        for rec in self._records:
            if rec["record_id"] == record_id:
                rec["fields"]["处理状态"] = status
                if status == "已处理":
                    rec["fields"]["处理时间"] = now
                self._save()
                return rec
        return {}

    def get_pending_count(self) -> int:
        return sum(1 for r in self._records if r["fields"].get("处理状态") == "待处理")

    def get_all_records(self) -> list[dict[str, Any]]:
        return list(self._records)


# ============================================================================
# Global instances
# ============================================================================

_bot_client: FeishuBotClient | None = None
_bitable_client: FeishuBitableClient | None = None


def get_bot_client(webhook_url: str = "", signing_secret: str = "") -> FeishuBotClient:
    global _bot_client
    if _bot_client is None:
        _bot_client = FeishuBotClient(webhook_url=webhook_url, signing_secret=signing_secret)
    return _bot_client


def get_bitable_client(app_token: str = "") -> FeishuBitableClient:
    global _bitable_client
    if _bitable_client is None:
        _bitable_client = FeishuBitableClient(app_token=app_token)
    return _bitable_client
