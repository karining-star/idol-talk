# -*- coding: utf-8 -*-
"""
IdolTalk 音色库批量管理：ingest 入库 / transcribe 转写 / validate 校验 / report 报告。

用法（在 tts/ 目录下执行）：
  python voice_batch.py ingest                              # 扫描素材目录入库
  python voice_batch.py ingest --only aespa-giselle        # 只处理指定 id（逗号分隔）
  python voice_batch.py transcribe                         # faster-whisper 补 prompt_text
  python voice_batch.py transcribe --force                 # 忽略状态过滤强制重跑
  python voice_batch.py validate                           # 格式检查 + 本地 TTS 试合成
  python voice_batch.py validate --tts-url http://127.0.0.1:8780
  python voice_batch.py report                             # 生成 qc/qc_report.html

数据布局（目录由 tts/config.json 的 voices_dir 决定，默认仓库下 voices/）：
  <voices_dir>/registry.json     # 音色注册表（本脚本维护）
  <voices_dir>/<pid>/ref*.wav    # 16k 单声道参考音频（fetch_idol_voices.py 抓的）
  <voices_dir>/<pid>/meta.json   # queries + clips（含 prompt_text）
  <voices_dir>/qc/               # 质检报告 + 试合成 mp3
状态机：sourced（素材入库）→ transcribed（有转写文本）→ validated（试合成通过）→ ready；missing/failed 特殊态。
"""
import argparse
import glob
import html
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
from datetime import datetime
from pathlib import Path

import numpy as np
import requests

import paths  # 本机路径解析：tts/config.json → 环境变量 → 仓库内相对默认值

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

APP_HTML = paths.path("app_html")
VOICES_DIR = paths.path("voices_dir")
REGISTRY = VOICES_DIR / "registry.json"
REGISTRY_LOCK = VOICES_DIR / "registry.lock"   # 跨进程写锁，与 fetch_idol_voices.py 共用同一把
QC_DIR = VOICES_DIR / "qc"
DEFAULT_TTS = "http://127.0.0.1:8780"
TEST_SENTENCE = "Hello, I am so glad you are here tonight."

STATUS_ORDER = {"missing": -1, "failed": -1, "sourced": 1, "transcribed": 2, "validated": 3, "ready": 4}
STATUS_LABEL = {"missing": "未抓取", "failed": "失败", "sourced": "已入库", "transcribed": "已转写",
                "validated": "已验证", "ready": "就绪"}
SKIP_DIRS = {"raw", "qc"}  # voices 目录下非 persona 的文件夹


# ---------------------------------------------------------------- 通用工具

def find_ffmpeg():
    """统一 ffmpeg 解析（各脚本保持一致）：FFMPEG_PATH 环境变量 → PATH → imageio-ffmpeg → 常见路径。"""
    env = os.environ.get("FFMPEG_PATH")
    if env and Path(env).is_file():
        return str(Path(env))
    hit = shutil.which("ffmpeg")
    if hit:
        return hit
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    local = os.environ.get("LOCALAPPDATA") or ""
    patterns = [
        str(Path(local) / "Microsoft/WinGet/Links/ffmpeg.exe"),
        str(Path(local) / "Microsoft/WinGet/Packages/Gyan.FFmpeg*/ffmpeg*/bin/ffmpeg.exe"),
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"D:\ffmpeg\bin\ffmpeg.exe",
    ]
    for pat in patterns:
        for hit in glob.glob(pat):
            if Path(hit).is_file():
                return hit
    raise RuntimeError(
        "找不到 ffmpeg。解决办法：`winget install Gyan.FFmpeg` 后重开终端，"
        "或设环境变量 FFMPEG_PATH 指向 ffmpeg.exe 完整路径。"
    )


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower())


