"""
诊断脚本: 逐行 import，定位 segfault 发生在哪个库
"""
import sys
print(">>> Python started", flush=True)

print(">>> [1/6] importing torch...", flush=True)
import torch
print(">>> [1/6] torch OK, version:", torch.__version__, "CUDA:", torch.version.cuda, flush=True)

print(">>> [2/6] importing json...", flush=True)
import json
print(">>> [2/6] json OK", flush=True)

print(">>> [3/6] importing transformers...", flush=True)
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
print(">>> [3/6] transformers OK", flush=True)

print(">>> [4/6] importing peft...", flush=True)
from peft import get_peft_model, LoraConfig, PeftModel, prepare_model_for_kbit_training
print(">>> [4/6] peft OK", flush=True)

print(">>> [5/6] importing datasets...", flush=True)
from datasets import load_dataset
print(">>> [5/6] datasets OK", flush=True)

print(">>> [6/6] importing bitsandbytes...", flush=True)
import bitsandbytes
print(">>> [6/6] bitsandbytes OK, version:", bitsandbytes.__version__, flush=True)

print(">>> ALL IMPORTS PASSED", flush=True)
