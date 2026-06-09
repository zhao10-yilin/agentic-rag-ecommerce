"""Rich mock e-commerce data for demo and interview presentation.

Provides realistic Chinese e-commerce data across all four scenarios:
1. Outdoor/music festival gear (Scenario 1 - Smart Customer Service)
2. Womenswear catalog with reviews (Scenario 2 - Personalized Recommendation)
3. Competitor pricing data (Scenario 3 - Automated Operations)
4. Order/return/supply chain data (Scenario 4 - Supply Chain)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ============================================================================
# Product Catalog
# ============================================================================


@dataclass
class MockProduct:
    id: str
    name: str
    category: str
    brand: str
    price: float
    description: str
    specs: dict[str, Any] = field(default_factory=dict)
    stock: int = 100
    rating: float = 4.5
    review_count: int = 100
    image_url: str = ""


# ---- Outdoor / Festival Gear ----
OUTDOOR_PRODUCTS: list[MockProduct] = [
    MockProduct(
        id="OUT-001",
        name="NatureHike 轻量防水帐篷 2人款",
        category="帐篷",
        brand="NatureHike",
        price=399.00,
        description="超轻防水双层帐篷，适合春夏户外音乐节露营。重量仅1.8kg，收纳体积30×15cm。防水指数3000mm。",
        specs={"重量": "1.8kg", "容量": "2人", "防水指数": "3000mm", "材质": "20D尼龙"},
        stock=45,
        rating=4.7,
        review_count=328,
    ),
    MockProduct(
        id="OUT-002",
        name="探路者 户外折叠椅 便携款",
        category="户外家具",
        brand="探路者",
        price=159.00,
        description="轻便折叠户外椅，承重120kg，自带杯架。音乐节/露营必备。",
        specs={"重量": "0.9kg", "承重": "120kg", "材质": "600D牛津布+铝合金"},
        stock=120,
        rating=4.5,
        review_count=512,
    ),
    MockProduct(
        id="OUT-003",
        name="迪卡侬 防晒速干衣 UPF50+",
        category="户外服装",
        brand="迪卡侬",
        price=129.00,
        description="UPF50+专业防晒速干T恤，透气快干。适合夏季户外活动。多色可选。",
        specs={"防晒指数": "UPF50+", "材质": "88%聚酯纤维+12%氨纶", "速干": "是"},
        stock=200,
        rating=4.4,
        review_count=890,
    ),
    MockProduct(
        id="OUT-004",
        name="Black Diamond 头灯 Spot 400",
        category="户外照明",
        brand="Black Diamond",
        price=298.00,
        description="400流明防水头灯，夜间音乐节/露营照明首选。红光夜视模式，IPX8防水。",
        specs={"亮度": "400流明", "防水等级": "IPX8", "重量": "86g", "电池": "3×AAA"},
        stock=75,
        rating=4.8,
        review_count=234,
    ),
    MockProduct(
        id="OUT-005",
        name="Osprey Daylite Plus 日用背包 20L",
        category="背包",
        brand="Osprey",
        price=499.00,
        description="20L轻量多功能背包，适合音乐节一日出行。内置水袋仓，多隔层设计。",
        specs={"容量": "20L", "重量": "0.6kg", "材质": "210D尼龙"},
        stock=60,
        rating=4.6,
        review_count=456,
    ),
    MockProduct(
        id="OUT-006",
        name="挪客 充气防潮垫 单人款",
        category="睡眠系统",
        brand="挪客",
        price=189.00,
        description="R值3.5充气防潮垫，入睡舒适保障。内置充气泵。",
        specs={"R值": "3.5", "重量": "0.7kg", "厚度": "6.5cm"},
        stock=90,
        rating=4.3,
        review_count=167,
    ),
]

# ---- Women's Clothing ----
WOMENSWEAR_PRODUCTS: list[MockProduct] = [
    MockProduct(
        id="WOM-001",
        name="Theory 羊毛混纺西装外套",
        category="西装外套",
        brand="Theory",
        price=2899.00,
        description="意大利羊毛混纺面料，经典单排扣设计。修身剪裁，适合职场通勤和商务会议。百搭黑色/深灰可选。",
        specs={"面料": "96%羊毛 4%氨纶", "版型": "修身", "适用季节": "春秋/冬", "尺码": "XS-XL"},
        stock=28,
        rating=4.7,
        review_count=456,
    ),
    MockProduct(
        id="WOM-002",
        name="ICICLE 之禾 真丝衬衫",
        category="衬衫",
        brand="ICICLE 之禾",
        price=1299.00,
        description="100%桑蚕丝面料，飘带领设计。可内搭西装也可单穿，职场通勤必备基础款。",
        specs={"面料": "100%桑蚕丝", "版型": "微宽松", "适用季节": "四季", "尺码": "XS-XXL"},
        stock=45,
        rating=4.8,
        review_count=892,
    ),
    MockProduct(
        id="WOM-003",
        name="Maje 高腰阔腿西裤",
        category="西裤",
        brand="Maje",
        price=1599.00,
        description="高腰设计拉长腿部线条，阔腿版型修饰腿型。垂坠感面料，不易起皱。搭配衬衫或针织衫都好看。",
        specs={"面料": "三醋酸纤维", "版型": "阔腿", "适用季节": "四季", "尺码": "XS-L"},
        stock=32,
        rating=4.6,
        review_count=623,
    ),
    MockProduct(
        id="WOM-004",
        name="COS 针织连衣裙 中长款",
        category="连衣裙",
        brand="COS",
        price=890.00,
        description="极简北欧设计，罗纹针织面料，弹性好不紧绷。圆领中长款，单穿或配外套皆可，通勤约会两穿。",
        specs={"面料": "棉+莫代尔混纺", "版型": "修身", "适用季节": "春秋", "尺码": "XS-L"},
        stock=55,
        rating=4.5,
        review_count=1234,
    ),
    MockProduct(
        id="WOM-005",
        name="Sandro 法式复古碎花茶歇裙",
        category="连衣裙",
        brand="Sandro",
        price=2199.00,
        description="V领收腰设计，法式复古印花。轻盈雪纺面料，适合约会、下午茶、度假。搭配草帽和凉鞋超好看。",
        specs={"面料": "雪纺", "版型": "收腰A字", "适用季节": "春夏", "尺码": "XS-L"},
        stock=18,
        rating=4.9,
        review_count=345,
    ),
]

# ---- All products combined ----
ALL_PRODUCTS = OUTDOOR_PRODUCTS + WOMENSWEAR_PRODUCTS


# ============================================================================
# Knowledge Base Articles (模拟 RAG 检索结果)
# ============================================================================

KB_ARTICLES: dict[str, str] = {
    "outdoor_festival_guide": """
