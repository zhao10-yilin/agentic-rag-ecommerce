"""
配置文件
------
运行前请填写 FEISHU_WEBHOOK 地址。
其他配置项可根据需要调整。
"""

# ---------- 业务配置 ----------

SHOP_NAME = "My Amazon Store"

# 监控的负面关键词（中英文，大小写不敏感）
NEGATIVE_KEYWORDS = [
    "terrible", "worst", "broken", "defective", "waste", "useless",
    "horrible", "awful", "cheap quality", "stopped working",
    "垃圾", "假货", "太差", "没法用", "崩溃", "不要买", "骗人的",
    "质量差", "坏了", "不工作", "失望", "后悔", "浪费钱",
]

# ---------- 飞书配置 ----------

# 必填：飞书群机器人 Webhook 地址
# 获取方式：飞书群设置 -> 群机器人 -> 添加机器人 -> 复制 Webhook 地址
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/e1fb829a-82b3-4d61-b911-58b965e6b9af"

# 必填：飞书机器人 Secret（用于签名校验）
# 获取方式：群机器人 -> 设置 -> 安全设置 -> 签名校验 -> 复制 Secret
FEISHU_SECRET = "Cieijk98szgdLoZFqR8QYe"

# ---------- 模拟数据配置（MVP阶段使用） ----------

MOCK_REVIEW_COUNT = 20          # 模拟生成的评论总条数
MOCK_NEGATIVE_RATIO = 0.25      # 差评占比（用于演示效果，真实场景由实际数据决定）
