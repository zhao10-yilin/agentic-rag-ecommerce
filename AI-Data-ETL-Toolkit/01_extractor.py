import json
import random
import time
import requests
import trafilatura
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 静态 HTML、对爬虫友好、内容量充足的技术文档
TEST_URLS = [
    "https://docs.python.org/3/tutorial/",                 # Python 入门教程（已验证）
    "https://docs.python.org/3/library/stdtypes.html",     # Python 标准库 - 内置类型
    "https://peps.python.org/pep-0008/",                   # PEP 8 编码规范
    "https://developer.mozilla.org/en-US/docs/Web/HTML",   # MDN HTML 参考文档
    "https://flask.palletsprojects.com/en/3.0.x/quickstart/",  # Flask 快速入门
]


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}


def create_session():
    """创建带重试机制的 requests Session。"""
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HEADERS)
    return session


def extract_urls_to_jsonl(url_list, output_path, cookies=None):
    """遍历 URL 列表，提取网页正文并保存为 JSONL 格式。"""
    session = create_session()
    if cookies:
        session.cookies.update(cookies)

    with open(output_path, "w", encoding="utf-8") as f:
        for idx, url in enumerate(url_list, start=1):
            print(f"\n[{idx}/{len(url_list)}] 正在处理: {url}")
            try:
                # 随机延时 1-3 秒，降低被反爬概率
                time.sleep(random.uniform(1.0, 3.0))

                resp = session.get(url, timeout=15, allow_redirects=True)
                print(f"  -> HTTP {resp.status_code}, 内容长度 {len(resp.text)} bytes")

                # 403/404 等错误状态码
                if resp.status_code == 403:
                    print(f"  -> [DIAG] 响应前 300 字符: {resp.text[:300]}")
                    print(f"[ERROR] HTTP 403 被拦截: {url}")
                    continue
                if resp.status_code == 404:
                    print(f"[ERROR] HTTP 404 页面不存在: {url}")
                    continue
                resp.raise_for_status()

                html = resp.text
                if not html or len(html) < 200:
                    print(f"[WARN] 返回内容过短，可能被拦截: {url}")
                    continue

                # 尝试用 trafilatura 提取正文
                result = trafilatura.extract(
                    html,
                    output_format="json",
                    with_metadata=True,
                    include_comments=False,
                    url=url,
                )

                if result is None:
                    print(f"[WARN] 提取正文为空，可能页面是 JS 动态渲染: {url}")
                    continue

                data = json.loads(result)
                record = {
                    "url": url,
                    "title": data.get("title", ""),
                    "text": data.get("raw_text", data.get("text", "")),
                }

                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                print(f"[OK] 提取成功: {url}")

            except requests.exceptions.RequestException as e:
                print(f"[ERROR] 网络请求失败 {url}: {e}")
            except Exception as e:
                print(f"[ERROR] 提取失败 {url}: {e}")
                continue


if __name__ == "__main__":
    extract_urls_to_jsonl(TEST_URLS, "data/01_raw.jsonl")
