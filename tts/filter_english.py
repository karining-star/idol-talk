# -*- coding: utf-8 -*-
"""只保留【连续英语】的参考片段（用户要求：素材必须是英语素材、连续英语）。

判据：prompt_lang=='en' 且 英文字母数 / (英文字母+中韩字符) >= 0.85 —— 即几乎全是英文，不掺韩语/中文。
没有合格英语片段的爱豆 → 清空 clips（宁缺毋滥，留待再抓）。
用法: python filter_english.py --all   或   python filter_english.py pid1,pid2
"""
import json
import re
import sys
from pathlib import Path

V = Path(r"C:\Users\95242\Desktop\IdolTalk-voices")
REG = V / "registry.json"


def en_ratio(t):
    en = len(re.findall(r"[A-Za-z]", t))
    other = len(re.findall(r"[\u4e00-\u9fff\uac00-\ud7af]", t))   # 中文 + 韩文音节
    return en / (en + other) if (en + other) else 0.0


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "--all"
    reg = json.loads(REG.read_text(encoding="utf-8"))
    pids = list(reg["personas"]) if arg == "--all" else [p.strip() for p in arg.split(",") if p.strip()]
    detail = []
    for pid in pids:
        per = reg["personas"].get(pid)
        if not per:
            continue
        clips = per.get("clips") or []
        if not clips:
            continue
        keep = [c for c in clips if (c.get("prompt_lang") == "en") and en_ratio(c.get("prompt_text") or "") >= 0.75]
        if len(keep) < len(clips) or len(keep) == 0:
            detail.append(f"  {pid:24s} {len(clips)}→{len(keep)} 段")
        per["clips"] = keep
        if not keep:
            per["status"] = "sourcing"
    REG.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    # 同步每人 meta.json
    for pid in pids:
        mp = V / pid / "meta.json"
        if mp.exists():
            try:
                m = json.loads(mp.read_text(encoding="utf-8"))
                m["clips"] = reg["personas"].get(pid, {}).get("clips") or []
                mp.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
            except Exception:
                pass
    empty = [p for p in pids if not (reg["personas"].get(p, {}).get("clips"))]
    print(f"过滤 {len(pids)} 位：{len(detail)} 位被削减")
    for d in detail[:30]:
        print(d)
    print(f"\n★ 现在没有连续英语片段的（需再抓）：{len(empty)} 位")
    Path(r"D:\AI\tmp\no_english.txt").write_text(",".join(empty), encoding="utf-8")


if __name__ == "__main__":
    main()
