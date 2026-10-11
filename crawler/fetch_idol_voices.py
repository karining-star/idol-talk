# -*- coding: utf-8 -*-
"""
批量抓取抖音上各爱豆的说话素材（视频 → 16k 单声道切片），供 GPT-SoVITS 声音克隆用。

用法（在 crawler/ 目录下执行）：
  python fetch_idol_voices.py                        # 抓全部（断点续跑）
  python fetch_idol_voices.py --only aespa-giselle   # 只抓指定 id（可逗号分隔）
  python fetch_idol_voices.py --limit 5              # 只抓前 5 个（试跑用）
  python fetch_idol_voices.py --link aespa-giselle=https://v.douyin.com/xxxx/
  python fetch_idol_voices.py --add                  # 追加模式：已有切片也重抓，新切片续编号
  python fetch_idol_voices.py --purge-raw            # 清空 raw 原始视频目录后退出
  python fetch_idol_voices.py --ffmpeg D:/ff/bin/ffmpeg.exe

产物（目录由 tts/config.json 的 voices_dir 决定，默认仓库下 voices/）：
  <pid>/refNN.wav   16k 单声道 PCM 切片（5-20s，按打分取 top）
  <pid>/meta.json   搜索词 + 每段切片明细（file/dur/score/rms_db/src）
  raw/<pid>/NN.mp4  原始视频（验证完 --purge-raw 清理）
  registry.json     音色注册表（status: sourced / missing）
"""
import argparse
import asyncio
import io
import json
import math
try:
    import msvcrt  # Windows 跨进程文件锁（registry 并发写防护），非 Windows 退化为无锁
except ImportError:
    msvcrt = None
import os
import re
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path
from urllib.parse import quote, unquote

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from patchright.async_api import async_playwright
import numpy as np
try:
    from scipy import signal as scipy_signal
except Exception:
    scipy_signal = None

HERE = Path(__file__).resolve().parent
REPO = HERE.parent


def local_path(key, env, default):
    """本机路径：tts/config.json（本地、不入库）→ 环境变量 → 仓库内相对默认值。

    与 tts/paths.py 同一套约定；crawler/ 与 tts/ 是两个独立目录，各留一份读取逻辑。
    未配置且无默认值时返回 None。
    """
    raw = ""
    cfg = REPO / "tts" / "config.json"
    if cfg.is_file():
        try:
            raw = json.loads(cfg.read_text(encoding="utf-8")).get(key) or ""
        except Exception:
            raw = ""
    raw = str(raw or os.environ.get(env) or default or "")
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_absolute() else (REPO / p)


OUT_DIR = local_path("voices_dir", "IDOLTALK_VOICES", "voices")
APP_HTML = local_path("app_html", "IDOLTALK_APP_HTML", "app/js/data.js")
COVERS_META = local_path("covers_meta", "IDOLTALK_COVERS_META", "")
REGISTRY = OUT_DIR / "registry.json"
REGISTRY_LOCK = OUT_DIR / "registry.lock"   # 跨进程写锁，与 idol-tts/voice_batch.py 共用同一把
RAW_DIR = OUT_DIR / "raw"
PROFILE_DIR = str(Path(__file__).resolve().parent / "douyin-profile")  # 与 fetch_idol_photos.py 同一份登录态

QUERY_SUFFIXES = ["采访", "电台", "说话", "直播", "语音"]   # 用户要求：采访最好（专业录制、单人声、无 BGM）；梦女二创重罚
TITLE_BONUS_WORDS = ("音频", "语音", "asmr", "电台", "说话", "采访", "完整版", "原声")
# 梦女/二创/混剪带 BGM 或特效 → 克隆出来发噪（用户明确要求排除）
TITLE_BLOCK_WORDS = ("梦女", "二创", "混剪", "剪辑", "翻唱", "cover", "自制", "bgm", "背景音乐", "配乐", "ai翻唱", "ai cover", "头像", "壁纸", "模板",
                     "解说", "吐槽", "盘点", "争议", "热议", "网友", "科普", "八卦", "锐评", "reaction", "对比", "揭秘", "真相", "为什么", "为何", "竟然", "居然")
MAX_REFS = 5            # 每位爱豆最多保留几段切片
MIN_SEG = 3.0           # 切片最短秒数（GPT-SoVITS 要求参考音频 3-10 秒）
MAX_SEG = 10.0          # 切片最长秒数（同上，超出会被引擎拒绝）
SEG_PAD = 0.2           # 切片前后留白秒数
MIN_RAW_BYTES = 300 * 1024   # 下载小于 300KB 视为失败
MIN_KEEP_SCORE = 0.30   # 切片最低得分，低于它不保留（一段都没有时兜底留最好的一段）
SCROLL_STEPS = 10       # 搜索页最多滚动次数
SCROLL_PAUSE = 1.6      # 每次滚动后的等待秒数
LOAD_WAIT = 5.0         # 打开页面后的初始等待
IDOL_PAUSE = 4.0        # 每个爱豆之间的间隔（温柔一点）

SNIFF_RE = re.compile(r"douyinvod\.com|/aweme/v1/play|volcvideocc|bytecdn|\.mp4\?|video_id=", re.I)

_FFMPEG = ""


def find_ffmpeg():
    """统一找 ffmpeg：1) FFMPEG_PATH 环境变量 2) PATH 3) imageio_ffmpeg 4) 常见路径。"""
    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG
    cands = []
    env = os.environ.get("FFMPEG_PATH")
    if env:
        cands.append(Path(env))
    w = shutil.which("ffmpeg")
    if w:
        cands.append(Path(w))
    try:
        import imageio_ffmpeg
        cands.append(Path(imageio_ffmpeg.get_ffmpeg_exe()))
    except Exception:
        pass
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        cands.append(Path(local) / "imageio" / "ffmpeg" / "bin" / "ffmpeg.exe")
        cands.extend(Path(local).glob("Microsoft/WinGet/Packages/Gyan.FFmpeg*/**/ffmpeg.exe"))
    cands += [
        Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
        Path(r"D:\ffmpeg\bin\ffmpeg.exe"),
    ]
    for c in cands:
        try:
            if c.is_file():
                _FFMPEG = str(c)
                return _FFMPEG
        except Exception:
            continue
    raise RuntimeError(
        "找不到 ffmpeg。请任选其一解决：\n"
        "  1) winget install Gyan.FFmpeg（新开终端验证 ffmpeg -version）\n"
        "  2) set FFMPEG_PATH=C:\\路径\\bin\\ffmpeg.exe\n"
        "  3) python -m pip install imageio-ffmpeg\n"
        "  4) 运行本脚本时加 --ffmpeg 完整路径"
    )


