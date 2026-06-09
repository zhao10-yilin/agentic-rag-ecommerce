"""
评论采集模块
-----------
MVP 阶段使用模拟数据生成器，数据字段完全对标真实亚马逊评论。
V2 接入真实采集时，只需替换本模块内部实现，保持函数签名不变。
"""

import random
import uuid
from datetime import datetime, timedelta

from models import Review


# 模拟商品池（含真实 ASIN 格式和商品名）
MOCK_PRODUCTS = [
    ("B08HMWZBXC", "Wireless Bluetooth Earbuds with Noise Cancelling"),
    ("B09V3KXJPB", "20000mAh Portable Charger Power Bank"),
    ("B07ZPKN6YR", "Running Shoes Men Lightweight Breathable"),
    ("B08N5WRWNW", "Organic Matcha Green Tea Powder 4oz"),
    ("B08PZJN7BD", "Yoga Mat Non-Slip 6mm Thick"),
]

# 评论模板池：按星级分类，中英文混合
REVIEW_TEMPLATES = {
    5: [
        ("Excellent product!", "Highly recommend this to everyone. Build quality is superb and works perfectly."),
        ("Love it!", "Best purchase I've made this year. Exactly as described."),
        ("Superb", "Works flawlessly. Battery life is amazing."),
        ("非常满意", "质量很好，和描述一致，物流也很快。"),
        ("Great value for money", "You won't find a better product at this price point."),
    ],
    4: [
        ("Pretty good", "Mostly satisfied. One minor issue but not a dealbreaker."),
        ("Good but not perfect", "Overall happy with the purchase. Could improve packaging."),
        ("还不错", "整体满意，有一点小瑕疵但不影响使用。"),
        ("Solid choice", "Does what it says. Delivery was fast too."),
    ],
    3: [
        ("It's okay", "Average quality. Expected a bit more given the price."),
        ("一般般", "中规中矩，没有惊喜也没有太失望。"),
        ("Mixed feelings", "Some pros and cons. Might not buy again."),
    ],
    2: [
        ("Disappointed", "Stopped working after two weeks. Not worth the money."),
        ("质量太差", "用了一次就坏了，根本没法用，浪费钱。"),
        ("Cheap quality", "Feels very flimsy. Broke within a month."),
        ("Not as described", "Color was different and size is smaller than advertised."),
    ],
    1: [
        ("Terrible! Do not buy!", "Complete waste of money. Broke on the first day."),
        ("垃圾产品", "假货！质量差到极点，千万不要买，后悔死了。"),
        ("Worst purchase ever", "Defective item received. Customer service ignored me."),
        ("骗人的", "完全不是描述的那样，太差了，崩溃。"),
        ("Horrible experience", "This is useless. Doesn't work at all. Terrible quality."),
    ],
}

MOCK_AUTHORS = [
    "John D.", "Sarah M.", "Mike Chen", "Lisa Wang", "Alex Johnson",
    "Emily Zhang", "David Liu", "Jessica Brown", "Tom Wilson", "Amy Li",
    "Chris Evans", "Nancy Wu", "Robert Taylor", "Linda Yang", "Kevin Park",
]


def _random_date(days_back: int = 30) -> str:
    """生成过去 N 天内的随机日期。"""
    delta = timedelta(days=random.randint(0, days_back))
    dt = datetime.now() - delta
    return dt.strftime("%Y-%m-%d")


def _generate_mock_review(index: int, force_negative: bool = False) -> Review:
    """生成一条模拟评论。"""
    product_id, product_name = random.choice(MOCK_PRODUCTS)

    if force_negative:
        rating = random.choice([1, 2])
    else:
        rating = random.choices(
            population=[1, 2, 3, 4, 5],
            weights=[5, 10, 15, 30, 40],
            k=1
        )[0]

    title, content = random.choice(REVIEW_TEMPLATES[rating])
    author = random.choice(MOCK_AUTHORS)
    date = _random_date()
    review_id = f"R{uuid.uuid4().hex[:8].upper()}"
    url = f"https://www.amazon.com/dp/{product_id}"

    return Review(
        review_id=review_id,
        product_id=product_id,
        product_name=product_name,
        rating=rating,
        title=title,
        content=content,
        author=author,
        date=date,
        url=url,
    )


def fetch_reviews() -> list[Review]:
    """
    获取评论列表。

    MVP 阶段返回模拟数据，用于验证整条监控链路。
    V2 接入真实采集时，保持此函数签名不变，仅替换内部实现。

    Returns:
        List[Review]: 评论对象列表
    """
    from config import MOCK_REVIEW_COUNT, MOCK_NEGATIVE_RATIO

    negative_count = int(MOCK_REVIEW_COUNT * MOCK_NEGATIVE_RATIO)
    positive_count = MOCK_REVIEW_COUNT - negative_count

    reviews: list[Review] = []
    for i in range(negative_count):
        reviews.append(_generate_mock_review(i, force_negative=True))
    for i in range(positive_count):
        reviews.append(_generate_mock_review(i + negative_count, force_negative=False))

    random.shuffle(reviews)
    return reviews
