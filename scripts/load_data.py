"""最小数据加载脚本 (T001 变体#53): 从 data/ 读取样本并输出批次形状。
复用 src.distiller.data 管线, 支持 --check 模式, 不启动训练。
"""
import os, sys
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
from src.distiller.data import create_dataloader, load_data, preprocess
import numpy as np

def main():
    data_path = os.path.join(PROJECT_ROOT, "data", "real_samples.jsonl")
    raw = load_data(data_path, seed=42, n_samples=16, n_features=4)
    loader = create_dataloader(raw, batch_size=8, shuffle=False, seed=42)
    batch = next(iter(loader))
    xs = [preprocess(s)[0] for s in raw[:8]]
    arr = np.array(xs)
    print(f"[load_data] 加载样本数: {len(raw)}")
    print(f"[load_data] 批次形状: {arr.shape}")
    print(f"[load_data] batch 元素类型: {type(batch).__name__}")
    print(f"[load_data] PASS: 至少一个批次成功读取")

if __name__ == "__main__":
    main()
