# -*- coding: utf-8 -*-
"""BGM/音乐检测器（开发验证用）：判断一段音频里有没有背景音乐。

原理：
  ① 包络调制率 —— 人说话有 2~8Hz 的音节节奏（调制能量占比高）；纯音乐/持续 BGM 调制弱。
  ② 低频占比 —— BGM（鼓/贝斯）在 30~140Hz 有明显能量，清人声几乎没有。
  ③ 静音抬升 —— 说话词间有真静音；若词间仍有能量（底噪/CD 音），说明垫了 BGM。
bgm_score 越高 = 越可能有 BGM。
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from audit_gender import read_wav  # noqa: E402


def bgm_score(y, sr):
    if len(y) < 400:      # 太短，判不出
        return 0.0, {}
    # ---- ① 包络调制率（2~8Hz 占比）----
    hop = int(sr * 0.02)
    n = len(y) // hop
    env = np.sqrt((y[: n * hop].reshape(n, hop) ** 2).mean(1)) if n > 4 else None
    mod = 0.0
    quiet = 0.0
    if env is not None and len(env) > 16:
        e = env - env.mean()
        E = np.abs(np.fft.rfft(e * np.hanning(len(e))))
        fr = np.fft.rfftfreq(len(e), 0.02)
        tot = E[(fr >= 0.3) & (fr <= 30)].sum() + 1e-9
        syll = E[(fr >= 2) & (fr <= 8)].sum()
        mod = float(syll / tot)                       # 语音 ~0.3-0.6；音乐偏低
        p10 = float(np.percentile(env, 10))
        p90 = float(np.percentile(env, 90)) + 1e-9
        quiet = p10 / p90                             # 清语音静音段很低；垫 BGM 会抬高

    # ---- ② 低频占比（30~140Hz，避开男声基频下沿）----
    Y = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    fr = np.fft.rfftfreq(len(y), 1 / sr)
    tot_e = Y[(fr >= 30) & (fr <= 8000)].sum() + 1e-9
    low = Y[(fr >= 30) & (fr <= 140)].sum()
    low_r = float(low / tot_e)

    # 组合（越高越可能有 BGM）
    score = 0.0
    score += max(0.0, (0.40 - mod) / 0.40) * 0.45     # 调制低 → 加分
    score += min(1.0, low_r / 0.25) * 0.30            # 低频多 → 加分
    score += min(1.0, quiet / 0.15) * 0.25            # 静音抬升 → 加分
    return round(min(1.0, score), 3), {"mod": round(mod, 3), "low": round(low_r, 3), "quiet": round(quiet, 3)}


if __name__ == "__main__":
    V = Path(r"C:\Users\95242\Desktop\IdolTalk-voices")
    import json
    reg = json.loads((V / "registry.json").read_text(encoding="utf-8"))
    # 扫描全部：列出 bgm_score 最高的一批（最可能带 BGM）
    rows = []
    for pid, per in reg["personas"].items():
        clips = per.get("clips") or []
        if not clips:
            continue
        best = max(clips, key=lambda c: c.get("score") or 0)
        f = V / pid / best["file"]
        if not f.is_file():
            continue
        try:
            y, sr = read_wav(f)
        except Exception:
            continue
        if len(y) / sr < 2:
            continue
        s, d = bgm_score(y, sr)
        rows.append((s, pid, d))
    rows.sort(reverse=True)
    print("bgm_score 最高（最可能带 BGM）20 位：")
    for s, pid, d in rows[:20]:
        print(f"  {pid:26s} bgm={s:.3f}  调制={d['mod']} 低频={d['low']} 静音抬升={d['quiet']}")
    print("\nbgm_score 最低（最干净）10 位：")
    for s, pid, d in rows[-10:]:
        print(f"  {pid:26s} bgm={s:.3f}  调制={d['mod']} 低频={d['low']} 静音抬升={d['quiet']}")
