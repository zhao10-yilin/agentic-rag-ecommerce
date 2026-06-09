import json
import os
import re
import time
from openai import OpenAI

# ---------- 加载 .env 文件（如果存在） ----------
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # 未安装 python-dotenv 时静默跳过
# -----------------------------------------------

# ========== 全局配置：优先级 环境变量 > .env 文件 > 默认值 ==========
# 支持 DeepSeek、硅基流动(SiliconFlow) 等兼容 OpenAI 协议的 API
API_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
API_KEY = os.getenv("OPENAI_API_KEY", "your-api-key-here")
MODEL_NAME = os.getenv("OPENAI_MODEL", "deepseek-chat")
# =========================================================

SYSTEM_PROMPT = (
    "你是一个严谨的数据标注专家。请阅读以下文本，生成 1 个高质量的用户提问和你的专业回答。"
    "要求：问题必须切中要害，回答必须完全基于给定文本，绝不能捏造。"
    '输出必须是严格的 JSON 格式：{"question": "...", "answer": "..."}'
)

# 初始化 OpenAI 兼容客户端
client = OpenAI(base_url=API_BASE_URL, api_key=API_KEY)


def extract_json_from_markdown(raw_text):
    """
    从大模型返回的文本中提取 JSON。
    兼容以下情况：
      1. 纯 JSON 字符串
      2. ```json\n{...}\n```
      3. ```\n{...}\n```
    """
    raw = raw_text.strip()

    # 尝试匹配 markdown 代码块
    code_block_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if code_block_match:
        return code_block_match.group(1)

    # 尝试直接提取第一个 {...} 结构
    brace_match = re.search(r"(\{.*\})", raw, re.DOTALL)
    if brace_match:
        return brace_match.group(1)

    return raw


def call_llm_for_qa(text, max_retries=2):
    """
    调用大模型 API，基于给定文本生成 QA 对。
    如果返回非合法 JSON，自动重试最多 max_retries 次。

    返回:
        (question, answer) 元组；全部失败则抛出异常。
    """
    user_content = f"请基于以下文本生成 QA 对：\n\n{text}"

    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.3,
                max_tokens=1024,
            )

            raw = response.choices[0].message.content.strip()
            json_str = extract_json_from_markdown(raw)
            qa = json.loads(json_str)

            question = qa["question"]
            answer = qa["answer"]
            return question, answer

        except (json.JSONDecodeError, KeyError) as e:
            if attempt < max_retries:
                print(f"  [WARN] JSON 解析失败，第 {attempt + 1} 次重试... 错误: {e}")
                time.sleep(1)
                continue
            else:
                print(f"  [ERROR] 重试耗尽，无法解析 QA。原始返回:\n{raw}")
                raise RuntimeError(f"QA 解析失败: {e}") from e

        except Exception as e:
            print(f"  [ERROR] API 调用异常：{e}")
            raise

    return None, None


def generate_qa_dataset(input_path, output_path, max_chunks=3):
    """
    读取 chunked JSONL，为前 max_chunks 个 chunk 生成 QA 对，
    输出 Alpaca SFT 标准格式数据集。

    SFT 格式:
        {
            "instruction": "用户问题",
            "input": "",
            "output": "模型回答",
            "chunk_id": "原始块ID"
        }
    """
    qa_records = []

    with open(input_path, "r", encoding="utf-8") as f_in:
        for i, line in enumerate(f_in):
            if i >= max_chunks:
                break

            if not line.strip():
                continue

            chunk = json.loads(line)
            chunk_id = chunk.get("chunk_id", "")
            text = chunk.get("text", "")

            print(f"[{i + 1}/{max_chunks}] 正在处理 chunk {chunk_id}...")

            try:
                question, answer = call_llm_for_qa(text)

                record = {
                    "instruction": question,
                    "input": "",
                    "output": answer,
                    "chunk_id": chunk_id,
                }
                qa_records.append(record)
                print(f"  [OK] 生成 QA：{question[:60]}...")

            except Exception as e:
                print(f"  [SKIP] 跳过 chunk {chunk_id}，原因：{e}")
                continue

    with open(output_path, "w", encoding="utf-8") as f_out:
        for rec in qa_records:
            f_out.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"\n完成：成功生成 {len(qa_records)} 条 QA 数据，保存至 {output_path}")


if __name__ == "__main__":
    generate_qa_dataset("data/03_chunked.jsonl", "data/04_qa_dataset.jsonl")