def load_personas():
    """读全部爱豆（pid + 性别），支持两种来源，与 crawler/fetch_idol_voices.py 保持一致：

      · 桌面单文件版 IdolTalk.html —— 解析 const seeds = [...] + femaleGroups
      · 仓库版 app/js/data.js      —— 解析 var IDOLS = [{...}]（字段已齐全）
    """
    text = APP_HTML.read_text(encoding="utf-8")
    out = []

    if "const seeds = [" in text:
        block = text.split("const seeds = [", 1)[1].split("];", 1)[0]
        seeds = re.findall(r"\['([^']*)','([^']*)','([^']*)','([^']*)'\]", block)
        female_block = text.split("const femaleGroups=new Set([", 1)[1].split("]);", 1)[0]
        female_groups = set(re.findall(r"'([^']*)'", female_block))
        for group, zh, stage, korean in seeds:
            out.append({"pid": f"{slug(group)}-{slug(stage)}", "group": group, "zh": zh,
                        "stage": stage, "korean": korean,
                        "gender": "female" if group in female_groups else "male"})
    else:
        m = re.search(r"var IDOLS = (\[.*?\n\s*\]);", text, re.S)
        if not m:
            raise RuntimeError(
                f"{APP_HTML} 里既没有『const seeds = [』也没有『var IDOLS = [』；"
                "请在 tts/config.json 的 app_html 指向你的 IdolTalk.html 或 app/js/data.js")
        raw = re.sub(r",(\s*\])", r"\1", m.group(1))   # JS 允许尾随逗号，JSON 不允许
        for o in json.loads(raw):
            out.append({"pid": o["id"], "group": o["group"], "zh": o["zh"],
                        "stage": o["stage"], "korean": o.get("korean", ""),
                        "gender": o["gender"]})

    if len(out) != 132:
        print(f"[warn] {APP_HTML.name} 解析出 {len(out)} 位（预期 132），应用可能改过", flush=True)
    return out