# 户外音乐节装备完全指南

## 核心装备清单
1. **帐篷与睡眠系统**：选择轻量双层防水帐篷（防水指数≥2000mm），配合充气防潮垫和睡袋。
2. **照明设备**：头灯优于手电，释放双手。建议亮度≥200流明，备用电池。
3. **防晒与防护**：UPF50+速干衣+宽檐帽+防晒霜。夏季音乐节必备三件套。
4. **座椅**：折叠便携椅让你在演出间隙舒适休息。
5. **背包**：20-30L日用背包，分区收纳。

## 不同季节注意事项
- **夏季**：防晒、补水是关键。带上水袋（2L以上）。
- **春秋**：昼夜温差大，带冲锋衣内胆。
- **冬季**：不推荐户外露营式音乐节，除非有专业装备。

## 预算参考
- 入门方案：800-1200元（国产帐篷+基础装备）
- 中端方案：1500-3000元（品牌装备，轻量化）
- 高端方案：3000-6000元（专业轻量装备）
""",
    "womenswear_style_guide": """
# 职场女装穿搭指南——从入门到出彩

## 如何打造职场衣橱？

### 1. 基础款优先
- **西装外套**：修身款最百搭，黑色/深灰/藏蓝是职场基础色。投资一件好面料（羊毛>90%）可以穿5年+。
- **真丝衬衫**：飘带领或尖领皆可，比棉质衬衫更有质感。白色/米色/香槟色最实穿。
- **西裤**：高腰阔腿款修饰比例，三醋酸纤维面料垂坠又不易皱，出差也省心。

