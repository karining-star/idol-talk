# -*- coding: utf-8 -*-
"""
GPT-SoVITS api_v2 引擎：把统一接口翻译成 api_v2 的 POST /tts，wav 转 mp3。

api_v2 契约（冻结，勿改）：
  POST {gptsovits_url}/tts
  {"text":..., "text_lang":"en", "ref_audio_path":绝对路径, "prompt_text":...,
   "prompt_lang":"zh", "text_split_method":"cut5", "speed_factor":1.0,
   "streaming_mode":false, "media_type":"wav"}
  成功 → audio/wav 字节；失败 → JSON（含 message）
本地联调可用 mock_gptsovits.py 顶替 api_v2。
"""
import asyncio
import contextlib
import glob
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import httpx

from . import EngineUnavailableError, TTSEngine, VoiceNotReadyError, VoiceStore

GPTSOVITS_DOWN = "GPT-SoVITS api_v2 未启动，请先运行整合包的 api_v2.py（默认 http://127.0.0.1:9880）"

# 并发闸：实测合成只用 ~1.3 核（16 线程机器），4 路并发互不抢核、吞吐约 2 倍。
# 应用侧把回复分句并发合成，等待时间减半、每句完整合成保证无卡顿。
_SYNTH_LOCK = asyncio.Semaphore(4)

# 与 voice_batch.py 的 find_ffmpeg 保持一致的候选位置
_FFMPEG_PATTERNS = [
    r"C:\Users\95242\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe",
    r"C:\Users\95242\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg*\ffmpeg*\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"D:\AI\ffmpeg\bin\ffmpeg.exe",
]


def find_ffmpeg() -> str:
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
    for pat in _FFMPEG_PATTERNS:
        for hit in glob.glob(pat):
            if Path(hit).is_file():
                return hit
    raise RuntimeError(
        "找不到 ffmpeg。解决办法：`winget install Gyan.FFmpeg` 后重开终端，"
        "或设环境变量 FFMPEG_PATH 指向 ffmpeg.exe 完整路径。"
    )