def run_ffmpeg(args, timeout=300):
    """跑一次 ffmpeg，返回 (returncode, stderr)。"""
    ff = find_ffmpeg()
    proc = subprocess.run(
        [ff, "-hide_banner", "-loglevel", "info"] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    return proc.returncode, proc.stderr or ""


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower())


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def load_idols():
    """读人设表，支持两种来源，并与 covers_meta.json 键核对：

      · 桌面单文件版 IdolTalk.html —— 解析 const seeds = [...] + femaleGroups
      · 仓库版 app/js/data.js      —— 解析 var IDOLS = [{...}]（字段已齐全）

  返回 [{pid, group, zh, stage, gender}, ...]
    """
    text = APP_HTML.read_text(encoding="utf-8")
    idols = []

    if "const seeds = [" in text:
        block = text.split("const seeds = [", 1)[1].split("];", 1)[0]
        rows = re.findall(r"\['([^']*)','([^']*)','([^']*)','([^']*)'\]", block)
        fblock = text.split("const femaleGroups=new Set([", 1)[1].split("]);", 1)[0]
        female = set(re.findall(r"'([^']*)'", fblock))
        for group, zh, stage, korean in rows:
            idols.append({
                "pid": f"{slug(group)}-{slug(stage)}", "group": group, "zh": zh, "stage": stage,
                "gender": "female" if group in female else "male",
            })
    else:
        m = re.search(r"var IDOLS = (\[.*?\n\s*\]);", text, re.S)
        if not m:
            raise RuntimeError(
                f"{APP_HTML} 里既没有『const seeds = [』也没有『var IDOLS = [』；"
                "请在 tts/config.json 的 app_html 指向你的 IdolTalk.html 或 app/js/data.js")
        raw = re.sub(r",(\s*\])", r"\1", m.group(1))   # JS 允许尾随逗号，JSON 不允许
        for o in json.loads(raw):
            idols.append({
                "pid": o["id"], "group": o["group"], "zh": o["zh"],
                "stage": o["stage"], "gender": o["gender"],
            })

    if COVERS_META and COVERS_META.exists():
        try:
            keys = set(json.loads(COVERS_META.read_text(encoding="utf-8")).keys())
            diff = keys - {x["pid"] for x in idols}
            if diff:
                print(f"[warn] covers_meta 里有 {len(diff)} 个 id 不在人设表里：{sorted(diff)[:5]}", flush=True)
        except Exception:
            pass
    return idols


def build_queries(zh, stage, n):
    """按序生成搜索词；zh==stage（如 CORTIS 的 James）时去重名字。"""
    name = zh if zh == stage else f"{zh} {stage}"
    out, seen = [], set()
    for suf in QUERY_SUFFIXES:
        q = f"{name} {suf}"
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out[: max(1, n)]


def load_registry():
    if REGISTRY.exists():
        try:
            reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
            if isinstance(reg.get("personas"), dict):
                return reg
        except Exception:
            pass
    return {"version": 1, "engine_default": "gpt_sovits", "personas": {}}


def _read_registry_unlocked():
    """读磁盘上的注册表原始内容（不加锁、不兜底）；损坏或不存在返回 None。"""
    if not REGISTRY.exists():
        return None
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("personas"), dict):
            return data
    except Exception:
        pass
    return None


def _acquire_registry_lock():
    """拿 registry.lock 的跨进程排它锁（msvcrt.locking），把「读-合并-写」窗口串行化。
    拿不到（超过约 10 秒）只警告，不阻塞主流程。返回文件句柄或 None。"""
    if msvcrt is None:
        return None
    f = None
    try:
        REGISTRY_LOCK.parent.mkdir(parents=True, exist_ok=True)
        f = open(REGISTRY_LOCK, "a+b")
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        return f
    except OSError as e:
        print(f"[warn] registry.lock 获取失败（{type(e).__name__}），无锁继续写入", flush=True)
        if f:
            f.close()
        return None


def _release_registry_lock(f):
    if f is None:
        return
    try:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        f.close()