### 2. 搭配逻辑
- **三件套公式**：西装 + 衬衫 + 西裤 = 最正式的通勤组合
- **一衣多穿**：真丝衬衫配西裤是职场、配半裙是约会、配牛仔裤是休闲
- **颜色组合**：全身不超过3个颜色。黑/白/米+一个饱和度低的点缀色（酒红/墨绿/雾蓝）

### 3. 不同场合推荐
- 面试/重要会议：Theory 西装 + ICICLE 衬衫 + Maje 西裤 = 专业气场
- 日常通勤：COS 针织裙 + 西装外搭 = 温柔干练
- 周五/团建：Sandro 茶歇裙 + 小白鞋 = 法式轻松
- 换季过渡：西装外套 + T恤 + 牛仔裤 = smart casual

### 4. 新手特别建议
- 先投资"天花板三件套"（好西装+好衬衫+好西裤），其他慢慢补
- 尽量选天然面料（羊毛/真丝/棉麻），化纤的质感差别肉眼可见
- 试穿时注意肩线和腰线——西装肩线要刚好落在肩膀末端，不能垮
""",
    "return_policy": """
# 退换货政策

## 一般退换货规则
1. **7天无理由退货**：自签收之日起7天内，商品完好可申请无理由退货。
2. **质量问题退换**：15天内出现质量问题的，可申请换货或退货。
3. **保修期内故障**：凭购买凭证享受保修服务，不同品类保修期不同：
   - 小家电（咖啡机等）：1年保修
   - 户外装备：6个月保修
   - 服装类：30天保修

## 退货流程
1. 用户提交退货申请 → 系统生成RMA号
2. 仓库确认退货地址 → 用户寄回商品
3. 仓库验收 → 确认无误 → 退款到账（3-5个工作日）

## 特殊品类说明
- 已拆封的个人护理用品不支持无理由退货
- 定制商品不支持退货
- 赠品需一并退回
""",
    "competitor_analysis_template": """
# 竞品分析报告模板

## 分析方法
1. 确认目标产品及其核心参数
2. 在同品类中选取3-5个直接竞品
3. 对比价格、功能、用户评分三个维度
4. 计算市场均价和中位数
5. 给出定价建议

