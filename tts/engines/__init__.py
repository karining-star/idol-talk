# -*- coding: utf-8 -*-
"""
TTS 引擎抽象层：统一引擎接口 + 工厂 + 音色库（registry.json / meta.json）热缓存。

各引擎实现放同目录（gpt_sovits.py 等），在 load_engine() 里注册；
新增云端引擎时继承 TTSEngine 即可，服务层（tts_server.py）不用改。
"""
import json
from abc import ABC, abstractmethod
from pathlib import Path

# 未在 config 里配 voices_dir 时的兜底目录（仓库下 voices/）；
# 正常由 tts_server.load_config() 传入，这里是引擎被单独调用时的保底。
DEFAULT_VOICES_DIR = Path(__file__).resolve().parents[2] / "voices"


class EngineUnavailableError(Exception):
    """后端引擎没启动（服务层映射为 503 + 中文提示）。"""


class VoiceNotReadyError(Exception):
    """音色存在但没有可用参考音频（服务层映射为 400 voice_not_ready）。"""


class TTSEngine(ABC):
    """所有 TTS 引擎的基类。

    约定：
    - synthesize() 返回一个【临时】mp3 路径（cache/tmp 下），调用方负责移到
      最终位置或删除；引擎层用全局 asyncio.Lock 串行合成（本机单卡 CPU）；
    - startup() 里后端不在线也不要抛异常（服务照常起，/healthz 报 down，
      合成请求才返回 503）。
    """

    name = "base"

    @abstractmethod
    async def startup(self):
        """异步初始化（建 HTTP 客户端、探测后端、清理临时文件等）。"""

    async def shutdown(self):
        """释放资源（默认无操作）。"""

    @abstractmethod
    async def synthesize(self, text: str, voice_id: str, speed: float = 1.0) -> Path:
        """合成 text（英语），voice_id 为 persona-id，speed 传引擎语速因子。"""

    @abstractmethod
    async def is_up(self) -> bool:
        """后端是否在线（/healthz 用，探测要快）。"""

    @abstractmethod
    def list_ready(self) -> list:
        """状态为 ready 的音色 id 列表。"""


def load_engine(name: str, config: dict) -> TTSEngine:
    """按名字实例化引擎。config 是 config.json 内容（可含运行时追加键，如共享的 store）。"""
    name = (name or "").strip()
    if name == "gpt_sovits":
        from .gpt_sovits import GPTSoVITSEngine
        return GPTSoVITSEngine(config)
    if name == "minimax":   # MiniMax（海螺）声音复刻
        from .minimax import MiniMaxEngine
        return MiniMaxEngine(config)
    if name == "volc":      # 火山引擎（豆包声音复刻）
        from .volc import VolcEngine
        return VolcEngine(config)
    # —— 云端引擎（模块已就位，按需注册/新增）——
    if name == "cosyvoice":  # 阿里云百炼 CosyVoice 声音复刻
        from .cosyvoice import CosyVoiceEngine
        return CosyVoiceEngine(config)
    raise ValueError(f"未知引擎 {name!r}（可选：gpt_sovits / minimax / volc / cosyvoice）")


# ------------------------------------------------------------------ 音色库缓存

def _clip_rank(c: dict):
    """参考切片排序键：有转写文本 > score 高 > 时长长。"""
    return (1 if c.get("prompt_text") else 0, float(c.get("score") or 0), float(c.get("dur") or 0))


class VoiceStore:
    """registry.json + <pid>/meta.json 的进程内缓存，mtime 变了自动重读（热加载）。"""

    def __init__(self, voices_dir):
        self.voices_dir = Path(voices_dir)
        self._reg = {"version": 1, "engine_default": "gpt_sovits", "personas": {}}
        self._reg_mtime = None
        self._meta = {}  # pid -> (mtime, data)

    def registry(self) -> dict:
        p = self.voices_dir / "registry.json"
        try:
            m = p.stat().st_mtime
        except OSError:
            return self._reg  # 文件还不存在 → 空注册表
        if m != self._reg_mtime:
            try:
                self._reg = json.loads(p.read_text(encoding="utf-8"))
                self._reg_mtime = m
            except Exception:
                pass  # 坏文件 → 沿用上次缓存，等 mtime 变了再试
        return self._reg

    def persona(self, pid: str):
        return (self.registry().get("personas") or {}).get(pid)

    def meta(self, pid: str) -> dict:
        p = self.voices_dir / pid / "meta.json"
        try:
            m = p.stat().st_mtime
        except OSError:
            return {}
        hit = self._meta.get(pid)
        if hit and hit[0] == m:
            return hit[1]
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        self._meta[pid] = (m, data)
        return data

    def best_clip(self, pid: str):
        """registry 与 meta 的 clips 合并去重，取排序最高且文件确实存在的一条。"""
        merged = {}
        for src in ((self.persona(pid) or {}).get("clips") or [], self.meta(pid).get("clips") or []):
            for c in src:
                f = c.get("file")
                # 不存在的条目直接放入（旧写法拿空 dict 的默认排名 (0,0,0) 比严格 >，
                # 排名恰为 (0,0,0) 的切片——无转写/score/dur——永远进不了候选集）
                if f and (f not in merged or _clip_rank(c) > _clip_rank(merged[f])):
                    merged[f] = c
        for c in sorted(merged.values(), key=_clip_rank, reverse=True):
            if (self.voices_dir / pid / c["file"]).is_file():
                return c
        return None
