"""
诊断脚本 v2: 定位 datasets 内部哪个子模块触发 segfault
"""
import sys
print(">>> Python started", flush=True)

import torch
print(">>> torch OK", flush=True)

# datasets 内部依赖链: pyarrow, multiprocess, dill, aiohttp, etc.
# 逐个排查
print(">>> [1] importing pyarrow...", flush=True)
import pyarrow
print(">>> [1] pyarrow OK, version:", pyarrow.__version__, flush=True)

print(">>> [2] importing multiprocess...", flush=True)
import multiprocess
print(">>> [2] multiprocess OK", flush=True)

print(">>> [3] importing dill...", flush=True)
import dill
print(">>> [3] dill OK", flush=True)

print(">>> [4] importing fsspec...", flush=True)
import fsspec
print(">>> [4] fsspec OK", flush=True)

print(">>> [5] importing huggingface_hub...", flush=True)
import huggingface_hub
print(">>> [5] huggingface_hub OK", flush=True)

print(">>> [6] importing aiohttp...", flush=True)
import aiohttp
print(">>> [6] aiohttp OK", flush=True)

print(">>> [7] now importing datasets (this may crash)...", flush=True)
from datasets import load_dataset
print(">>> [7] datasets OK", flush=True)

print(">>> ALL CLEAR - datasets import succeeded", flush=True)
