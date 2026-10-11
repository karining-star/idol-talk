# -*- coding: utf-8 -*-
"""本机路径解析：tts/config.json → 环境变量 → 仓库内相对默认值。

仓库**不提交任何含个人目录的绝对路径**，别人 clone 下来改 config.json 就能跑。
本机使用时在 tts/config.json 里写自己的路径（该文件已被 .gitignore 排除），
或设对应环境变量。相对路径一律挂到仓库根目录。
"""
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent      # tts/
REPO_DIR = BASE_DIR.parent                      # 仓库根

# key -> (环境变量名, 仓库内相对默认值；空串表示未配置时需报错)
SPEC = {
    "voices_dir":  ("IDOLTALK_VOICES", "voices"),
    "backup_dir":  ("IDOLTALK_BACKUP", "voice-refs-backup"),
    "work_dir":    ("IDOLTALK_WORK", "work"),
    "hf_home":     ("HF_HOME", ".hf_cache"),
    "app_html":    ("IDOLTALK_APP_HTML", ""),
    "covers_meta": ("IDOLTALK_COVERS_META", ""),
    "rnnn_model":  ("IDOLTALK_RNNN", ""),
}

_cache = None


def config() -> dict:
    """读脚本同目录的 config.json（本地文件，不入库）。"""
    global _cache
    if _cache is None:
        p = BASE_DIR / "config.json"
        try:
            _cache = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        except Exception as e:
            print(f"[warn] tts/config.json 解析失败，改用默认值：{e}", flush=True)
            _cache = {}
    return _cache


def path(key: str) -> Path:
    """解析路径；未配置且无默认值时抛 KeyError。"""
    env, default = SPEC[key]
    raw = config().get(key) or os.environ.get(env) or default
    if not raw:
        raise KeyError(f"{key} 未配置：请在 tts/config.json 里填，或设环境变量 {env}")
    p = Path(raw)
    return p if p.is_absolute() else (REPO_DIR / p)


def dir_of(key: str) -> Path:
    """同 path()，但顺带建目录（写文件用）。"""
    p = path(key)
    p.mkdir(parents=True, exist_ok=True)
    return p


def maybe_path(key: str):
    """同 path()，但未配置时返回 None（用于可选资源，缺失时调用方自行降级）。"""
    try:
        return path(key)
    except KeyError:
        return None