## 咖啡机市场格局（2026年5月）
- 德龙 EC685（1299元）：半自动入门标杆，京东好评率96%
- 飞利浦 EP1221（2499元）：全自动性价比之王
- 百胜图 Mini（799元）：价格屠夫，学生党首选
- Breville BES870（3299元）：专业入门之选
- 市场均价：~2100元 | 中位数：~1900元
""",
}


# ============================================================================
# User Profiles
# ============================================================================

@dataclass
class MockUser:
    id: str
    name: str
    preferences: dict[str, Any]
    purchase_history: list[dict[str, Any]]
    search_history: list[str]


MOCK_USERS: dict[str, MockUser] = {
    "user_xiaoyu": MockUser(
        id="user_xiaoyu",
        name="小雨",
        preferences={
            "price_range": "中高端",
            "favorite_brands": ["Theory", "ICICLE", "COS", "Maje"],
            "style": "职场通勤",
            "interests": ["女装", "穿搭", "职场形象"],
            "budget_for_womenswear": "2000-5000元",
        },
        purchase_history=[
            {"date": "2026-04-10", "product": "COS 羊绒衫 米色", "price": 690.0},
            {"date": "2026-02-14", "product": "Sam Edelman 乐福鞋 黑色", "price": 899.0},
            {"date": "2025-11-20", "product": "Longchamp 饺子包 中号", "price": 1200.0},
        ],
        search_history=["职场穿搭 女装", "西装外套 百搭 品牌", "通勤连衣裙推荐"],
    ),
    "user_xiaomei": MockUser(
        id="user_xiaomei",
        name="小美",
        preferences={
            "price_range": "中低",
            "favorite_brands": ["迪卡侬", "NatureHike"],
            "interests": ["户外", "音乐节", "露营", "徒步"],
            "budget_for_outdoor": "800-2000元",
        },
        purchase_history=[
            {"date": "2026-04-20", "product": "迪卡侬登山鞋", "price": 299.0},
            {"date": "2026-03-05", "product": "NatureHike 睡袋", "price": 199.0},
        ],
        search_history=["户外音乐节装备", "轻量帐篷推荐", "音乐节穿搭"],
    ),
}


# ============================================================================
# Competitor Pricing Data
# ============================================================================

COMPETITOR_PRICES: dict[str, list[dict[str, Any]]] = {
    "WOM-001": [
        {"platform": "京东", "seller": "Theory官方旗舰店", "price": 2899, "promotion": "满3000减300"},
        {"platform": "天猫", "seller": "Theory旗舰店", "price": 2919, "promotion": "送丝巾"},
        {"platform": "拼多多", "seller": "品牌Outlet", "price": 2399, "promotion": "无"},
        {"platform": "苏宁", "seller": "自营", "price": 2799, "promotion": "满减"},
    ],
    "WOM-003": [
        {"platform": "京东", "seller": "Maje旗舰店", "price": 1599, "promotion": "9折券后1439"},
        {"platform": "天猫", "seller": "Maje官方店", "price": 1499, "promotion": "送腰带"},
    ],
}


# ============================================================================
# Demo Scenarios — pre-scripted agent traces
# ============================================================================

DEMO_SCENARIOS = {
    "festival_gear": {
        "title": "🎸 场景一：智能导购 — 户外音乐节装备推荐",
        "user_message": "我要去参加一个户外音乐节，帮我推荐一套装备吧",
        "user_name": "小美",
        "user_id": "user_xiaomei",
        "clarifying_question": "好的！户外音乐节需要不少准备呢～您是打算过夜露营，还是当天来回？另外，音乐节在什么季节、在哪个城市？这些会决定推荐什么装备哦。",
        "clarification_response": "夏季的音乐节，在杭州，会过夜露营两晚",
    },
    "womenswear": {
        "title": "场景二：个性化推荐 — 女装推荐",
        "user_message": "我想买一件适合职场穿的百搭女装外套",
        "user_name": "小雨",
        "user_id": "user_xiaoyu",
        "clarifying_question": "职场穿搭选对一件好外套太重要了！为了精准推荐，想了解一下：您偏好什么风格（简约/法式/韩系）？另外大概预算和尺码方便说一下吗？",
        "clarification_response": "简约风，预算3000左右，平时穿M码",
    },
    "competitor_analysis": {
        "title": "场景三：运营分析 — 竞品定价",
        "user_message": "帮我分析一下 Theory 西装外套在市场上的定价情况，看看我们需要调整吗",
        "user_name": "运营经理",
        "user_id": None,
        "clarifying_question": None,
        "clarification_response": None,
    },
    "return_order": {
        "title": "场景四：供应链 — 退货处理",
        "user_message": "我的订单 #ORD-20260501-001 收到的大衣尺码不合适，我要退货",
        "user_name": "小雨",
        "user_id": "user_xiaoyu",
        "clarifying_question": "很抱歉给您带来不便！请问瑕疵的具体情况是什么？是外观划痕还是功能故障？这会影响我们处理方式的优先级。",
        "clarification_response": "外观有明显划痕，而且水箱漏水",
    },
}