def load_registry():
    reg = {"version": 1, "engine_default": "gpt_sovits", "personas": {}}
    if REGISTRY.exists():
        try:
            data = json.loads(REGISTRY.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("personas"), dict):
                reg.update(data)
        except Exception as e:
            print(f"[warn] registry.json 损坏（{type(e).__name__}），从头开始", flush=True)
    reg.setdefault("version", 1)
    reg.setdefault("engine_default", "gpt_sovits")
    reg.setdefault("personas", {})
    return reg


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

    registry.json 有两个长跑写者（本脚本 transcribe/validate 一跑几小时，idol-crawler 的
    fetch_idol_voices.py 也在写），整文件 load-modify-save 会互相静默抹掉对方刚写入的条目，
    所以这里做两层防护：
      1. 写前重读磁盘态，只把 dirty 条目覆盖进去，其余条目（含并行进程新写的）原样保留；
      2. registry.lock 文件锁（与 fetch_idol_voices.py 同一把）串行化「读-合并-写」窗口。
    dirty=None 退化为整文件写（仅在确定没有并行写者时使用）。
    """
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
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


def parse_only(text):
    return {s.strip() for s in text.split(",") if s.strip()}


def warn_unknown_ids(only, known):
    unknown = only - set(known)
    if unknown:
        print(f"[!] 未知 persona-id：{', '.join(sorted(unknown))}", flush=True)


def probe_wav_dur(path):
    """用标准库 wave 量时长（秒），读不出返回 None。"""
    try:
        with wave.open(str(path), "rb") as w:
            sr = w.getframerate() or 16000
            return w.getnframes() / float(sr)
    except Exception:
        return None


def check_ref_wav(path):
    """参考音频体检：可读、16k、单声道、5-20s、RMS 合理。返回 (ok, 消息, 时长, rms_db)。"""
    dur = None
    try:
        with wave.open(str(path), "rb") as w:
            sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
            if sr != 16000:
                return False, f"采样率 {sr}≠16000", None, None
            if ch != 1:
                return False, f"声道数 {ch}≠1", None, None
            if sw != 2:
                return False, f"位深 {sw * 8}bit≠16bit", None, None
            dur = n / float(sr)
            if not 5 <= dur <= 20:
                return False, f"时长 {dur:.1f}s 不在 5-20s", dur, None
            raw = w.readframes(n)
        if len(raw) % 2:  # 数据块被截断（奇数字节）时截齐，避免 frombuffer 抛未捕获 ValueError
            raw = raw[: len(raw) // 2 * 2]
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        if not x.size:
            return False, "空音频", dur, None
        rms = float(np.sqrt(np.mean(x * x)) / 32768.0)
        db = 20 * math.log10(rms) if rms > 0 else -120.0
        if db < -45:
            return False, f"RMS {db:.1f}dB 过轻", dur, db
        if db > -1:
            return False, f"RMS {db:.1f}dB 疑似削波", dur, db
        return True, f"{dur:.1f}s/{db:.1f}dB", dur, db
    except Exception as e:
        return False, f"无法读取（{type(e).__name__}）", dur, None


def deep_check_mp3(path, ffmpeg):
    """用 ffmpeg 解码试合成结果，验证时长与非静音。返回 (ok, 消息)。"""
    try:
        r = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
            capture_output=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if r.returncode != 0 or not r.stdout:
            return False, "ffmpeg 解码失败"
        dur = len(r.stdout) / 32000.0
        if dur < 1.0:
            return False, f"时长仅 {dur:.1f}s"
        x = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32)
        rms = float(np.sqrt(np.mean(x * x)) / 32768.0)
        db = 20 * math.log10(rms) if rms > 0 else -120.0
        if db < -50:
            return False, f"疑似静音（RMS {db:.1f}dB）"
        return True, f"{dur:.1f}s"
    except Exception as e:
        return True, f"深度检查跳过（{type(e).__name__}）"  # 检查本身出错不否定合成结果


def write_meta(pid, entry):
    """把 registry 里的 clips 同步回 <pid>\\meta.json（保留 queries 等其他字段）。"""
    folder = VOICES_DIR / pid
    mpath = folder / "meta.json"
    meta = {}
    if mpath.exists():
        try:
            meta = json.loads(mpath.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    meta["clips"] = entry.get("clips", [])
    meta.setdefault("queries", [])
    folder.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 子命令：ingest

def cmd_ingest(args):
    personas = load_personas()
    by_pid = {p["pid"]: p for p in personas}
    if not VOICES_DIR.exists():
        print(f"[!] 素材目录不存在：{VOICES_DIR}\n    请先运行 idol-crawler 的 fetch_idol_voices.py 抓参考音频。", flush=True)
        return 1
    only = parse_only(args.only)
    warn_unknown_ids(only, by_pid)

    reg = load_registry()
    all_dirs = [d.name for d in VOICES_DIR.iterdir() if d.is_dir()]
    unknown_dirs = sorted(d for d in all_dirs if d not in by_pid and d not in SKIP_DIRS)
    if unknown_dirs:
        print(f"[warn] 忽略无法识别的文件夹：{', '.join(unknown_dirs)}", flush=True)
    targets = sorted(d for d in all_dirs if d in by_pid and (not only or d in only))
    print(f"素材目录共 {len([d for d in all_dirs if d in by_pid])} 位有文件夹，本次入库 {len(targets)} 位", flush=True)

    for i, pid in enumerate(targets, 1):
        p = by_pid[pid]
        folder = VOICES_DIR / pid
        wavs = sorted(folder.glob("ref*.wav"))
        meta = {}
        mpath = folder / "meta.json"
        if mpath.exists():
            try:
                meta = json.loads(mpath.read_text(encoding="utf-8"))
            except Exception:
                print(f"  [warn] {pid} 的 meta.json 损坏，按空处理", flush=True)
                meta = {}
        old = reg["personas"].get(pid, {})

        if not wavs:
            if old:
                n_dangling = len(old.get("clips") or [])
                old["status"] = "missing"   # 素材没了就是 missing，不管原来是 sourced/validated/ready
                old["clips"] = []           # 文件已不存在，顺带清掉悬空的 clips 记录
                save_registry(reg, dirty={pid})
                extra = f"，清 {n_dangling} 段悬空 clips" if n_dangling else ""
                print(f"[{i}/{len(targets)}] {pid} —— 文件夹里没有 ref*.wav，标 missing{extra}", flush=True)
            else:
                print(f"[{i}/{len(targets)}] {pid} —— 文件夹里没有 ref*.wav，跳过", flush=True)
            continue

        # 合并 clips：meta.json 为准，注册表里已有的 prompt_text 别丢，wav 以磁盘为准。
        # ref*.wav 被重新抓取覆盖（mtime/size 与注册表记录不符）时，旧 prompt_text 和
        # validated/ready 状态不能搬到新音频上：丢旧文本、状态降回 sourced 重走 transcribe/validate。
        file_sig = {}
        for w in wavs:
            try:
                st = w.stat()
                file_sig[w.name] = (st.st_mtime_ns, st.st_size)
            except OSError:
                file_sig[w.name] = None

        def stale(c, fname):
            """注册表里的 clip 记录与磁盘同名文件是否对不上（旧记录没存 mtime 的视为兼容迁移，不算变化）。"""
            sig = file_sig.get(fname)
            old_sig = (c.get("mtime"), c.get("size")) if c.get("mtime") is not None else None
            return sig is not None and old_sig is not None and sig != old_sig

        merged = {}
        for c in meta.get("clips") or []:
            if c.get("file"):
                merged[c["file"]] = dict(c)
        files_changed = False
        for c in old.get("clips") or []:
            f = c.get("file")
            if not f:
                continue
            if f in merged:
                if stale(c, f):
                    files_changed = True   # 同名文件已换新，旧转写文本作废
                    continue
                if not merged[f].get("prompt_text") and c.get("prompt_text"):
                    merged[f]["prompt_text"] = c["prompt_text"]
                    merged[f]["prompt_lang"] = c.get("prompt_lang", "")
            elif (folder / f).exists():
                if stale(c, f):
                    files_changed = True
                    continue
                merged[f] = dict(c)
        clips = []
        for w in wavs:
            m = merged.get(w.name, {})
            dur = m.get("dur") or probe_wav_dur(w)
            sig = file_sig.get(w.name)
            clips.append({"file": w.name,
                          "dur": round(dur, 2) if dur else None,
                          "score": m.get("score"),
                          "rms_db": m.get("rms_db"),
                          "src": m.get("src", "") or "",
                          "prompt_text": m.get("prompt_text", "") or "",
                          "prompt_lang": m.get("prompt_lang", "") or "",
                          "mtime": sig[0] if sig else None,
                          "size": sig[1] if sig else None})

        prev = old.get("status", "")
        if files_changed:
            status = "sourced"   # 参考音频换过了，旧的转写/validated 作废，重新走流程
        else:
            status = prev if STATUS_ORDER.get(prev, -1) >= STATUS_ORDER["sourced"] else "sourced"
        reg["personas"][pid] = {"status": status, "gender": p["gender"], "clips": clips,
                                "engine": old.get("engine", "gpt_sovits"),
                                "created_at": old.get("created_at") or now_iso(),
                                "last_qc": old.get("last_qc", "")}
        save_registry(reg, dirty={pid})
        note = f"，状态 {prev}→{status}" if prev and prev != status else ""
        if files_changed:
            note += "（ref 文件被重抓覆盖，旧文本/状态作废）"
        print(f"[{i}/{len(targets)}] {pid}（{p['zh']} · {p['stage']}）← {len(clips)} 段参考音频{note}", flush=True)

    # 顺带按 femaleGroups 补全/修正所有已入库条目的 gender
    patched_pids = set()
    for pid, e in reg["personas"].items():
        g = by_pid.get(pid, {}).get("gender")
        if g and e.get("gender") != g:
            e["gender"] = g
            patched_pids.add(pid)
    if patched_pids:
        save_registry(reg, dirty=patched_pids)
        print(f"[ok] 按 femaleGroups 修正了 {len(patched_pids)} 条 gender 字段", flush=True)
    print(f"\n[完成] registry 现有 {len(reg['personas'])} 位：{REGISTRY}", flush=True)
    return 0


# ---------------------------------------------------------------- 子命令：transcribe

def whisper_one(model, wav):
    """转写单个参考音频，返回 (text, lang)。先带 VAD，拿到空结果再裸转一次。"""
    for kw in ({"vad_filter": True}, {}):
        try:
            segments, info = model.transcribe(str(wav), beam_size=5, **kw)
            text = re.sub(r"\s+", " ", "".join(s.text for s in segments)).strip()
        except Exception as e:
            print(f"\n  [warn] 转写出错 {type(e).__name__}: {str(e)[:80]}", flush=True)
            return "", ""
        if text:
            return text, (info.language or "zh")
    return "", ""


def cmd_transcribe(args):
    reg = load_registry()
    personas = load_personas()
    pinfo = {p["pid"]: p for p in personas}
    order = {p["pid"]: i for i, p in enumerate(personas)}
    only = parse_only(args.only)
    warn_unknown_ids(only, pinfo)

    def lacks_text(e):
        return any(not (c.get("prompt_text") or "").strip() for c in e.get("clips") or [])

    targets = [pid for pid, e in reg["personas"].items()
               if (not only or pid in only)
               and (args.force or STATUS_ORDER.get(e.get("status"), -1) >= STATUS_ORDER["sourced"])
               and e.get("clips") and lacks_text(e)]
    targets.sort(key=lambda x: order.get(x, 9999))
    if not targets:
        print("没有需要转写的（缺 prompt_text 的都已处理，或素材未入库）。", flush=True)
        return 0

    # 环境要在 import faster_whisper 之前配好：HF 国内镜像 + 模型缓存目录（config 的 hf_home）
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    hf_home = None
    try:
        hf_home = paths.dir_of("hf_home")
        os.environ.setdefault("HF_HOME", str(hf_home))
    except Exception:
        pass
    # av(PyAV) 兼容 shim：PyAV 16+ 的 av.open 移除了 metadata_errors 参数，
    # 而 faster-whisper 1.2.x 仍会传它 → 一律剥掉该参数再调（对新旧 av 都安全）。
    try:
        import av as _av
        if not getattr(_av.open, "_idol_compat", False):
            _orig_av_open = _av.open
            def _compat_av_open(*a, **kw):
                kw.pop("metadata_errors", None)
                return _orig_av_open(*a, **kw)
            _compat_av_open._idol_compat = True
            _av.open = _compat_av_open
    except Exception as e:
        print(f"[warn] av 兼容 shim 未生效：{e}", flush=True)
    print(f"加载 faster-whisper（small / cpu / int8），待转写 {len(targets)} 位…"
          f"首次运行会从 hf-mirror.com 下载模型（约几百 MB，存 {hf_home or '默认缓存目录'}）", flush=True)
    from faster_whisper import WhisperModel
    model = WhisperModel("small", device="cpu", compute_type="int8")

    t0 = time.time()
    full, partial = 0, 0
    for i, pid in enumerate(targets, 1):
        e = reg["personas"][pid]
        p = pinfo.get(pid, {})
        label = f"{p.get('zh', '')} · {p.get('stage', '')}" if p else pid
        print(f"[{i}/{len(targets)}] {pid}（{label}）", end="", flush=True)
        folder = VOICES_DIR / pid
        for c in e["clips"]:
            if (c.get("prompt_text") or "").strip():
                continue
            wav = folder / c["file"]
            if not wav.exists():
                print(f"\n  [warn] 缺文件 {c['file']}，跳过该段", flush=True)
                continue
            text, lang = whisper_one(model, wav)
            c["prompt_text"] = text
            c["prompt_lang"] = lang if text else ""
        n_ok = sum(1 for c in e["clips"] if (c.get("prompt_text") or "").strip())
        if n_ok == len(e["clips"]):
            if STATUS_ORDER.get(e.get("status"), -1) < STATUS_ORDER["transcribed"]:
                e["status"] = "transcribed"
            full += 1
        else:
            partial += 1
        write_meta(pid, e)
        save_registry(reg, dirty={pid})
        langs = {c.get("prompt_lang") for c in e["clips"] if c.get("prompt_lang")}
        print(f" → {n_ok}/{len(e['clips'])} 段有文本（{'/'.join(sorted(langs)) or '?'}）", flush=True)

    print(f"\n[完成] 全部转写 {full} 位、部分成功 {partial} 位，用时 {int(time.time() - t0)} 秒。"
          f"空文本的段可重跑本命令再试。", flush=True)
    return 0


# ---------------------------------------------------------------- 子命令：validate

def tts_alive(base):
    try:
        requests.get(f"{base}/healthz", timeout=5)
        return True  # 任何 HTTP 响应都说明服务在
    except Exception:
        return False


def cmd_validate(args):
    reg = load_registry()
    personas = load_personas()
    pinfo = {p["pid"]: p for p in personas}
    order = {p["pid"]: i for i, p in enumerate(personas)}
    only = parse_only(args.only)
    warn_unknown_ids(only, pinfo)

    targets = [pid for pid, e in reg["personas"].items()
               if e.get("clips") and (not only or pid in only)
               and (args.force or e.get("status") not in ("validated", "ready"))]
    targets.sort(key=lambda x: order.get(x, 9999))
    if not targets:
        print("没有待校验的（都已是 validated/ready，或素材未入库）。", flush=True)
        return 0

    # 第一步：本底检查（16k 单声道、5-20s、可读、RMS 合理）
    print(f"第一步：检查 {len(targets)} 位的参考音频格式…", flush=True)
    passed = []
    touched = set()   # dur/rms_db 被刷新过的 pid，最后一次性合并落盘
    for i, pid in enumerate(targets, 1):
        e = reg["personas"][pid]
        msgs, ok_clips = [], 0
        for c in e["clips"]:
            ok, msg, dur, db = check_ref_wav(VOICES_DIR / pid / c["file"])
            if dur:
                c["dur"] = round(dur, 2)
            if db is not None:
                c["rms_db"] = round(db, 1)
            if ok:
                ok_clips += 1
            else:
                msgs.append(f"{c['file']} {msg}")
        touched.add(pid)
        if ok_clips == 0:
            e["status"] = "failed"
            save_registry(reg, dirty={pid})
            print(f"[{i}/{len(targets)}] {pid} —— 全部片段不合格（{'；'.join(msgs)}）→ failed", flush=True)
        else:
            if msgs:
                print(f"[{i}/{len(targets)}] {pid} —— {ok_clips}/{len(e['clips'])} 段合格（{'；'.join(msgs)}）", flush=True)
            passed.append(pid)
    if touched:
        save_registry(reg, dirty=touched)
    if not passed:
        print("[!] 没有通过本底检查的音色，先修素材再跑。", flush=True)
        return 1

    # 第二步：起本地 TTS 服务试合成
    base = args.tts_url.rstrip("/")
    no_text = [pid for pid in passed if any(not (c.get("prompt_text") or "").strip()
                                            for c in reg["personas"][pid]["clips"])]
    if no_text:
        preview = ", ".join(no_text[:5]) + ("…" if len(no_text) > 5 else "")
        print(f"[warn] {len(no_text)} 位还缺 prompt_text（建议先跑 transcribe），服务可能拒绝：{preview}", flush=True)
    print(f"第二步：通过本地 TTS 服务试合成 {len(passed)} 位（{base}）…", flush=True)
    if not tts_alive(base):
        print(f"[!] TTS 服务不可达。请先在 tts/ 目录下启动：\n"
              f"    python tts_server.py\n"
              f"    （服务会加载 {REGISTRY}，起来后再重跑 validate）", flush=True)
        return 1

    ffmpeg = None
    try:
        ffmpeg = find_ffmpeg()
    except RuntimeError as e:
        print(f"[warn] {e}（跳过合成结果的深度检查，只验证 HTTP 200）", flush=True)

    QC_DIR.mkdir(parents=True, exist_ok=True)
    ok_n = fail_n = 0
    for i, pid in enumerate(passed, 1):
        e = reg["personas"][pid]
        p = pinfo.get(pid, {})
        try:
            r = requests.post(f"{base}/v1/audio/speech",
                              json={"model": "gpt-sovits", "voice": pid, "input": TEST_SENTENCE,
                                    "response_format": "mp3", "speed": 1},
                              timeout=300)
        except requests.exceptions.ConnectionError:
            print(f"[{i}/{len(passed)}] {pid} —— 服务中途失联，中止（先起服务再重跑，已完成的不丢）", flush=True)
            break
        except requests.exceptions.Timeout:
            print(f"[{i}/{len(passed)}] {pid} —— 合成超时（>300s），保留状态稍后重试", flush=True)
            fail_n += 1
            continue
        if r.status_code != 200:
            detail = ""
            try:
                detail = (r.json().get("error") or {}).get("message", "")
            except Exception:
                pass
            print(f"[{i}/{len(passed)}] {pid} —— HTTP {r.status_code} {str(detail)[:80]}", flush=True)
            fail_n += 1
            continue
        if len(r.content) < 512:
            print(f"[{i}/{len(passed)}] {pid} —— 返回音频过小（{len(r.content)}B），疑似异常", flush=True)
            fail_n += 1
            continue
        out = QC_DIR / f"{pid}_test.mp3"
        out.write_bytes(r.content)
        if ffmpeg:
            deep_ok, deep_msg = deep_check_mp3(out, ffmpeg)
            if not deep_ok:
                e["status"] = "failed"
                save_registry(reg, dirty={pid})
                print(f"[{i}/{len(passed)}] {pid} —— 合成异常（{deep_msg}）→ failed", flush=True)
                fail_n += 1
                continue
        else:
            deep_msg = "未深度检查"
        e["status"] = "validated"
        e["last_qc"] = now_iso()
        ok_n += 1
        save_registry(reg, dirty={pid})
        print(f"[{i}/{len(passed)}] {pid}（{p.get('zh', '')} · {p.get('stage', '')}）"
              f"→ validated（{deep_msg}，{len(r.content) // 1024}KB）", flush=True)

    print(f"\n[完成] validated {ok_n} 位，未通过/跳过 {fail_n} 位。", flush=True)
    return 0


# ---------------------------------------------------------------- 子命令：report

def audio_tag(src):
    if not src:
        return "<span class='dim'>—</span>"
    return f"<audio controls preload='none' src='{html.escape(src)}'></audio>"


def cmd_report(args):
    reg = load_registry()
    personas = load_personas()
    only = parse_only(args.only)
    warn_unknown_ids(only, [p["pid"] for p in personas])

    counts, rows = {}, []
    for p in personas:
        pid = p["pid"]
        if only and pid not in only:
            continue
        e = reg["personas"].get(pid)
        status = e.get("status") if e else "missing"
        if status not in STATUS_LABEL:
            status = "missing"
        counts[status] = counts.get(status, 0) + 1
        clips = (e or {}).get("clips") or []
        best = None
        for c in clips:  # 得分最高的片段
            if c.get("score") is not None and (best is None or (c.get("score") or 0) > (best.get("score") or 0)):
                best = c
        if best is None and clips:
            best = clips[0]
        score = f"{best['score']:.2f}" if best and best.get("score") is not None else "—"
        dur = f"{best['dur']:.1f}s" if best and best.get("dur") else "—"
        ref_file = best["file"] if best else "ref01.wav"
        ref_src = f"../{pid}/{ref_file}" if (VOICES_DIR / pid / ref_file).exists() else ""
        test_src = f"{pid}_test.mp3" if (QC_DIR / f"{pid}_test.mp3").exists() else ""
        rows.append(
            f"<tr data-st=\"{status}\"><td>{html.escape(p['zh'] + ' · ' + p['stage'])}"
            f"<span class='grp'>{html.escape(p['group'])}</span></td>"
            f"<td class='pid'>{pid}</td>"
            f"<td><span class='st st-{status}'>{STATUS_LABEL[status]}</span></td>"
            f"<td>{score}</td><td>{dur}</td><td>{len(clips)}</td>"
            f"<td>{audio_tag(ref_src)}</td><td>{audio_tag(test_src)}</td></tr>")

    badges = "".join(f"<span class='st st-{st}'>{STATUS_LABEL[st]} {counts.get(st, 0)}</span> "
                     for st in ("ready", "validated", "transcribed", "sourced", "failed", "missing"))
    ready_total = counts.get("validated", 0) + counts.get("ready", 0)
    opts = "".join(f"<option value='{st}'>{STATUS_LABEL[st]}</option>"
                   for st in ("ready", "validated", "transcribed", "sourced", "failed", "missing"))
    doc = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>IdolTalk 音色库质检报告</title>
<style>
 body{{font:14px/1.5 system-ui,'Microsoft YaHei',sans-serif;margin:24px;color:#222;background:#fafafa}}
 h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#777;font-size:12px;margin-bottom:10px}}
 .sum{{margin:10px 0 16px}} .sum .st{{margin-right:4px}}
 .st{{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;color:#fff;white-space:nowrap}}
 .st-ready{{background:#16a34a}} .st-validated{{background:#2563eb}} .st-transcribed{{background:#0891b2}}
 .st-sourced{{background:#64748b}} .st-failed{{background:#dc2626}} .st-missing{{background:#d1d5db;color:#555}}
 table{{border-collapse:collapse;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
 th,td{{border:1px solid #e5e7eb;padding:5px 10px;text-align:left;vertical-align:middle}}
 th{{background:#f3f4f6;position:sticky;top:0;font-size:13px}}
 .pid{{font-family:Consolas,monospace;font-size:12px;color:#555}}
 .grp{{display:block;font-size:11px;color:#999}} audio{{width:180px;height:30px;display:block}}
 .dim{{color:#bbb}} select{{margin-left:12px;padding:2px 6px}}
</style></head><body>
<h1>IdolTalk 音色库质检报告</h1>
<div class="sub">生成于 {now_iso()} · 共 {len(rows)} 位 · 可用（validated+ready）{ready_total} 位 · 参考音频取每位得分最高的片段</div>
<div class="sum">{badges}<select onchange="var v=this.value;document.querySelectorAll('tbody tr').forEach(function(tr){{tr.style.display=(!v||tr.dataset.st===v)?'':'none'}})">
<option value="">全部状态</option>{opts}</select></div>
<table><thead><tr><th>爱豆</th><th>persona-id</th><th>状态</th><th>最佳得分</th><th>时长</th><th>段数</th>
<th>参考音频</th><th>合成测试</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>
</body></html>"""

    QC_DIR.mkdir(parents=True, exist_ok=True)
    out = QC_DIR / "qc_report.html"
    out.write_text(doc, encoding="utf-8")
    print(f"[完成] 报告 → {out}", flush=True)
    print(f"       共 {len(rows)} 位：{', '.join(f'{STATUS_LABEL[k]} {v}' for k, v in sorted(counts.items()))}", flush=True)
    return 0


# ---------------------------------------------------------------- 入口

def main():
    ap = argparse.ArgumentParser(description="IdolTalk 音色库批量管理（ingest/transcribe/validate/report）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(sp, force=False):
        sp.add_argument("--only", default="", help="只处理指定 persona-id（逗号分隔）")
        if force:
            sp.add_argument("--force", action="store_true", help="忽略状态过滤，强制重跑")

    common(sub.add_parser("ingest", help="扫描素材目录，upsert 进 registry.json"))
    sp_t = sub.add_parser("transcribe", help="faster-whisper 转写参考音频，补 prompt_text")
    common(sp_t, force=True)
    sp_v = sub.add_parser("validate", help="格式检查 + 本地 TTS 试合成")
    common(sp_v, force=True)
    sp_v.add_argument("--tts-url", default=DEFAULT_TTS, help=f"本地 TTS 服务地址（默认 {DEFAULT_TTS}）")
    common(sub.add_parser("report", help="生成 qc\\qc_report.html 质检报告"))

    args = ap.parse_args()
    fn = {"ingest": cmd_ingest, "transcribe": cmd_transcribe,
          "validate": cmd_validate, "report": cmd_report}[args.cmd]
    sys.exit(fn(args) or 0)


if __name__ == "__main__":
    main()
