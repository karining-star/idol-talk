# -*- coding: utf-8 -*-
"""从 B站 抓爱豆直播/采访音频（用户要求：听不清的从 B站 搜"爱豆 直播"重抓）。

复用 fetch_idol_voices.py 的静音切片 + BGM/性别质检 + registry 入库。
用法: python fetch_bilibili.py --only pid1,pid2 [--per-idol 3] [--add]
"""
import argparse
import asyncio
import importlib.util
import subprocess
import sys
import urllib.parse
from pathlib import Path

import imageio_ffmpeg

HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("dv", HERE / "fetch_idol_voices.py")
dv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dv)

FF = imageio_ffmpeg.get_ffmpeg_exe()
PY = sys.executable


async def search_bili(page, keyword, n):
    """B站搜索 → 前 n 个视频 {href,title}（去重）。"""
    url = "https://search.bilibili.com/all?keyword=" + urllib.parse.quote(keyword)
    try:
        await page.goto(url, timeout=60000)
    except Exception as e:
        print(f"  [打开失败] {type(e).__name__}", flush=True)
        return []
    await page.wait_for_timeout(6000)
    items = await page.evaluate(
        """() => {
            const out = [];
            document.querySelectorAll('a[href*="/video/"]').forEach(a => {
                const href = (a.href || '').split('?')[0];
                if (!/\\/video\\/BV/.test(href)) return;
                const t = (a.getAttribute('title') || a.innerText || '').replace(/\\s+/g, ' ').trim();
                out.push({ href, title: t });
            });
            return out;
        }"""
    )
    seen, res = set(), []
    for it in items:
        if it["href"] in seen:
            continue
        seen.add(it["href"])
        res.append(it)
    return res[:n]


def ytdlp_audio(url, dest):
    """yt-dlp 下载 B站音频到 dest（.m4a）。成功返回 True。"""
    r = subprocess.run(
        [PY, "-m", "yt_dlp", "-f", "bestaudio", "-o", str(dest), "--no-warnings",
         "--no-playlist", "--ffmpeg-location", FF, url],
        capture_output=True, text=True, errors="replace", timeout=1200,
    )
    return r.returncode == 0 and dest.exists() and dest.stat().st_size > 20000


async def process_idol(page, idol, args, registry):
    pid, zh, stage = idol["pid"], idol["zh"], idol["stage"]
    folder, raw_folder = dv.OUT_DIR / pid, dv.RAW_DIR / pid
    existing = sorted(folder.glob("ref*.wav")) if folder.exists() else []
    if existing and not args.add:
        print(f"{zh} · {stage} —— 已有 {len(existing)} 段，跳过", flush=True)
        return
    raw_folder.mkdir(parents=True, exist_ok=True)

    # 用户要求：素材必须是【连续的英语】+ 优先【单人】。实测对中文名，中文措辞（"英文采访"/"说英文"）
    # 比英文措辞命中率高，所以中文措辞排前面。
    queries = [
        f"{zh} {stage} 英文采访", f"{zh} {stage} 说英文",
        f"{zh} {stage} 英语合集", f"{stage} english interview",
        f"{zh} {stage} 单人 英文", f"{stage} solo interview",
    ]
    print(f"\n[{zh} · {stage}] B站搜索：{' / '.join(queries)}", flush=True)
    segs, sources = [], dv.load_sources(raw_folder)
    nums = [int(p.stem) for p in raw_folder.glob("*") if p.stem.isdigit()]
    n = (max(nums) + 1) if nums else 1
    downloaded = 0   # 关键：不能"够 5 段就停"——抓取时还不知道是不是英语（英文判断在后面过滤步），
                     # 否则第一个非英语视频就把配额吃光，永远抓不到英语。改成下满 per_idol 个视频。

    for q in queries:
        if downloaded >= args.per_idol:
            break
        cands = await search_bili(page, q, args.per_idol)
        print(f"  [B站] {q} → {len(cands)} 个视频", flush=True)
        for c in cands:
            if downloaded >= args.per_idol:
                break
            dest = raw_folder / f"{n:02d}.m4a"
            if ytdlp_audio(c["href"], dest):
                sources[c["href"]] = c["title"]
                dv.save_sources(raw_folder, sources)
                got = dv.harvest_from(dest, raw_folder, sources, idol["gender"])
                segs += got
                downloaded += 1
                print(f"    ↓ {c['title'][:38]} → 本视频 {len(got)} 段 / 累计 {dv.kept_count(segs)}", flush=True)
                n += 1
            else:
                print(f"    ✗ 下载失败 {c['href']}", flush=True)

    dv.finalize_idol(pid, idol["gender"], queries, raw_folder, segs, registry, existing, args.add)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="pid 逗号列表")
    ap.add_argument("--per-idol", type=int, default=5, help="每位最多下几个视频")
    ap.add_argument("--add", action="store_true", help="追加模式（保留已有切片）")
    args = ap.parse_args()
    only = {s.strip() for s in args.only.split(",") if s.strip()}

    idols = [i for i in dv.load_idols() if not only or i["pid"] in only]
    registry = dv.load_registry()
    print(f"B站抓取 {len(idols)} 位", flush=True)

    from patchright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        for i, idol in enumerate(idols, 1):
            print(f"\n[{i}/{len(idols)}]", flush=True)
            try:
                await process_idol(page, idol, args, registry)
            except Exception as e:
                print(f"  [异常] {type(e).__name__}: {str(e)[:120]}", flush=True)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
