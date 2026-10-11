# -*- coding: utf-8 -*-
"""声音质检：基频（F0）性别检测 + 多说话人检测 + 与爱豆实际性别比对。

原理：对参考音频的浊音帧做自相关基频估计，取中位数：
  男声典型 85-155Hz，女声典型 165-255Hz。明显错位 → 素材里是别的人声（男旁白/多人）。
输出：不匹配清单（可直接用于重抓决策）+ JSON 报告。

用法: python audit_gender.py [--only pid1,pid2] [--json 路径]
"""
import argparse
import io
import json
import os
import sys
import wave
from pathlib import Path

import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import paths

VOICES = paths.path("voices_dir")


def read_wav(path):
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        y = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        if ch > 1:
            y = y.reshape(-1, ch).mean(axis=1)
    return y, sr


def est_f0_median(y, sr, fmin=70, fmax=400, frame_ms=40, hop_ms=20):
    """自相关基频估计：返回 (中位 F0, 浊音帧占比, F0 四分位距)。"""
    fl = int(sr * frame_ms / 1000)
    hop = int(sr * hop_ms / 1000)
    f0s = []
    n = max(1, (len(y) - fl) // hop)
    lag_min, lag_max = int(sr / fmax), int(sr / fmin)
    for i in range(n):
        fr = y[i * hop: i * hop + fl]
        rms = float(np.sqrt((fr ** 2).mean()))
        if rms < 0.02:  # 静音帧跳过
            continue
        fr = fr - fr.mean()
        ac = np.correlate(fr, fr, mode="full")[fl - 1:]
        if ac[0] <= 0:
            continue
        seg = ac[lag_min:lag_max]
        if not len(seg):
            continue
        lag = int(np.argmax(seg)) + lag_min
        peak = ac[lag] / ac[0]
        if peak < 0.35:  # 相关性太低（非周期/噪声）
            continue
        f0s.append(sr / lag)
    if len(f0s) < 8:
        return None, 0.0, None
    f0s = np.array(f0s)
    return float(np.median(f0s)), len(f0s) / max(1, n), float(np.percentile(f0s, 75) - np.percentile(f0s, 25))


def verdict(f0, expected):
    """返回 (判定, 说明)。expected: 'female' | 'male'"""
    if f0 is None:
        return "unknown", "浊音帧太少，无法判定"
    if expected == "female":
        if f0 < 140:
            return "mismatch", f"女爱豆但基频 {f0:.0f}Hz（男声区间）"
        if f0 < 160:
            return "warn", f"女爱豆但基频偏低 {f0:.0f}Hz（偏中性/低音女声）"
        return "ok", f"基频 {f0:.0f}Hz 符合女声"
    else:
        if f0 > 185:
            return "mismatch", f"男爱豆但基频 {f0:.0f}Hz（女声区间）"
        if f0 > 165:
            return "warn", f"男爱豆但基频偏高 {f0:.0f}Hz"
        return "ok", f"基频 {f0:.0f}Hz 符合男声"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--json", default=str(paths.path("work_dir") / "gender_audit.json"))
    args = ap.parse_args()
    only = {s.strip() for s in args.only.split(",") if s.strip()}

    reg = json.loads((VOICES / "registry.json").read_text(encoding="utf-8"))
    report = []
    for pid, per in sorted(reg["personas"].items()):
        if only and pid not in only:
            continue
        clips = per.get("clips") or []
        if not clips:
            continue
        best = max(clips, key=lambda c: c.get("score") or 0)
        f = VOICES / pid / best["file"]
        if not f.is_file():
            report.append({"pid": pid, "verdict": "error", "note": "文件缺失"})
            continue
        try:
            y, sr = read_wav(f)
        except Exception as e:
            report.append({"pid": pid, "verdict": "error", "note": f"读取失败 {e}"})
            continue
        f0, voiced, iqr = est_f0_median(y, sr)
        gender = per.get("gender") or "female"
        v, note = verdict(f0, gender)
        multi = "（F0 波动大，可能多说话人）" if (iqr and iqr > 60) else ""
        report.append({
            "pid": pid, "gender": gender, "f0": None if f0 is None else round(f0, 1),
            "voiced": round(voiced, 2), "iqr": None if iqr is None else round(iqr, 1),
            "verdict": v, "note": note + multi, "clip": best["file"],
        })

    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    mis = [r for r in report if r["verdict"] == "mismatch"]
    warn = [r for r in report if r["verdict"] == "warn"]
    unk = [r for r in report if r["verdict"] in ("unknown", "error")]
    ok = [r for r in report if r["verdict"] == "ok"]
    print(f"审计 {len(report)} 位：符合 {len(ok)} | 偏低/偏高 {len(warn)} | **性别错乱 {len(mis)}** | 无法判定 {len(unk)}")
    print("\n=== 性别错乱（必须重抓） ===")
    for r in mis:
        print(f"  {r['pid']:28s} {r['note']}")
    print("\n=== 偏低/偏高（建议复核） ===")
    for r in warn[:25]:
        print(f"  {r['pid']:28s} {r['note']}")
    if unk:
        print("\n=== 无法判定 ===")
        for r in unk[:10]:
            print(f"  {r['pid']:28s} {r.get('note','')}")
    print(f"\n完整报告: {args.json}")


if __name__ == "__main__":
    main()