def save_registry(reg, dirty=None):
    """原子写入注册表；dirty 是本次修改过的 persona-id 集合。

    registry.json 另一个长跑写者是 idol-tts 的 voice_batch.py（transcribe/validate 一跑
    几小时），整文件 load-modify-save 会互相静默抹掉对方刚写入的条目，所以：
      1. 写前重读磁盘态，只覆盖 dirty 条目，其余条目（含 voice_batch 刚写的）原样保留；
      2. registry.lock 文件锁（与 voice_batch.py 同一把）串行化「读-合并-写」窗口；
      3. 临时文件 + replace 原子落盘，中途崩不留半截文件。
    """
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    lock_f = _acquire_registry_lock()
    try:
        reg_out = reg
        if dirty:
            disk = _read_registry_unlocked()
            if disk is not None:  # 磁盘读不出来时以内存态兜底，避免误清空其他条目
                for pid in dirty:
                    if pid in reg["personas"]:
                        disk["personas"][pid] = reg["personas"][pid]
                    else:
                        disk["personas"].pop(pid, None)
                disk["version"] = reg.get("version", disk.get("version", 1))
                disk["engine_default"] = reg.get("engine_default", disk.get("engine_default", "gpt_sovits"))
                reg_out = disk
        tmp = REGISTRY.with_name(REGISTRY.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(reg_out, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(REGISTRY)
    finally:
        _release_registry_lock(lock_f)


def load_sources(raw_folder):
    p = raw_folder / "sources.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_sources(raw_folder, sources):
    raw_folder.mkdir(parents=True, exist_ok=True)
    (raw_folder / "sources.json").write_text(
        json.dumps(sources, ensure_ascii=False, indent=1), encoding="utf-8"
    )


async def has_captcha(page):
    return await page.evaluate(
        """() => {
          const t = document.body.innerText || '';
          return t.includes('请完成下列验证') || t.includes('拖动完成上方拼图')
                 || !!document.querySelector('#captcha_container, .captcha_verify_container');
        }"""
    )


async def handle_captcha(page, name, wait_seconds):
    """有验证码就等待人工处理；处理不了也不阻塞——结果可能已经在弹窗后面渲染了。"""
    if not await has_captcha(page):
        return True
    print(
        f"  [!] 出现验证码（{name}）→ 请在浏览器窗口里手动滑一下，"
        f"最多等 {wait_seconds} 秒，之后将直接提取已加载内容…",
        flush=True,
    )
    for _ in range(wait_seconds // 2):
        await page.wait_for_timeout(2000)
        if not await has_captcha(page):
            print("  [ok] 验证完成，继续正常抓取。", flush=True)
            return True
    print("  [warn] 验证码仍在，尝试直接提取已渲染的结果。", flush=True)
    return False


def parse_dur(text):
    """'1:23' → 83 秒；解析不了返回 0。"""
    m = re.match(r"^(\d{1,2}):(\d{2})$", (text or "").strip())
    return int(m.group(1)) * 60 + int(m.group(2)) if m else 0


CAND_JS = """() => {
  const out = [];
  const push = (vid, href, title, dur) => {
    if (vid && !out.some(o => o.vid === vid)) out.push({vid, href, title, dur});
  };
  // 实测首选（2026-10 验证）：搜索卡片容器 id 自带视频 ID —— waterfall_item_<aweme_id>
  document.querySelectorAll("[id^='waterfall_item_']").forEach(card => {
    const m = (card.id || '').match(/waterfall_item_(\\d+)/);
    if (!m) return;
    const text = (card.innerText || '').replace(/\\s+/g, ' ').trim();
    const dm = text.match(/\\b(\\d{1,2}:\\d{2})\\b/);
    push(m[1], 'https://www.douyin.com/video/' + m[1], text.slice(0, 120), dm ? dm[1] : '');
  });
  // 兜底：部分布局仍是 <a href> 链接
  document.querySelectorAll('a[href*="/video/"], a[href*="/note/"]').forEach(a => {
    const m = (a.href || '').match(/\\/(?:video|note)\\/(\\d+)/);
    if (!m) return;
    let title = a.getAttribute('aria-label') || '';
    if (!title) title = (a.textContent || '').replace(/\\s+/g, ' ').trim();
    let dur = '';
    const box = a.closest('li') || a;
    box.querySelectorAll('*').forEach(el => {
      const t = (el.textContent || '').trim();
      if (/^\\d{1,2}:\\d{2}$/.test(t)) dur = t;
    });
    push(m[1], a.href, title, dur);
  });
  return out;
}"""


async def collect_video_candidates(page, min_dur, max_dur, max_steps=SCROLL_STEPS):
    """扫描搜索页的视频/note 链接（滚轮最多 max_steps 次），按标题关键词 + 时长合适度排序。"""
    seen = {}
    for _ in range(max_steps):
        for d in await page.evaluate(CAND_JS):
            vid = d.get("vid") or ""
            if not vid:
                m = re.search(r"/(?:video|note)/(\d+)", d.get("href") or "")
                vid = m.group(1) if m else ""
            if vid and vid not in seen:
                seen[vid] = d
        if len(seen) >= 40:
            break
        await page.mouse.wheel(0, 780)
        await page.wait_for_timeout(int(SCROLL_PAUSE * 1000))
    ranked = []
    for vid, d in seen.items():
        score = 0.0
        title = (d.get("title") or "").lower()
        if any(w in title for w in TITLE_BONUS_WORDS):
            score += 2
        if any(w in title for w in TITLE_BLOCK_WORDS):
            score -= 8   # 梦女二创/混剪/翻唱带 BGM → 克隆发噪，重度降权（用户要求：只要清晰人声）
        dur = parse_dur(d.get("dur"))
        if min_dur <= dur <= max_dur:
            score += 3
        elif dur == 0:
            score += 1  # 没标时长也试试
        ranked.append({"vid": vid, "href": d["href"], "title": d.get("title") or "", "dur": dur, "score": score})
    ranked.sort(key=lambda x: -x["score"])
    return ranked


def rank_media_urls(urls):
    """给嗅探/解析到的播放地址排序：mp4 直链 > CDN 域名 > m3u8 > 其他。"""
    def key(u):
        if ".mp4" in u:
            return 0
        if "douyinvod.com" in u or "volcvideocc" in u or "bytecdn" in u:
            return 1
        if ".m3u8" in u:
            return 2
        return 3
    return sorted(urls, key=key)[0] if urls else ""


async def try_render_data(page):
    """② RENDER_DATA / _ROUTER_DATA 里的 play_addr。"""
    txt = await page.evaluate(
        """() => {
          const el = document.getElementById('RENDER_DATA') || document.getElementById('_ROUTER_DATA');
          return el ? el.textContent : '';
        }"""
    )
    if not txt:
        return ""
    txt = unquote(txt).replace("\\u002F", "/").replace("\\u0026", "&").replace("\\/", "/")
    urls = []
    for m in re.finditer(r"https?://[^\s\"'<>\\]+", txt):
        u = m.group(0)
        if SNIFF_RE.search(u) or u.split("?")[0].endswith((".mp4", ".m3u8")):
            urls.append(u)
    return rank_media_urls(urls)


async def try_download_button(page):
    """③ hover 播放器 → 点下载按钮 → expect_download 落盘。成功返回临时文件路径。"""
    try:
        player = await page.query_selector(".xgplayer")
        if player:
            await player.hover(timeout=5000)
            await page.wait_for_timeout(600)
        btn = await page.query_selector(".xgplayer-download")
        if not btn:
            return ""
        async with page.expect_download(timeout=20000) as dl_info:
            await btn.click()
        dl = await dl_info.value
        p = await dl.path()
        if p and Path(p).stat().st_size > MIN_RAW_BYTES:
            return str(p)
    except Exception:
        pass
    return ""


VIDEO_SRC_JS = ("() => { const v = document.querySelector('video');"
                " return (v && v.src && !v.src.startsWith('blob:')) ? v.src : ''; }")

AUDIO_RE = re.compile(r"mime_type=audio|media-audio|audio_mp4|audio-und", re.I)


def has_audio_track(path: Path) -> bool:
    """用 ffmpeg -i 的 stderr 判断文件里有没有音频轨。"""
    try:
        code, err = run_ffmpeg(["-i", str(path)], timeout=60)
    except Exception:
        return False
    return "Audio:" in (err or "")


async def merge_dash_audio(context, video: Path, audio_url: str) -> bool:
    """DASH 分离流：长视频常被拆成纯视频轨 + 独立音频轨，嗅探到的 mp4 没有声音。
    下载音频轨并与视频合并（-c copy 无损）。"""
    tmp = video.with_name(video.stem + ".audio.mp4")
    merged = video.with_name(video.stem + ".merged.mp4")
    try:
        resp = await context.request.get(
            audio_url, headers={"Referer": "https://www.douyin.com/"}, timeout=120000
        )
        if not resp.ok:
            return False
        tmp.write_bytes(await resp.body())
        code, err = run_ffmpeg(
            ["-y", "-i", str(video), "-i", str(tmp), "-c", "copy",
             "-map", "0:v:0", "-map", "1:a:0", str(merged)],
            timeout=300,
        )
        if code == 0 and merged.exists() and merged.stat().st_size > MIN_RAW_BYTES:
            merged.replace(video)
            return True
        print(f"    [合并音轨] 失败: {(err or '')[-120:]}", flush=True)
        return False
    except Exception as e:
        print(f"    [合并音轨] 异常 {type(e).__name__}: {str(e)[:60]}", flush=True)
        return False
    finally:
        tmp.unlink(missing_ok=True)
        merged.unlink(missing_ok=True)


async def extract_play_url(page, href):
    """详情页取播放地址，策略链按序尝试（成功即止）：
    video.src 直链 → 请求嗅探 → video.src 复查 → RENDER_DATA → 下载按钮；m3u8 由 download_media 交 ffmpeg 拉。
    返回 (url, local_path, audio_url)：url 走网络下载，local_path 是下载按钮已落盘的文件，
    audio_url 是 DASH 分离流的音频轨地址（长视频无音轨时由 merge_dash_audio 合并）。"""
    captured = []

    def on_request(req):
        try:
            u = req.url
        except Exception:
            return
        if SNIFF_RE.search(u) and u not in captured:
            captured.append(u)

    page.on("request", on_request)
    try:
        await page.goto(href, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(int(LOAD_WAIT * 1000))
        # ① video 标签 src —— 实测（2026-10-04）：详情页 video.src 就是 v11-weba.douyinvod.com
        #    直链（和嗅探拿到同一个 URL）；命中后轻点一次播放器，抓 DASH 分离的音频流请求
        src = await page.evaluate(VIDEO_SRC_JS)
        if src:
            try:
                await page.mouse.move(720, 480)
                await page.wait_for_timeout(600)
                await page.mouse.click(720, 480)
                await page.wait_for_timeout(3500)
            except Exception:
                pass
            audio = next((u for u in captured if AUDIO_RE.search(u)), "")
            return src, "", audio
        # ② 请求嗅探；播放器没自动播 → 挪过去点一下再等媒体请求
        if not captured:
            try:
                await page.mouse.move(720, 480)
                await page.wait_for_timeout(800)
                await page.mouse.click(720, 480)
                await page.wait_for_timeout(4000)
            except Exception:
                pass
        audio = next((u for u in captured if AUDIO_RE.search(u)), "")
        if captured:
            best = rank_media_urls(captured)
            if best:
                return best, "", audio
        # ③ 点击/等待之后播放器可能才初始化，再读一次 video.src（实测最可靠的提取方式）
        src = await page.evaluate(VIDEO_SRC_JS)
        if src:
            return src, "", audio
        # ④ RENDER_DATA / _ROUTER_DATA（实测里面没有 play_addr，纯兜底）
        url = await try_render_data(page)
        if url:
            return url, "", audio
        # ⑤ 播放器下载按钮（直接落盘）
        local = await try_download_button(page)
        if local:
            return "", local, ""
        return "", "", ""
    finally:
        try:
            page.remove_listener("request", on_request)
        except Exception:
            pass


async def download_media(context, url, dest: Path):
    """下载到 dest；m3u8 → ④ 交给 ffmpeg 带 Referer 拉。成功返回 True。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if ".m3u8" in url:
        try:
            code, _ = run_ffmpeg(
                ["-y", "-headers", "Referer: https://www.douyin.com/\r\n",
                 "-i", url, "-c", "copy", str(dest)],
                timeout=300,
            )
            return code == 0 and dest.exists() and dest.stat().st_size > MIN_RAW_BYTES
        except Exception as e:
            print(f"    [m3u8 下载失败] {type(e).__name__}: {str(e)[:80]}", flush=True)
            return False
    try:
        resp = await context.request.get(
            url, headers={"Referer": "https://www.douyin.com/"}, timeout=120000
        )
        if not resp.ok:
            return False
        body = await resp.body()
        if len(body) < MIN_RAW_BYTES:
            return False
        dest.write_bytes(body)
        return True
    except Exception as e:
        print(f"    [下载失败] {type(e).__name__}: {str(e)[:80]}", flush=True)
        return False


async def fetch_one(context, page, cand, dest: Path):
    """打开候选详情页取播放地址并下载到 dest。成功返回 True。"""
    try:
        url, local, audio_url = await extract_play_url(page, cand["href"])
    except Exception as e:
        print(f"    [详情页失败] {type(e).__name__}: {str(e)[:60]}", flush=True)
        return False
    ok = False
    if local:
        try:
            shutil.copyfile(local, dest)
            ok = dest.stat().st_size > MIN_RAW_BYTES
        except Exception:
            ok = False
    elif url:
        ok = await download_media(context, url, dest)
        if ok and audio_url and not has_audio_track(dest):
            print("    [合并音轨] 视频无音频，补下 DASH 音频流…", flush=True)
            await merge_dash_audio(context, dest, audio_url)
    if not ok and dest.exists():
        try:
            dest.unlink()
        except Exception:
            pass
    return ok


# ---------------- 音频处理（全部走 find_ffmpeg() + numpy/scipy） ----------------

def extract_wav(mp4: Path, wav: Path):
    """ffmpeg 抽 16k 单声道 PCM。"""
    try:
        code, _ = run_ffmpeg(
            ["-y", "-i", str(mp4), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)]
        )
    except Exception:
        return False
    return code == 0 and wav.exists()


def detect_silences(wav_path: Path):
    """silencedetect（noise=-35dB:d=0.6）找静音间隔，解析 stderr。返回 [(start, end|None)]。"""
    try:
        _, err = run_ffmpeg(["-i", str(wav_path), "-af", "silencedetect=noise=-35dB:d=0.6", "-f", "null", "-"])
    except Exception:
        return []
    starts = [float(m) for m in re.findall(r"silence_start:\s*([0-9.]+)", err)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([0-9.]+)", err)]
    return [(st, ends[i] if i < len(ends) else None) for i, st in enumerate(starts)]


def build_segments(total, silences, min_s=MIN_SEG, max_s=MAX_SEG, pad=SEG_PAD):
    """静音间隔 → 合并成 [min_s, max_s] 秒的片段（前后 pad 秒留白），返回 [(start, end)]。"""
    nonsil, cur = [], 0.0
    for st, en in silences:
        if st > cur:
            nonsil.append((cur, min(st, total)))
        cur = en if en is not None else total
        if cur >= total:
            break
    if cur < total:
        nonsil.append((cur, total))
    # 相邻非静音段合并（总长不超过 max_s）
    merged = []
    for a, b in nonsil:
        if merged and b - merged[-1][0] <= max_s:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    # 扩留白 → 超长再切 → 丢掉太短的
    out = []
    for a, b in merged:
        a2, b2 = max(0.0, a - pad), min(total, b + pad)
        while b2 - a2 >= min_s:
            cut = min(b2, a2 + max_s)
            if cut - a2 >= min_s:
                out.append((a2, cut))
            a2 = cut
    return out


def read_wav_mono(path: Path):
    with wave.open(str(path), "rb") as w:
        sr, raw = w.getframerate(), w.readframes(w.getnframes())
    y = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return y, sr


def slice_segment(wav_in: Path, start, dur, out: Path):
    try:
        code, _ = run_ffmpeg(
            ["-y", "-ss", f"{start:.3f}", "-i", str(wav_in), "-t", f"{dur:.3f}",
             "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)]
        )
    except Exception:
        return False
    return code == 0 and out.exists()


def est_f0_qc(y, sr, fmin=70, fmax=400):
    """自相关基频估计（与 audit_gender.est_f0_median 同算法）。

    返回 (中位 F0, 四分位距 IQR, 浊音帧占比)；判不出返回 (None, None, 0)。
    IQR 大 = 基频忽高忽低 = 混了别的说话人（旁白/多人）；F0 越界 = 性别不符。
    注意：不要加"倍频纠正"——实测它会把真实女声(250Hz)折半成男声(125Hz)，
    导致男爱豆素材里的女声旁白被误判为合格而放行。
    """
    fl = int(sr * 0.04)
    hop = int(sr * 0.02)
    n = max(1, (len(y) - fl) // hop)
    lag_min, lag_max = int(sr / fmax), int(sr / fmin)
    f0s = []
    for i in range(n):
        fr = y[i * hop: i * hop + fl]
        if float(np.sqrt((fr ** 2).mean())) < 0.02:
            continue
        fr = fr - fr.mean()
        ac = np.correlate(fr, fr, mode="full")[fl - 1:]
        if ac[0] <= 0:
            continue
        seg = ac[lag_min:lag_max]
        if not len(seg):
            continue
        lag = int(np.argmax(seg)) + lag_min
        if ac[lag] / ac[0] < 0.35:
            continue
        f0s.append(sr / lag)
    if len(f0s) < 8:
        return None, None, len(f0s) / max(1, n)
    arr = np.array(f0s)
    return (float(np.median(arr)),
            float(np.percentile(arr, 75) - np.percentile(arr, 25)),
            len(f0s) / max(1, n))


def bgm_score(y, sr):
    """BGM/音乐检测（2026-10-07，用户核心要求"视频里不能有其他 BGM"）。
    原理：① 语音有 2~8Hz 音节调制（音乐没有）② BGM 低频(30~140Hz)能量多 ③ 词间静音被 BGM 抬升。
    返回 0~1，越高越可能有背景音乐。"""
    hop = int(sr * 0.02)
    n = max(1, len(y) // hop)
    env = np.sqrt((y[: n * hop].reshape(n, hop) ** 2).mean(1)) if n > 4 else None
    mod = 0.0
    quiet = 0.0
    if env is not None and len(env) > 16:
        e = env - env.mean()
        E = np.abs(np.fft.rfft(e * np.hanning(len(e))))
        fr = np.fft.rfftfreq(len(e), 0.02)
        tot = E[(fr >= 0.3) & (fr <= 30)].sum() + 1e-9
        mod = float(E[(fr >= 2) & (fr <= 8)].sum() / tot)
        quiet = float(np.percentile(env, 10)) / (float(np.percentile(env, 90)) + 1e-9)
    Y = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    frq = np.fft.rfftfreq(len(y), 1 / sr)
    tot_e = Y[(frq >= 30) & (frq <= 8000)].sum() + 1e-9
    low_r = float(Y[(frq >= 30) & (frq <= 140)].sum() / tot_e)
    s = 0.0
    s += max(0.0, (0.40 - mod) / 0.40) * 0.45
    s += min(1.0, low_r / 0.25) * 0.30
    s += min(1.0, quiet / 0.15) * 0.25
    return min(1.0, s)


def score_segment(y, sr, gender=None):
    """0-1 打分：时长在 [5,20]s、RMS 在 [-24,-8] dBFS 加分；削波/静音占比/谱平坦度（像噪声或音乐）惩罚；
    300-3400Hz 人声频段能量占比加分。只用 numpy/scipy。"""
    dur = len(y) / sr
    s = 0.0
    if MIN_SEG <= dur <= MAX_SEG:
        s += 0.20
    elif 3.5 <= dur < MIN_SEG or MAX_SEG < dur <= 25.0:
        s += 0.08
    rms = float(np.sqrt(np.mean(np.square(y)))) if len(y) else 0.0
    rms_db = 20 * math.log10(rms + 1e-12)
    if -24 <= rms_db <= -8:
        s += 0.25
    elif -30 <= rms_db < -24:
        s += 0.12
    if len(y):
        s -= 0.6 * float(np.mean(np.abs(y) > 0.985))  # 削波比例
    frame = max(1, int(0.02 * sr))
    if len(y) >= frame * 2:  # 20ms 帧静音占比
        n = len(y) // frame
        fr = np.sqrt(np.mean(np.square(y[: n * frame].reshape(n, frame)), axis=1))
        s -= 0.4 * float(np.mean(fr < 0.004))
    if scipy_signal is not None and len(y) >= 4096:
        try:
            freqs, psd = scipy_signal.welch(y, fs=sr, nperseg=2048)
            pos = psd[psd > 0]
            if len(pos):
                flat = float(np.exp(np.mean(np.log(pos))) / np.mean(pos))  # 谱平坦度，越接近 1 越像噪声
                s -= 0.2 * flat
                s += 0.2 * (1.0 - flat)
            band = (freqs >= 300) & (freqs <= 3400)
            tot = float(psd.sum())
            if tot > 0:
                s += 0.35 * float(psd[band].sum() / tot)  # 人声频段能量占比
        except Exception:
            pass
    # —— 声音质检（2026-10-06）：性别匹配 + 单人声判定 ——
    f0, iqr, voiced = est_f0_qc(y, sr)
    if f0 is None:
        s -= 0.30  # 判不出基频：噪声/音乐为主
    else:
        if gender == "female":
            if f0 < 140:
                s -= 0.55  # 女爱豆素材混了男声（阈值与 audit_gender.verdict 对齐）
            elif f0 < 160:
                s -= 0.15
        elif gender == "male":
            if f0 > 185:
                s -= 0.55  # 男爱豆素材混了女声（阈值与 audit_gender.verdict 对齐）
            elif f0 > 165:
                s -= 0.15
        if iqr is not None:
            if iqr > 65:
                s -= 0.30  # F0 剧烈波动 → 多说话人（旁白混入）
            elif iqr > 50:
                s -= 0.12
        if voiced < 0.25:
            s -= 0.25  # 浊音帧太少：不是连续人声
    # —— BGM/音乐检测：带背景音乐的重罚（用户核心要求：视频里不能有其他 BGM）——
    bgm = bgm_score(y, sr)
    if bgm > 0.35:
        s -= 0.85          # 明显有 BGM → 基本淘汰
    elif bgm > 0.20:
        s -= 0.35          # 疑似有 BGM
    return max(0.0, min(1.0, s))


def harvest_from(mp4: Path, raw_folder: Path, sources, gender=None):
    """单个 raw 视频 → wav → 静音切分 → 逐段打分。返回切片列表（wav 是 raw 目录里的临时文件）。"""
    for stale in raw_folder.glob(mp4.stem + ".audio_seg*.wav"):  # 清掉上次中断的残留
        try:
            stale.unlink()
        except Exception:
            pass
    tmp_wav = raw_folder / (mp4.stem + ".audio.wav")
    if not extract_wav(mp4, tmp_wav):
        print(f"    [抽音频失败] {mp4.name}", flush=True)
        return []
    try:
        y, sr = read_wav_mono(tmp_wav)
    except Exception as e:
        print(f"    [读 wav 失败] {mp4.name}: {e}", flush=True)
        return []
    total = len(y) / sr
    if total < 3.0:
        return []
    segs = []
    for k, (a, b) in enumerate(build_segments(total, detect_silences(tmp_wav))):
        seg_wav = raw_folder / f"{mp4.stem}.audio_seg{k:02d}.wav"
        if not slice_segment(tmp_wav, a, b - a, seg_wav):
            continue
        try:
            sy, ssr = read_wav_mono(seg_wav)
        except Exception:
            continue
        rms = float(np.sqrt(np.mean(np.square(sy))))
        segs.append({
            "wav": seg_wav, "dur": len(sy) / ssr,
            "score": score_segment(sy, ssr, gender),
            "rms_db": 20 * math.log10(rms + 1e-12),
            "src": sources.get(mp4.name, f"raw/{mp4.parent.name}/{mp4.name}"),
        })
    print(f"    [切片] {mp4.name}（{total:.0f}s）→ {len(segs)} 段", flush=True)
    return segs


def kept_count(segs):
    return len([s for s in segs if s["score"] >= MIN_KEEP_SCORE])


def cleanup_tmp_wavs(raw_folder: Path):
    """删掉 raw 目录里的中间 wav（完整音频 + 切片临时件，ref 已拷到 <pid>/ 下）。"""
    if not raw_folder.exists():
        return
    for f in list(raw_folder.glob("*.wav")):
        try:
            f.unlink()
        except Exception:
            pass


def finalize_idol(pid, gender, queries, raw_folder: Path, segs, registry, existing, add):
    """挑 top 切片存 <pid>/refNN.wav，写 meta.json，同步 registry，清中间文件。返回保留段数（0=missing）。"""
    folder = OUT_DIR / pid
    segs = sorted(segs, key=lambda x: -x["score"])
    keep = [s for s in segs if s["score"] >= MIN_KEEP_SCORE][:MAX_REFS]
    if not keep and segs:
        keep = segs[:1]  # 一段合格的都没有时兜底留最好的一段，交给人工 QC 判断
    prev = registry["personas"].get(pid, {})
    n_raw = len(list(raw_folder.glob("*.mp4"))) if raw_folder.exists() else 0
    meta_clips = []  # meta.json 里的旧切片：追加续编号、失败保底判断都用它
    if (folder / "meta.json").exists():
        try:
            meta_clips = json.loads((folder / "meta.json").read_text(encoding="utf-8")).get("clips", [])
        except Exception:
            meta_clips = []
    if not keep:
        note = f"{len(queries)} 组搜索词 / {n_raw} 个视频里没有合格切片（候选 {len(segs)} 段）"
        prev_clips = prev.get("clips") or meta_clips
        if existing and prev_clips:
            # 磁盘仍有 ref*.wav：本次一无所获也不能把已有素材抹成 missing/clips:[]——
            # tts_server 以 registry 为准，错写会让有素材的爱豆直接 404 且不会自愈
            if prev.get("status") in ("sourced", "transcribed", "validated", "ready"):
                entry = dict(prev)  # 原样保留（含 voice_batch 补的 prompt_text / 状态推进）
                where = f"维持 {prev['status']}"
            else:
                entry = {"status": "sourced", "gender": gender, "engine": "gpt_sovits",
                         "created_at": prev.get("created_at", now()), "last_qc": prev.get("last_qc", "")}
                where = "重建为 sourced"
            entry["clips"] = prev_clips
            entry["note"] = (prev.get("note") + "；" if prev.get("note") else "") + f"本次抓取未获得新切片（{note}）"
            registry["personas"][pid] = entry
            save_registry(registry, dirty={pid})
            cleanup_tmp_wavs(raw_folder)
            print(f"  [保留] 本次无合格切片，但磁盘仍有 {len(existing)} 段 ref → {where}，原因记入 note", flush=True)
            return len(prev_clips)
        registry["personas"][pid] = {
            "status": "missing", "gender": gender, "clips": [], "engine": "gpt_sovits",
            "created_at": prev.get("created_at", now()), "last_qc": "",
            "note": note,
        }
        save_registry(registry, dirty={pid})
        cleanup_tmp_wavs(raw_folder)
        print(f"  [missing] 无合格切片（候选 {len(segs)} 段）→ registry 已记录", flush=True)
        return 0
    folder.mkdir(parents=True, exist_ok=True)
    old_clips = meta_clips if add else []
    start = len(existing) if add else 0
    clips = []
    for i, s in enumerate(keep):
        name = f"ref{start + i + 1:02d}.wav"
        shutil.copyfile(s["wav"], folder / name)
        clips.append({
            "file": name, "dur": round(s["dur"], 1), "score": round(s["score"], 3),
            "rms_db": round(s["rms_db"], 1), "src": s["src"],
            "prompt_text": "", "prompt_lang": "zh",  # 转写由 voice_batch.py 补
        })
    (folder / "meta.json").write_text(
        json.dumps({"queries": queries, "clips": old_clips + clips}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    registry["personas"][pid] = {
        "status": "sourced", "gender": gender, "clips": old_clips + clips, "engine": "gpt_sovits",
        "created_at": prev.get("created_at", now()), "last_qc": "",
    }
    save_registry(registry, dirty={pid})
    cleanup_tmp_wavs(raw_folder)
    print(f"  [完成] 保留 {len(clips)} 段切片 → {folder}", flush=True)
    return len(clips)


async def process_idol(context, page, idol, args, registry, idx, total):
    pid, zh, stage = idol["pid"], idol["zh"], idol["stage"]
    folder, raw_folder = OUT_DIR / pid, RAW_DIR / pid
    existing = sorted(folder.glob("ref*.wav")) if folder.exists() else []
    if existing and not args.add:  # 断点续传
        print(f"[{idx}/{total}] {zh} · {stage} —— 已有 {len(existing)} 段切片，跳过", flush=True)
        return

    queries = ([q.strip() for q in args.query.split(",") if q.strip()]
               if getattr(args, "query", "") else build_queries(zh, stage, args.queries_per_idol))
    print(f"\n[{idx}/{total}] {zh} · {stage}（{idol['group']}）搜索：{' / '.join(queries)}", flush=True)

    segs = []
    raws = sorted(raw_folder.glob("*.mp4")) if raw_folder.exists() else []
    sources = load_sources(raw_folder)
    # 下载预算只计真正收割进来的 raw：已有切片（--add 重跑）时旧 mp4 不收割，就不许占预算——
    # 否则搜索循环第一轮就 break，segs 恒为空，finalize 会把已 sourced 的爱豆抹成 missing
    reused = raws[: args.max_videos] if (raws and not existing) else []
    if reused:
        print(f"  [续跑] 复用已有 {len(reused)} 个原始视频", flush=True)
        for mp4 in reused:
            segs += harvest_from(mp4, raw_folder, sources, idol["gender"])
    nums = [int(p.stem) for p in raws if p.stem.isdigit()]
    next_n = (max(nums) + 1) if nums else 1
    downloads = len(reused)

    for q in queries:
        if downloads >= args.max_videos or kept_count(segs) >= MAX_REFS:
            break
        url = "https://www.douyin.com/search/" + quote(q)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"  [打开失败] {type(e).__name__}", flush=True)
            continue
        await page.wait_for_timeout(int(LOAD_WAIT * 1000))
        solved = await handle_captcha(page, f"{zh} {stage}", 300 if idx == 1 else 45)
        cands = await collect_video_candidates(
            page, args.min_dur, args.max_dur, max_steps=SCROLL_STEPS if solved else 4
        )
        print(f"  [搜索] {q} → 候选 {len(cands)} 个", flush=True)
        for cand in cands:
            if downloads >= args.max_videos or kept_count(segs) >= MAX_REFS:
                break
            dest = raw_folder / f"{next_n:02d}.mp4"
            if await fetch_one(context, page, cand, dest):
                next_n += 1
                downloads += 1
                sources[dest.name] = cand["href"]
                save_sources(raw_folder, sources)
                print(f"    [下载 {downloads}/{args.max_videos}] {dest.name} ← {cand['title'][:36]}", flush=True)
                segs += harvest_from(dest, raw_folder, sources, idol["gender"])

    finalize_idol(pid, idol["gender"], queries, raw_folder, segs, registry, existing, args.add)
    await page.wait_for_timeout(int(IDOL_PAUSE * 1000))


async def process_link(context, page, pid, url, idol, registry, idx, total):
    """--link 模式：直接处理 v.douyin.com 短链（跳转后从 page.url 提取视频 id），总是追加。"""
    gender = idol["gender"] if idol else "female"
    zh = idol["zh"] if idol else pid
    print(f"\n[link {idx}/{total}] {zh} ← {url}", flush=True)
    raw_folder = RAW_DIR / pid
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"  [打开失败] {type(e).__name__}", flush=True)
        return
    await page.wait_for_timeout(int(LOAD_WAIT * 1000))
    await handle_captcha(page, pid, 300 if idx == 1 else 45)
    m = re.search(r"/(?:video|note)/(\d+)", page.url)
    if not m:
        print("  [失败] 短链没有跳到 /video/ 或 /note/ 详情页", flush=True)
        return
    kind = "note" if "/note/" in page.url else "video"
    href = f"https://www.douyin.com/{kind}/{m.group(1)}"
    sources = load_sources(raw_folder)
    nums = [int(p.stem) for p in raw_folder.glob("*.mp4") if p.stem.isdigit()] if raw_folder.exists() else []
    dest = raw_folder / f"{(max(nums) + 1) if nums else 1:02d}.mp4"
    if not await fetch_one(context, page, {"href": href, "title": url}, dest):
        print("  [失败] 没取到播放地址（策略链全部失败）", flush=True)
        return
    sources[dest.name] = url
    save_sources(raw_folder, sources)
    print(f"    [下载] {dest.name}", flush=True)
    segs = harvest_from(dest, raw_folder, sources, gender)
    folder = OUT_DIR / pid
    existing = sorted(folder.glob("ref*.wav")) if folder.exists() else []
    finalize_idol(pid, gender, [url], raw_folder, segs, registry, existing, add=True)
    await page.wait_for_timeout(int(IDOL_PAUSE * 1000))


async def main():
    ap = argparse.ArgumentParser(description="抖音爱豆说话素材抓取（供声音克隆）")
    ap.add_argument("--only", default="", help="只处理指定 persona id（逗号分隔，如 aespa-giselle,cortis-juhoon）")
    ap.add_argument("--query", default="", help="自定义搜索词（覆盖默认模板，如「吉赛尔 英语」；逗号分隔多组）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个（试跑用）")
    ap.add_argument("--link", default="", help="手动指定素材链接：pid=url[,pid=url]（v.douyin.com 短链）")
    ap.add_argument("--queries-per-idol", type=int, default=3, help="每位爱豆最多尝试几组搜索词（默认 3，全部 5 组）")
    ap.add_argument("--max-videos", type=int, default=4, help="每位爱豆最多下载几个视频（默认 4）")
    ap.add_argument("--min-dur", type=int, default=8, help="候选视频最短秒数（默认 8）")
    ap.add_argument("--max-dur", type=int, default=90, help="候选视频最长秒数（默认 90）")
    ap.add_argument("--add", action="store_true", help="追加模式：已有切片也重抓，新切片续编号（ref06…）")
    ap.add_argument("--purge-raw", action="store_true", help="清空 raw 原始视频目录后退出")
    ap.add_argument("--ffmpeg", default="", help="手动指定 ffmpeg 完整路径（优先级最高）")
    args = ap.parse_args()

    if args.ffmpeg:
        os.environ["FFMPEG_PATH"] = args.ffmpeg  # find_ffmpeg 的最优先候选

    if args.purge_raw:
        n = 0
        if RAW_DIR.exists():
            for f in RAW_DIR.rglob("*"):
                if f.is_file():
                    try:
                        f.unlink()
                        n += 1
                    except Exception:
                        pass
            for d in sorted((p for p in RAW_DIR.rglob("*") if p.is_dir()), reverse=True):
                try:
                    d.rmdir()
                except Exception:
                    pass
            try:
                RAW_DIR.rmdir()
            except Exception:
                pass
        print(f"[purge-raw] 已清理 {n} 个文件 → {RAW_DIR}", flush=True)
        return

    links = []
    if args.link:
        for part in args.link.split(","):
            part = part.strip()
            if not part:
                continue
            if "=" not in part:
                print(f"[warn] --link 格式应为 pid=url，忽略：{part}", flush=True)
                continue
            pid, url = part.split("=", 1)
            links.append((pid.strip(), url.strip()))
        if not links:
            print("[warn] --link 没有可用条目，退出。", flush=True)
            return

    idols = load_idols()
    idol_by_pid = {x["pid"]: x for x in idols}
    print(f"从 IdolTalk.html 读到 {len(idols)} 位成员", flush=True)

    if not links:
        if args.only:
            wanted = {s.strip() for s in args.only.split(",")}
            unknown = wanted - set(idol_by_pid)
            if unknown:
                print(f"[warn] 未知 id（忽略）：{', '.join(sorted(unknown))}", flush=True)
            idols = [x for x in idols if x["pid"] in wanted]
        if args.limit:
            idols = idols[: args.limit]
        if not idols:
            print("没有要处理的爱豆，退出。", flush=True)
            return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    registry = load_registry()

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            viewport={"width": 1440, "height": 960},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        t0 = time.time()
        if links:
            for i, (pid, url) in enumerate(links, 1):
                await process_link(context, page, pid, url, idol_by_pid.get(pid), registry, i, len(links))
        else:
            for i, idol in enumerate(idols, 1):
                await process_idol(context, page, idol, args, registry, i, len(idols))

        await context.close()
        done = sum(1 for v in registry["personas"].values() if v.get("status") == "sourced")
        miss = sum(1 for v in registry["personas"].values() if v.get("status") == "missing")
        print(
            f"\n全部完成，用时 {int(time.time() - t0)} 秒。registry：sourced {done} / missing {miss} → {REGISTRY}",
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
