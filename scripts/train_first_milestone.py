"""T001 变体#122+155: First Training Milestone 训练启动脚本。

骨架（变体#122）+ 日志/检查点目录逻辑（变体#155）。
本文件不含实际训练逻辑，不保存任何模型权重。
"""
import argparse
import json
import os
import logging

import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def setup_logging():
    """初始化日志目录与 logging 配置。"""
    log_dir = os.path.join(PROJECT_ROOT, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "train_first_milestone.log")
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return log_file


def setup_checkpoints():
    """初始化检查点目录（仅创建目录，不保存权重）。"""
    ckpt_dir = os.path.join(PROJECT_ROOT, "checkpoints")
    os.makedirs(ckpt_dir, exist_ok=True)
    return ckpt_dir


def parse_args():
    parser = argparse.ArgumentParser(description="QXEN First Training Milestone (骨架)")
    parser.add_argument("--config", default="configs/first_milestone.yaml",
                        help="训练配置路径")
    return parser.parse_args()


def load_config(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_data_placeholder(config):
    """数据加载占位：仅验证配置中的数据路径可访问。"""
    data_dir = config["data"]["train_path"]
    exists = os.path.exists(data_dir)
    print(f"[骨架] 数据路径存在: {exists} ({data_dir})")
    return exists


def train_loop_placeholder(config):
    """训练循环占位：仅打印超参数，不执行训练。"""
    print(f"[骨架] batch_size={config['train']['batch_size']} "
          f"learning_rate={config['train']['learning_rate']} "
          f"epochs={config['train']['epochs']}")
    print("[骨架] 训练循环占位 - 未执行实际训练")


def main():
    args = parse_args()
    cfg = load_config(args.config)
    log_file = setup_logging()
    ckpt_dir = setup_checkpoints()
    logging.info("训练启动脚本初始化: config=%s log=%s ckpt=%s",
                 args.config, log_file, ckpt_dir)
    load_data_placeholder(cfg)
    train_loop_placeholder(cfg)
    print(f"[骨架] 日志: {log_file} | 检查点目录: {ckpt_dir}")
    print("[骨架] PASS: 脚本可运行, 无训练发生")


if __name__ == "__main__":
    main()