class GPTSoVITSEngine(TTSEngine):
    name = "gpt_sovits"

    def __init__(self, config: dict):
        self.base_url = (config.get("gptsovits_url") or "http://127.0.0.1:9880").rstrip("/")
        self.voices_dir = Path(config.get("voices_dir") or r"C:\Users\95242\Desktop\IdolTalk-voices")
        cd = Path(config.get("cache_dir") or "cache")
        self.cache_dir = cd if cd.is_absolute() else Path(__file__).resolve().parent.parent / cd
        # 服务层会传入共享的 VoiceStore；单独调用（voice_batch 等）时自建
        self.store = config.get("store") or VoiceStore(self.voices_dir)
        self.client = None

    async def startup(self):
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0))
        tmp = self.cache_dir / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - 86400  # 顺手清掉超过一天的残留临时文件
        for f in tmp.iterdir():
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass
        up = await self.is_up()
        print(f"[engine] GPT-SoVITS api_v2 @ {self.base_url} —— "
              + ("在线" if up else "未启动（/healthz 会报 down，合成请求返回 503）"), flush=True)

    async def shutdown(self):
        if self.client:
            await self.client.aclose()
            self.client = None

    async def is_up(self) -> bool:
        if not self.client:
            return False
        for path in ("/", "/tts"):  # 任何 HTTP 响应（含 404/405）都说明端口活着
            try:
                await self.client.get(self.base_url + path, timeout=3.0)
                return True
            except Exception:
                continue
        return False

    def list_ready(self) -> list:
        return [pid for pid, per in sorted((self.store.registry().get("personas") or {}).items())
                if per.get("status") == "ready"]

    def _build_payload(self, text: str, voice_id: str, speed: float, streaming: bool):
        """构造 api_v2 请求体（整句/流式共用）。"""
        clip = self.store.best_clip(voice_id)
        if not clip:
            raise VoiceNotReadyError(
                f"音色 {voice_id} 没有可用的参考音频（registry/meta 里没有 clips，或文件不存在）")
        # 语速：全局默认 +12%（2026-10-05 用户验收通过——克隆语速偏慢，+12% 贴近真人自然语速）；
        # registry 里每位爱豆可用 speed_scale 单独覆盖
        persona = self.store.persona(voice_id) or {}
        try:
            scale = float(persona.get("speed_scale", 1.12))
        except (TypeError, ValueError):
            scale = 1.12
        return {
            "text": text,
            "text_lang": "en",  # 冻结契约：应用是英语陪练，正文恒为英文
            "ref_audio_path": str((self.voices_dir / voice_id / clip["file"]).resolve()),
            "prompt_text": clip.get("prompt_text") or "",
            "prompt_lang": clip.get("prompt_lang") or "zh",
            "text_split_method": "cut0",  # 不按标点切段——cut5 每段重置语气，听起来一顿一顿
            "speed_factor": round(float(speed) * scale, 3),
            "streaming_mode": 3 if streaming else False,
            "media_type": "wav",
        }

    async def synthesize_stream(self, text: str, voice_id: str, speed: float = 1.0):
        """流式合成：api_v2 streaming_mode=3 的 WAV 块直接 yield（不做转码）。

        实测 WAV 直通最平滑：首音 ~3 秒、之后连续到达（ffmpeg 管道会攒 5-8 秒才吐第一块）。
        调用方（tts_server）收集全部块，结束后自行转 mp3 缓存。
        全程持 _SYNTH_LOCK（CPU 单卡串行）。
        """
        payload = self._build_payload(text, voice_id, speed, streaming=True)
        if not self.client:
            raise EngineUnavailableError(GPTSOVITS_DOWN)
        async with _SYNTH_LOCK:
            async with self.client.stream(
                "POST", self.base_url + "/tts", json=payload,
                timeout=httpx.Timeout(None, connect=5.0),
            ) as r:
                if r.status_code != 200:
                    body = await r.aread()
                    raise RuntimeError(
                        f"GPT-SoVITS 返回 {r.status_code}: {body[:200].decode('utf-8', 'replace')}")
                async for chunk in r.aiter_bytes(8192):
                    yield chunk

    async def synthesize(self, text: str, voice_id: str, speed: float = 1.0) -> Path:
        clip = self.store.best_clip(voice_id)
        if not clip:
            raise VoiceNotReadyError(
                f"音色 {voice_id} 没有可用的参考音频（registry/meta 里没有 clips，或文件不存在）")
        payload = self._build_payload(text, voice_id, speed, streaming=False)
        async with _SYNTH_LOCK:
            if not self.client:
                raise EngineUnavailableError(GPTSOVITS_DOWN)
            tmp_dir = self.cache_dir / "tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            tag = uuid.uuid4().hex[:12]
            wav = tmp_dir / f"{tag}.wav"
            mp3 = tmp_dir / f"{tag}.mp3"
            try:
                try:
                    r = await self.client.post(self.base_url + "/tts", json=payload)
                except httpx.HTTPError as e:  # 连不上/超时 → 引擎没启动
                    raise EngineUnavailableError(f"{GPTSOVITS_DOWN}（{type(e).__name__}）")
                if r.status_code != 200:
                    try:
                        msg = r.json().get("message") or r.text[:200]
                    except Exception:
                        msg = r.text[:200]
                    raise RuntimeError(f"GPT-SoVITS 返回 {r.status_code}: {msg}")
                ctype = (r.headers.get("content-type") or "").lower()
                if "wav" not in ctype and "octet-stream" not in ctype:
                    raise RuntimeError(f"GPT-SoVITS 返回了意外的内容类型 {ctype}（预期 audio/wav）")
                wav.write_bytes(r.content)
                try:
                    await self._wav_to_mp3(wav, mp3)
                except Exception:
                    mp3.unlink(missing_ok=True)  # 清掉半成品
                    raise
            finally:
                wav.unlink(missing_ok=True)
        return mp3

    @staticmethod
    async def _wav_to_mp3(wav: Path, mp3: Path):
        """ffmpeg 转 128k mp3。用线程跑子进程，不依赖事件循环的 subprocess 支持。"""
        cmd = [find_ffmpeg(), "-y", "-loglevel", "error", "-i", str(wav),
               "-codec:a", "libmp3lame", "-b:a", "128k", str(mp3)]
        proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True)
        if proc.returncode != 0 or not mp3.is_file():
            raise RuntimeError(f"ffmpeg 转 mp3 失败: {proc.stderr.decode('utf-8', 'replace')[-300:]}")
