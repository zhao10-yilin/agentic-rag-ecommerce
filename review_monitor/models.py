from dataclasses import dataclass


@dataclass
class Review:
    """亚马逊评论数据模型，字段完全对标真实亚马逊评论结构。"""

    review_id: str      # 评论唯一ID
    product_id: str     # ASIN (Amazon Standard Identification Number)
    product_name: str   # 商品标题
    rating: int         # 1-5 星
    title: str          # 评论标题
    content: str        # 评论正文
    author: str         # 买家昵称
    date: str           # 评论日期 (YYYY-MM-DD)
    url: str            # 商品详情页链接
