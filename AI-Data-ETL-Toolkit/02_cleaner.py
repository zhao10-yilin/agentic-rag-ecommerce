import json
import re
from datasketch import MinHash, MinHashLSH


def clean_text(text):
    """基础清洗：去除多余连续换行符、不可见字符，并去除首尾空白。"""
    # 去除控制字符等不可见字符（保留正常换行、制表符）
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # 将 2 个及以上的连续换行符合并为 1 个
    text = re.sub(r"\n{2,}", "\n", text)
    # 去除首尾空白
    text = text.strip()
    return text


def get_3grams(text):
    """将文本切分为 3-gram（中文每 3 个字符为一个词组）。"""
    return [text[i : i + 3] for i in range(len(text) - 2)]


def process_minhash_dedup(input_path, output_path, threshold=0.85):
    """
    读取原始 JSONL，执行基础清洗 + MinHash LSH 去重，输出清洗后的 JSONL。

    参数:
        input_path:  原始数据路径，如 data/01_raw.jsonl
        output_path: 清洗后输出路径，如 data/02_cleaned.jsonl
        threshold:   MinHash LSH 相似度阈值，默认 0.85
    """
    # 初始化 LSH：num_perm=128 是 MinHash 签名的标准长度
    lsh = MinHashLSH(threshold=threshold, num_perm=128)

    cleaned_records = []
    total = 0
    deduped = 0

    with open(input_path, "r", encoding="utf-8") as f_in:
        for line in f_in:
            if not line.strip():
                continue

            record = json.loads(line)
            total += 1

            # ---------- 1. 基础清洗 ----------
            raw_text = record.get("text", "")
            record["text"] = clean_text(raw_text)

            text = record["text"]

            # ---------- 2. 生成 3-gram ----------
            ngrams = get_3grams(text)
            if len(ngrams) < 3:
                # 文本过短，不足以生成可靠签名，直接保留
                cleaned_records.append(record)
                continue

            # ---------- 3. 生成 MinHash 签名 ----------
            m = MinHash(num_perm=128)
            for ngram in ngrams:
                m.update(ngram.encode("utf-8"))

            # ---------- 4. LSH 查重 ----------
            similar_keys = lsh.query(m)
            if similar_keys:
                deduped += 1
                title = record.get("title", "无标题")
                print(f"\033[91m[拦截] 发现相似文章被拦截：{title}\033[0m")
                continue

            # ---------- 5. 新文档入库 ----------
            doc_key = record.get("url", f"doc_{total}")
            lsh.insert(doc_key, m)
            cleaned_records.append(record)

    # ---------- 6. 写入结果 ----------
    with open(output_path, "w", encoding="utf-8") as f_out:
        for rec in cleaned_records:
            f_out.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"\n处理完成：总共 {total} 篇，去重 {deduped} 篇，保留 {len(cleaned_records)} 篇")
    print(f"结果已保存至 {output_path}")


if __name__ == "__main__":
    process_minhash_dedup("data/01_raw.jsonl", "data/02_cleaned.jsonl")
