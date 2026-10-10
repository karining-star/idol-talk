# -*- coding: utf-8 -*-
"""从微博搜索抓图（m.weibo.cn，无需登录）。

用法：
  python fetch_weibo.py --query "宫胁咲良" --out "C:\...\weibo-sakura" [--limit 30]
"""
import argparse
import asyncio
import io
import re
import sys
from pathlib import Path
from urllib.parse import quote

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from patchright.async_api import async_playwright

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


def full_size(url):
    """把缩略图 URL 换成原图：/orj360/ 或 /thumb150/ → /large/"""
    return re.sub(r"/(orj\d+|thumb\d+|small|mw\d+)/", "/large/", url)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=30)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    url = "https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D" + quote(args.query)
    found = {}  # 原图 key -> url

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 480, "height": 900}, user_agent=UA,
            is_mobile=True, has_touch=True,
        )
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)

        for step in range(12):
            imgs = await page.evaluate(
                """() => [...document.querySelectorAll('img')]
                     .map(i => i.src).filter(s => s && s.includes('wx') && s.includes('sinaimg'))"""
            )
            for src in imgs:
                full = full_size(src.split("?")[0])
                key = full.rsplit("/", 1)[-1]
                if key not in found:
                    found[key] = full
            if len(found) >= args.limit * 2:
                break
            await page.mouse.wheel(0, 1000)
            await page.wait_for_timeout(1300)

        print(f"共收集 {len(found)} 张候选图，开始下载前 {args.limit} 张…", flush=True)
        saved = 0
        for key, u in found.items():
            if saved >= args.limit:
                break
            dest = out / f"{saved+1:03d}.jpg"
            try:
                resp = await context.request.get(
                    u, headers={"Referer": "https://weibo.com/"}, timeout=30000
                )
                if resp.ok:
                    body = await resp.body()
                    if len(body) > 8192:
                        dest.write_bytes(body)
                        saved += 1
            except Exception as e:
                print(f"  [失败] {type(e).__name__}: {str(e)[:60]}")
        print(f"完成：下载 {saved} 张 → {out}", flush=True)
        await browser.close()


asyncio.run(main())
