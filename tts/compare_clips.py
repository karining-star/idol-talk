# -*- coding: utf-8 -*-
"""全量体检：对比【当前素材】与【旧备份】的"干净度"，找出被梦女二创/音乐搞差的爱豆。

干净度 = 浊音率(高好) × 100 − IQR(低好) × 0.5   （0~100，越高越干净）
输出：被搞差的清单（当前明显不如备份）→ 可一键还原备份或按新规则重抓。
用法: python compare_clips.py [--only pid1,pid2]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from audit_gender import est_f0_median, read_wav  # noqa: E402

VOICES = Path(r"C:\Users\95242\Desktop\IdolTalk-voices")
BACKUP = Path(r"D:\AI\voice-refs-backup")


def clip_stats(files):
    rows = []
    for f in files:
        try:
            y, sr = read_wav(f)
        except Exception:
            continue
        if len(y) / sr < 2:
            continue
        f0, voiced, iqr = est_f0_median(y, sr)
        rows.append({"f0": f0, "voiced": voiced, "iqr": iqr, "dur": round(len(y) / sr, 2)})
    return rows


def clean_score(rows):
    """0~100，越高越干净（清澈人声、无音乐/BGM）。"""
    if not rows:
        return None, {}
    iqrs = [r["iqr"] for r in rows if r["iqr"] is not None]
    vs = [r["voiced"] for r in rows]
    mean_iqr = float(np.mean(iqrs)) if iqrs else 200.0
    mean_v = float(np.mean(vs))
    score = mean_v * 100 - mean_iqr * 0.5
    return round(score, 1), {"iqr": round(mean_iqr), "voiced": round(mean_v, 2), "n": len(rows)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    only = {s.strip() for s in args.only.split(",") if s.strip()}

    result = []
    for folder in sorted(VOICES.iterdir()):
        if not folder.is_dir() or folder.name.startswith("_") or folder.name in ("raw", "qc"):
            continue
        pid = folder.name
        if only and pid not in only:
            continue
        cur_files = sorted(folder.glob("ref*.wav"))
        bak_dir = BACKUP / pid
        bak_files = sorted(bak_dir.glob("ref*.wav")) if bak_dir.is_dir() else []
        if not cur_files:
            continue
        cur_rows = clip_stats(cur_files)
        cur_s, cur_m = clean_score(cur_rows)
        if not bak_files:
            result.append({"pid": pid, "cur": cur_s, "bak": None, "delta": None, "cur_m": cur_m, "bak_m": {}})
            continue
        bak_rows = clip_stats(bak_files)
        bak_s, bak_m = clean_score(bak_rows)
        delta = None if (cur_s is None or bak_s is None) else round(cur_s - bak_s, 1)
        result.append({"pid": pid, "cur": cur_s, "bak": bak_s, "delta": delta, "cur_m": cur_m, "bak_m": bak_m})

    # 被搞差的：当前比备份差 >= 8 分
    worse = [r for r in result if r["delta"] is not None and r["delta"] <= -8]
    worse.sort(key=lambda r: r["delta"])
    print(f"体检 {len(result)} 位（有备份可比的 {sum(1 for r in result if r['bak'] is not None)} 位）")
    print(f"\n=== 被重抓搞差（当前比旧版差 ≥8 分）{len(worse)} 位 → 建议还原旧版 ===")
    for r in worse:
        print(f"  {r['pid']:26s} 现状{r['cur']:6.1f}(IQR{r['cur_m'].get('iqr')} 浊{r['cur_m'].get('voiced')})"
              f" ← 旧版{r['bak']:6.1f}(IQR{r['bak_m'].get('iqr')} 浊{r['bak_m'].get('voiced')})  差{r['delta']}")

    json_path = Path(r"D:\AI\tmp\clip_compare.json")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n完整报告: {json_path}")
    # 供脚本消费的纯 pid 列表
    (Path(r"D:\AI\tmp\worse_ids.txt")).write_text(",".join(r["pid"] for r in worse), encoding="utf-8")
    print(f"还原名单: D:\\AI\\tmp\\worse_ids.txt（{len(worse)} 位）")


if __name__ == "__main__":
    main()
