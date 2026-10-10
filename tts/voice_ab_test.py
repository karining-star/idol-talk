# -*- coding: utf-8 -*-
"""爱豆声音可懂度测试（2026-10-07，用户要求"测试所有爱豆的声音，能听懂英文算成功"）。

方法：每位爱豆用同一句英文合成语音 → faster-whisper 转写 → 与原文比对（词级相似度）。
相似度低 = 声音糊/电音/口音不对 → 失败。
可续跑：已测的跳过。结果 D:\\AI\\tmp\\voice_ab\\report.json
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import wave
from pathlib import Path

import numpy as np

TTS = "http://127.0.0.1:8780/v1/audio/stream"
TEXT = "I really missed you today, and I want to know how your day went."
OUT = Path(r"D:\AI\tmp\voice_ab")
OUT.mkdir(parents=True, exist_ok=True)
REG = Path(r"C:\Users\95242\Desktop\IdolTalk-voices\registry.json")


def synth(pid):
    url = f"{TTS}?voice={urllib.parse.quote(pid)}&text={urllib.parse.quote(TEXT)}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=240) as r:
        return r.read()


def read_wav_bytes(b):
    """兼容 WAV 和 MP3（缓存命中返回 MP3，未命中返回 WAV）。"""
    import io
    import os
    import subprocess
    import tempfile
    if b[:4] != b"RIFF":
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            f.write(b)
            tmp = f.name
        out = tmp + ".wav"
        subprocess.run([ff, "-y", "-i", tmp, "-ac", "1", "-ar", "16000", out], capture_output=True)
        try:
            with wave.open(out, "rb") as w:
                sr = w.getframerate()
                y = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        finally:
            for p in (tmp, out):
                try:
                    os.unlink(p)
                except OSError:
                    pass
        return y, sr
    with wave.open(io.BytesIO(b), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        y = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        if ch > 1:
            y = y.reshape(-1, ch).mean(1)
    return y, sr


def norm(s):
    return re.sub(r"[^a-z0-9 ]", " ", str(s).lower()).split()


def similarity(ref, hyp):
    """词级：1 - 编辑距离/参考词数（近似 WER）。"""
    a, b = norm(ref), norm(hyp)
    if not a:
        return 1.0
    dp = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, len(b) + 1):
            cur = dp[j]
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + (a[i - 1] != b[j - 1]))
            prev = cur
    return max(0.0, 1 - dp[len(b)] / len(a))


def main():
    pids = sorted(json.loads(REG.read_text(encoding="utf-8"))["personas"].keys())
    rep_path = OUT / "report.json"
    report = {}
    if rep_path.exists():
        try:
            report = json.loads(rep_path.read_text(encoding="utf-8"))
        except Exception:
            report = {}

    print("加载 faster-whisper（small/cpu/int8）…", flush=True)
    import os
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HOME", r"D:\AI\hf_cache")
    try:  # av(PyAV) 16+ 兼容 shim：剥掉 faster-whisper 仍会传的 metadata_errors 参数
        import av as _av
        if not getattr(_av.open, "_idol_compat", False):
            _orig = _av.open
            def _compat(*a, **kw):
                kw.pop("metadata_errors", None)
                return _orig(*a, **kw)
            _compat._idol_compat = True
            _av.open = _compat
    except Exception as e:
        print(f"[warn] av shim 未生效：{e}", flush=True)
    from faster_whisper import WhisperModel
    model = WhisperModel("small", device="cpu", compute_type="int8")

    todo = [p for p in pids if p not in report]   # 全测（含 aespa）
    print(f"待测 {len(todo)} / {len(pids)} 位", flush=True)
    for i, pid in enumerate(todo, 1):
        t0 = time.time()
        try:
            b = synth(pid)
            wav = OUT / f"{pid}.wav"
            wav.write_bytes(b)
            y, sr = read_wav_bytes(b)
            # 杂音/BGM 检测（对合成音频本身）
            from bgm_check import bgm_score as _bgm
            noise, _ = _bgm(y, sr)
            segs, _ = model.transcribe(str(wav), language="en", beam_size=1)
            hyp = " ".join(s.text for s in segs).strip()
            sim = similarity(TEXT, hyp)
            report[pid] = {"score": round(sim, 3), "noise": round(noise, 3), "dur": round(len(y) / sr, 1), "hyp": hyp[:120]}
            flag = "✅" if (sim >= 0.7 and noise <= 0.2) else ("⚠️" if sim >= 0.4 else "❌")
            print(f"  [{i}/{len(todo)}] {pid:26s} {flag} 听清={sim:.2f} 杂音={noise:.2f} ({time.time()-t0:.0f}s) {hyp[:50]}", flush=True)
        except Exception as e:
            report[pid] = {"score": None, "err": str(e)[:100]}
            print(f"  [{i}/{len(todo)}] {pid:26s} ❌ 失败: {str(e)[:70]}", flush=True)
        if i % 5 == 0:
            rep_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    rep_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    bad = sorted([(v.get("score") or 0, k) for k, v in report.items() if v.get("score") is not None], reverse=False)
    print(f"\n=== 可懂度最低的 20 位（要重做）===")
    for s, k in bad[:20]:
        print(f"  {k:26s} 可懂度={s}")


if __name__ == "__main__":
    main()
