import json
import re
import uuid
from langchain_text_splitters import RecursiveCharacterTextSplitter


def mask_pii(text):
    """轻量级 PII 脱敏：基于正则替换手机号和电子邮箱。"""
    # 11 位手机号：1 开头，第二位 3-9，后面 9 位数字
    text = re.sub(r"1[3-9]\d{9}", "[PHONE_MASK]", text)
    # 电子邮箱：本地部分@域名部分.顶级域名
    text = re.sub(
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        "[EMAIL_MASK]",
        text,
    )
    return text


def chunk_and_mask(input_path, output_path, chunk_size=500, chunk_overlap=50):
    """
    读取清洗后的 JSONL，执行 PII 脱敏 + 语义切分 + 元数据注入，输出 chunked JSONL。
    """
    # 使用中文友好的分隔符：优先在段落、换行、句号、逗号、空格处切分
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
        separators=["\n\n", "\n", "。", "，", " ", ""],
    )

    results = []

    with open(input_path, "r", encoding="utf-8") as f_in:
        for line in f_in:
            if not line.strip():
                continue

            record = json.loads(line)
            url = record.get("url", "")
            text = record.get("text", "")

            # ---------- 1. PII 脱敏 ----------
            masked_text = mask_pii(text)

            # ---------- 2. 语义切分 ----------
            chunks = splitter.split_text(masked_text)

            # ---------- 3. 元数据注入 ----------
            for idx, chunk_text in enumerate(chunks):
                item = {
                    "chunk_id": str(uuid.uuid4())[:8],
                    "text": chunk_text,
                    "metadata": {
                        "source_url": url,
                        "chunk_index": idx,
                        "char_count": len(chunk_text),
                    },
                }
                results.append(item)

    # ---------- 4. 写入结果 ----------
    with open(output_path, "w", encoding="utf-8") as f_out:
        for item in results:
            f_out.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"\n切分完成：共生成 {len(results)} 个 chunk，保存至 {output_path}")
    # 打印前 3 条样本的摘要
    for item in results[:3]:
        meta = item["metadata"]
        print(
            f"  - chunk_id={item['chunk_id']}, "
            f"index={meta['chunk_index']}, "
            f"chars={meta['char_count']}, "
            f"url={meta['source_url'][:50]}..."
        )


if __name__ == "__main__":
    chunk_and_mask("data/02_cleaned.jsonl", "data/03_chunked.jsonl")
