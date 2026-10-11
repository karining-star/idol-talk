# -*- coding: utf-8 -*-
"""批量降噪：对爱豆的参考音频跑 干音处理链（高通70Hz + afftdn + arnndn 神经降噪）。

原文件备份到 <backup_dir>\<pid>\（默认仓库下 voice-refs-backup/），处理后就地替换。
降噪不改变语音内容 → prompt_text 继续有效；但合成缓存必须清（音色变了）。

⚠️ 只处理"新抓的、未降噪的"素材！已降过噪的（如吉赛尔等 31 位豁免重抓的）不要重复跑。
用法: python batch_denoise.py [--only pid1,pid2,...]   # 不给 --only 则处理全部（谨慎）
"""
import argparse
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import imageio_ffmpeg

import paths

FF = imageio_ffmpeg.get_ffmpeg_exe()
VOICES = paths.path("voices_dir")
BACKUP = paths.path("backup_dir")

# 三级降噪链：高通 + 频谱 + 神经网络。arnndn 模型没配/不在位时自动降为前两级。
_rnnn = paths.maybe_path("rnnn_model")
if _rnnn and _rnnn.is_file():
    _m = str(_rnnn).replace("\\", "/").replace(":", "\\:")   # ffmpeg 滤镜里的 Windows 路径写法
    FILTER = f"highpass=f=70,afftdn=nf=-38:nr=12,arnndn=m='{_m}'"
else:
    FILTER = "highpass=f=70,afftdn=nf=-38:nr=12"
    print("[warn] 未配置 arnndn 神经降噪模型（config.json 的 rnnn_model），本轮只用前两级", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="逗号分隔的 pid 白名单；为空则处理全部")
    args = ap.parse_args()
    only = {s.strip() for s in args.only.split(",") if s.strip()}

    BACKUP.mkdir(parents=True, exist_ok=True)
    done = 0
    failed = []
    skipped = 0
    for folder in sorted(VOICES.iterdir()):
        if not folder.is_dir() or folder.name.startswith("_") or folder.name in ("raw", "qc"):
            continue
        if only and folder.name not in only:
            skipped += 1
            continue
        wavs = sorted(folder.glob("ref*.wav"))
        if not wavs:
            continue
        bdir = BACKUP / folder.name
        bdir.mkdir(parents=True, exist_ok=True)
        for w in wavs:
            bak = bdir / w.name
            if not bak.is_file():
                shutil.copy2(w, bak)  # 只备份一次（重跑不覆盖备份）
            tmp = w.with_suffix(".dn.wav")
            r = subprocess.run(
                [FF, "-y", "-i", str(w), "-af", FILTER, "-ac", "1", "-ar", "16000",
                 "-c:a", "pcm_s16le", str(tmp)], capture_output=True)
            if r.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 10000:
                shutil.move(str(tmp), str(w))
                done += 1
            else:
                failed.append(f"{folder.name}/{w.name}")
                tmp.unlink(missing_ok=True)
        print(f"[{folder.name}] 完成（累计 {done} 个文件）", flush=True)
    scope = f"白名单 {len(only)} 位" if only else "全部"
    print(f"\n全部完成（范围：{scope}）：处理 {done} 个参考音频；跳过 {skipped} 位；失败 {len(failed)} 个")
    if failed:
        print("失败清单:", failed[:10])


if __name__ == "__main__":
    main()
