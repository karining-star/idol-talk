# -*- coding: utf-8 -*-
r"""
阿里云百炼（Model Studio）CosyVoice 声音复刻引擎：OSS 上传参考音频 → 建复刻音色 → 云端合成 mp3。

接口契约（2026-10-05 对照官方文档核对，出处链接见《云端试听指南.md》§7）：
1) 建复刻音色（REST；dashscope SDK 1.27.x 已移除老的 VoiceEnrollmentService，
   官方现行文档只给 HTTP 调用方式，故本引擎走 httpx 直连）：
   POST https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization
   Headers: Authorization: Bearer <aliyun_dashscope_key>、Content-Type: application/json
   Body: {"model": "voice-enrollment", "input": {"action": "create_voice",
          "target_model": <复刻与合成必须同一个模型>, "prefix": <≤10 位纯字母数字>,
          "url": <公网可访问的音频 URL>}}
   target_model 可选（北京地域）：cosyvoice-v3.5-plus / v3.5-flash / v3-plus / v3-flash /
   v2 / v1；其中 v3.5-plus、v3.5-flash、v3-plus、v3-flash 支持 language_hints（提示样本
   语种），v3.5-plus、v3.5-flash、v3-flash 另支持 enable_preprocess / max_prompt_audio_length。
   成功返回 output.voice_id，格式 {target_model}-{prefix}-{唯一标识}（如
   cosyvoice-v3.5-flash-idoltalk-xxxxx）；失败返回 HTTP 4xx/5xx +
   {"code": ..., "message": ..., "request_id": ...}。
2) 查询音色状态（同一 endpoint）：
   {"model": "voice-enrollment", "input": {"action": "query_voice", "voice_id": ...}}
   → output.status：DEPLOYING（审核中，一般几秒到几分钟）/ OK（可用）/
   UNDEPLOYED（审核未通过，不可用）。另有 list_voice（按 prefix 查列表）。
3) 语音合成（dashscope SDK，WebSocket 单向流式）：
   from dashscope.audio.tts_v2 import SpeechSynthesizer, AudioFormat
   SpeechSynthesizer(model=<与复刻一致>, voice=<voice_id>,
                     format=AudioFormat.MP3_22050HZ_MONO_256KBPS,
                     speech_rate=<[0.5,2.0]，1.0=原速>).call(text) → 完整 mp3 bytes
   API Key 取 dashscope.api_key 全局；任务失败时 call() 抛 RequestFailure
   （属性 name / message / http_code / request_id）。
4) 参考音频要求（官方文档「音频要求」）：
   wav(16bit)/mp3/m4a；推荐 10~20 秒、最长 60 秒；≤10MB；≥16kHz；单声道（双声道只取
   首声道）；至少 5 秒连续清晰人声、不能有背景音乐/环境噪音/其他人声。
   我们的切片是 3~10 秒单条常不达标 → 本引擎以 best_clip（score 最高片段）为锚点，
   ffmpeg 可用时续拼同语种切片凑到 10~20 秒推荐窗口（宁短勿混语种），再上传 OSS。
5) 计费/额度（官方「配额与计费」）：北京地域开通百炼后 90 天内 1000 次免费创建音色
   （创建失败不占额度，删除不返还）；合成按字符计费（v3.5-flash 0.8 元/万字符、
   v3.5-plus 1.5、v2 2.0）；音色 1 年未用于合成会被自动清理；每账号上限 1000 个音色。

凭据（config.json，冻结键）：aliyun_dashscope_key（百炼 API Key，华北 2-北京地域）
/ aliyun_oss_endpoint（桶概览「外网访问」Endpoint，如 oss-cn-beijing.aliyuncs.com）
/ aliyun_oss_bucket（私有桶名）/ aliyun_oss_ak / aliyun_oss_sk（AccessKey ID/Secret，
复刻接口只收公网 URL → 音频传自己的 OSS 桶再给带签名的临时直链，桶保持私有）。
可选键：cosyvoice_target_model（默认 cosyvoice-v3.5-flash，可换 v3.5-plus / v2 等。
注意音色不能跨模型使用——本引擎按 persona 簿记的 cosyvoice_voice_model 取合成模型，
换 target_model 只影响之后新建的音色）、
cosyvoice_preprocess（默认 false：本地管线已降噪，关闭预处理最保音色还原度；
参考音频底噪大时改 true，官方注明开启会略损还原度）。
音色 ID：registry persona 的 "cosyvoice_voice_id"（首次合成自动注册并写回 registry.json，
附 cosyvoice_voice_model / cosyvoice_voice_status / cosyvoice_voice_created_at 簿记字段）。
凭据缺失：startup 不崩溃，is_up() = False，synthesize 抛 EngineUnavailableError。
依赖：D:\AI\Python312 已装 dashscope + oss2（缺库时同样按未配置处理并给安装提示）。
"""
import asyncio
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx

try:
    import msvcrt  # Windows 跨进程文件锁（registry 并发写防护），非 Windows 退化为无锁
except ImportError:
    msvcrt = None

try:  # SDK 缺失不能挡模块导入（compare_engines 要先 import 拿类），运行时再按未配置处理
    import dashscope
    from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer
except ImportError:
    dashscope = None
    AudioFormat = None
    SpeechSynthesizer = None

try:
    import oss2
    import oss2.exceptions
except ImportError:
    oss2 = None
    oss2.exceptions = None  # type: ignore[module-attr]

from . import (EngineUnavailableError, TTSEngine, VoiceNotReadyError, VoiceStore,
               _clip_rank)
from .gpt_sovits import find_ffmpeg  # 与 gpt_sovits / volc / minimax 保持同一份 ffmpeg 解析

COSYVOICE_UNCONFIGURED = (
    "阿里云百炼 CosyVoice 未配置：请在 config.json 填齐 aliyun_dashscope_key / "
    "aliyun_oss_endpoint / aliyun_oss_bucket / aliyun_oss_ak / aliyun_oss_sk 五键"
    "（填写方法见《云端试听指南.md》§3：百炼控制台 API-KEY 页 + OSS 五件套），"
    "并确认已 pip install dashscope oss2"
)

# 复刻/合成的固定入口（官方文档：现有域名仍可正常使用，无需业务空间专属域名）
DASHSCOPE_BASE = "https://dashscope.aliyuncs.com"
ENROLL_URL = DASHSCOPE_BASE + "/api/v1/services/audio/tts/customization"

DEFAULT_TARGET_MODEL = "cosyvoice-v3.5-flash"

# language_hints 的模型支持集（官方文档：qwen-audio 系列不在本引擎可选范围，略）
_HINTS_LANG_MODELS = {"cosyvoice-v3.5-plus", "cosyvoice-v3.5-flash",
                      "cosyvoice-v3-plus", "cosyvoice-v3-flash"}
# enable_preprocess / max_prompt_audio_length 的模型支持集（比 language_hints 少 v3-plus）
_PREPROCESS_MODELS = {"cosyvoice-v3.5-plus", "cosyvoice-v3.5-flash",
                      "cosyvoice-v3-flash"}

# 复刻音频时长窗口（官方：推荐 10~20 秒、至少 5 秒连续人声、最长 60 秒）
REF_TARGET_SECONDS = 12.0   # 多段拼接的目标总时长（10~20 秒窗口内留余量）
REF_MAX_SECONDS = 20.0      # 拼接总时长上限（超出服务端也只用 max_prompt_audio_length 那部分）
REF_MIN_SECONDS = 5.0       # 低于此值大概率被拒（打印警告，仍尝试让服务端裁决）
MAX_PROMPT_AUDIO_LENGTH = 20.0  # 上传 20 秒就允许模型用满 20 秒（参数范围 [3,30]）

# OSS：私有桶 + 24 小时带签名直链交给复刻接口（注册只发生一次，24h 足够）
OSS_KEY_PREFIX = "ref-audio/"
OSS_URL_EXPIRES = 86400
OSS_CONNECT_TIMEOUT = 10

# 合成语速（speech_rate 合法区间 [0.5, 2.0]，1.0=原速）
SPEED_MIN, SPEED_MAX = 0.5, 2.0
SPEED_SCALE_DEFAULT = 1.12  # 与 gpt_sovits 一致（2026-10-05 验收：克隆语速偏慢，+12%）
SYNTH_TIMEOUT_MS = 120000   # 单次合成（含 WebSocket 建连）总超时

# 注册后审核等待：DEPLOYING 一般几秒到几分钟，最多等 60 秒再放行试合成（服务端最终裁决）
ENROLL_WAIT_TIMEOUT = 60.0
ENROLL_POLL_INTERVAL = 5.0

# 常见 DashScope 错误码/错误名的可操作中文提示（REST 与 WebSocket 两侧通用）
_CODE_CN = {
    "InvalidApiKey": "API Key 无效（或地域不符：百炼控制台右上角需选华北 2-北京再创建 Key）",
    "InvalidApiKey.NotFound": "API Key 不存在（核对 aliyun_dashscope_key 是否复制完整）",
    "Arrearage": "账户已欠费，请到阿里云百炼控制台「费用」充值",
    "Throttling": "触发限流，请稍后重试",
    "Throttling.RateQuota": "触发限流（并发/频率超限），请稍后重试",
    "Throttling.AllocationQuota": "账号额度超限，请检查百炼控制台额度或稍后重试",
    "InvalidParameter": "请求参数不合法（prefix 需 ≤10 位纯字母数字；音频 URL 需公网可访问）",
    "DataInspectionFailed": "内容审核未通过（文本或参考音频含敏感内容），请更换后重试",
    "InternalError": "服务端内部错误，可重试",
    "UnsupportedOperation": "接口/参数组合不被支持（如给 v2 音色传了 v3.5 专属参数）",
}


def _f(x, default=0.0):
    """容错的 float()。"""
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _sanitize_prefix(pid: str) -> str:
    """persona-id → 复刻接口 prefix（仅数字和英文字母，≤10 字符）。
    生成音色名格式 {target_model}-{prefix}-{唯一标识}，唯一标识由服务端保证，
    prefix 短一点也不怕撞名（如 aespa-giselle / aespa-karina 都截成 aespa 前缀也能区分）。"""
    s = re.sub(r"[^0-9A-Za-z]", "", pid or "").lower()
    return s[:10] or "idol"


def _fmt_rest_error(r: httpx.Response) -> str:
    """把复刻 REST 接口返回的错误响应拼成一句中文可读错误（附带排查提示）。"""
    code, msg = "", ""
    try:
        j = r.json()
        code = str(j.get("code") or "")
        msg = str(j.get("message") or "")
    except Exception:
        msg = r.text[:200]
    hint = _CODE_CN.get(code, "")
    low = f"{code} {msg}".lower()
    if not hint:
        if r.status_code in (401, 403) or "api" in low and "key" in low:
            hint = _CODE_CN["InvalidApiKey"]
        elif "voice" in low and ("limit" in low or "quota" in low or "1000" in low):
            hint = "自定义音色数已达上限（每账号 1000 个）：删除不用音色后重试"
        elif "voice" in low and ("not" in low or "exist" in low):
            hint = "音色不存在（可能已被自动清理，本地字段会自愈重建）"
    base = f"HTTP {r.status_code} code={code or '-'} {msg}"
    return f"{base}（{hint}）" if hint else base


def _acquire_registry_lock(lock_path: Path):
    """拿 voices_dir/registry.lock 的跨进程排它锁（1 字节 msvcrt.locking）。

    协议与 voice_batch.py / minimax.py 完全一致（同一把锁）：registry.json 有多个
    长跑写者，整文件 load-modify-save 不加锁会互相静默抹掉对方刚写入的条目。
    LK_LOCK 拿不到时最多自旋约 10 秒，仍失败返回 None（调用方退化为无锁写入并警告）。"""
    if msvcrt is None:
        return None
    f = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        f = open(lock_path, "a+b")
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        return f
    except OSError:
        if f:
            f.close()
        return None


def _release_registry_lock(f):
    """释放 _acquire_registry_lock 拿到的锁（句柄为 None 时无操作）。"""
    if f is None:
        return
    try:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        f.close()


class CosyVoiceEngine(TTSEngine):
    """阿里云百炼 CosyVoice 声音复刻引擎（name=cosyvoice）。

    与 gpt_sovits 同一抽象：synthesize 返回 cache/tmp 下的临时 mp3，调用方
    （tts_server / compare_engines）负责移到最终位置或删除。云端引擎无需本地
    后端，is_up() 只看凭据是否配齐 + 最近一次调用有没有报鉴权错（探测零开销）。
    合成走 dashscope WebSocket SDK（阻塞调用放线程跑），注册走 REST + OSS。
    """

    name = "cosyvoice"
    # 冻结接口：云端音色 id 存 registry persona 的 引擎名 + "_voice_id"
    VOICE_FIELD = "cosyvoice_voice_id"

    def __init__(self, config: dict):
        self.api_key = str(config.get("aliyun_dashscope_key") or "").strip()
        self.oss_endpoint = str(config.get("aliyun_oss_endpoint") or "").strip()
        self.oss_bucket_name = str(config.get("aliyun_oss_bucket") or "").strip()
        self.oss_ak = str(config.get("aliyun_oss_ak") or "").strip()
        self.oss_sk = str(config.get("aliyun_oss_sk") or "").strip()
        self.target_model = str(config.get("cosyvoice_target_model")
                                or DEFAULT_TARGET_MODEL).strip() or DEFAULT_TARGET_MODEL
        self.preprocess = bool(config.get("cosyvoice_preprocess", False))
        # SDK 缺失视同未配置（错误信息里带 pip install 提示）
        self._sdk_missing = []
        if dashscope is None:
            self._sdk_missing.append("dashscope")
        if oss2 is None:
            self._sdk_missing.append("oss2")
        self.voices_dir = Path(config.get("voices_dir")
                                or r"C:\Users\95242\Desktop\IdolTalk-voices")
        cd = Path(config.get("cache_dir") or "cache")
        self.cache_dir = cd if cd.is_absolute() else Path(__file__).resolve().parent.parent / cd
        # 服务层会传入共享的 VoiceStore；单独调用（enroll_all 等）时自建
        self.store = config.get("store") or VoiceStore(self.voices_dir)
        self.client = None          # 复刻/查询 REST 用（httpx）
        self._bucket = None         # OSS 桶（oss2，阻塞 SDK，懒建）
        self.tmp_dir = self.cache_dir / "tmp"
        self._enroll_lock = asyncio.Lock()   # 注册 + 写回 registry 串行化（防重复建音色）
        self._auth_failed = False   # 最近一次调用是否报鉴权错（is_up 依据）

    # ------------------------------------------------------------------ 生命周期

    @property
    def creds_ok(self) -> bool:
        return bool(self.api_key and self.oss_endpoint and self.oss_bucket_name
                    and self.oss_ak and self.oss_sk)

    @property
    def configured(self) -> bool:
        return self.creds_ok and not self._sdk_missing

    async def startup(self):
        # 凭据缺失也不崩溃：服务照常起，/healthz 报 down，合成请求才返回 503
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(180.0, connect=10.0),
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - 86400  # 顺手清掉超过一天的残留临时文件
        for f in self.tmp_dir.iterdir():
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass
        if not self.creds_ok:
            print("[engine] 阿里云百炼 CosyVoice —— 未配置（" + COSYVOICE_UNCONFIGURED
                  + "）；/healthz 会报 down，合成请求返回 503", flush=True)
            return
        if self._sdk_missing:
            print(f"[engine] 阿里云百炼 CosyVoice —— 缺 Python 库：{','.join(self._sdk_missing)}"
                  f"（D:\\AI\\Python312\\python.exe -m pip install {' '.join(self._sdk_missing)}），"
                  "装好前引擎按未配置处理", flush=True)
            return
        if dashscope is not None:
            dashscope.api_key = self.api_key  # SpeechSynthesizer 从全局取 Key
        print(f"[engine] 阿里云百炼 CosyVoice @ {DASHSCOPE_BASE} —— 已配置（"
              f"target_model={self.target_model}，OSS 桶 {self.oss_bucket_name} @ "
              f"{self.oss_endpoint}，预处理={'开' if self.preprocess else '关'}）；"
              "没有 cosyvoice_voice_id 的音色会在首次合成时自动注册并写回 registry", flush=True)
        await self._probe_reachability()

    async def shutdown(self):
        if self.client:
            await self.client.aclose()
        self.client = None
        self._bucket = None

    async def is_up(self) -> bool:
        # 云端引擎：凭据齐 + 客户端在 + 最近一次调用没报鉴权错（鉴权错误后置 False，
        # 修好 config 重启服务恢复；网络波动不影响——那不是配置问题）
        return bool(self.client and self.configured and not self._auth_failed)

    def list_ready(self) -> list:
        return [pid for pid, per in sorted((self.store.registry().get("personas") or {}).items())
                if per.get("status") == "ready"]

    # ------------------------------------------------------------------ 合成主流程

    async def synthesize(self, text: str, voice_id: str, speed: float = 1.0) -> Path:
        if not self.client or not self.creds_ok or self._sdk_missing:
            hint = COSYVOICE_UNCONFIGURED
            if self._sdk_missing:
                hint = (f"CosyVoice 引擎缺依赖库 {','.join(self._sdk_missing)}："
                        f"请先 D:\\AI\\Python312\\python.exe -m pip install "
                        f"{' '.join(self._sdk_missing)}")
            raise EngineUnavailableError(hint)
        text = (text or "").strip()
        if not text:
            raise RuntimeError("合成文本为空（没有可朗读的有效字符）")
        persona = self.store.persona(voice_id)
        if persona is None:
            raise VoiceNotReadyError(f"未知音色 {voice_id}：registry.json 里没有这个 persona-id")

        # 1) 拿（或自动注册）云端音色 ID
        cloud_vid = str(persona.get(self.VOICE_FIELD) or "").strip() \
            or await self.ensure_voice_id(voice_id)
        # 合成模型必须与注册时一致：优先用 persona 簿记的模型（换过 target_model 也能合成）
        persona = self.store.persona(voice_id) or persona  # 注册写回后热重载
        model = str(persona.get("cosyvoice_voice_model") or self.target_model).strip()

        # 2) 语速：入参 speed × persona 的 speed_scale（默认 1.12，与 gpt_sovits 一致）
        try:
            scale = float((persona or {}).get("speed_scale", SPEED_SCALE_DEFAULT))
        except (TypeError, ValueError):
            scale = SPEED_SCALE_DEFAULT
        speech_rate = round(min(max(_f(speed, 1.0) * scale, SPEED_MIN), SPEED_MAX), 3)

        # 3) 合成（失败若是「音色在云端已失效」→ 清簿记重注册一次，自愈 1 年未用被清理的音色）
        try:
            raw = await self._synth(text, cloud_vid, model, speech_rate)
        except RuntimeError as e:
            if not self._voice_gone(e):
                raise
            print(f"[cosyvoice] 音色 {cloud_vid} 在云端已失效，自动重新注册——{e}", flush=True)
            await self._save_persona_fields(voice_id, {self.VOICE_FIELD: None})
            cloud_vid = await self.ensure_voice_id(voice_id)
            persona = self.store.persona(voice_id) or persona
            model = str(persona.get("cosyvoice_voice_model") or self.target_model).strip()
            raw = await self._synth(text, cloud_vid, model, speech_rate)

        # 4) 落盘 mp3（引擎请求的就是 mp3；万一拿到 RIFF 头用 ffmpeg 兜底转码）
        raw = await self._normalize_mp3(raw)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        mp3 = self.tmp_dir / f"cosyvoice_{uuid.uuid4().hex[:12]}.mp3"
        mp3.write_bytes(raw)
        return mp3

    async def _synth(self, text: str, vid: str, model: str, speech_rate: float) -> bytes:
        """dashscope WebSocket 合成（阻塞 SDK → 线程里跑，不卡事件循环）。

        RequestFailure 携带 name/message/http_code；连不上/握手超时抛
        ConnectionError/TimeoutError。全部翻译成带中文排查提示的 RuntimeError，
        鉴权类错误同时把 _auth_failed 置位（is_up → False）。"""
        if dashscope is None or SpeechSynthesizer is None:
            raise EngineUnavailableError("dashscope SDK 未安装（pip install dashscope）")

        def _run() -> bytes:
            dashscope.api_key = self.api_key  # 线程内再设一次，防多引擎实例互相覆盖全局
            synth = SpeechSynthesizer(
                model=model,
                voice=vid,
                format=AudioFormat.MP3_22050HZ_MONO_256KBPS,  # 官方默认格式，全系模型可用
                speech_rate=speech_rate,
                language_hints=["en"] if model != "cosyvoice-v1" else None,  # 应用恒为英语输出
            )
            return synth.call(text, timeout_millis=SYNTH_TIMEOUT_MS)

        try:
            raw = await asyncio.to_thread(_run)
        except Exception as e:
            raise RuntimeError(self._fmt_synth_error(e)) from e
        self._auth_failed = False  # 成功调用即洗掉历史鉴权错误标记
        if not raw:
            raise RuntimeError(f"CosyVoice 合成未返回音频（voice={vid}, model={model}）")
        return raw

    @staticmethod
    def _fmt_synth_error(e: Exception) -> str:
        """把 dashscope 抛的异常翻译成中文可读错误（含鉴权/欠费/限流排查提示）。"""
        name = getattr(e, "name", None) or ""
        msg = str(getattr(e, "message", None) or e)
        http_code = getattr(e, "http_code", None)
        hint = _CODE_CN.get(name, "")
        base = f"CosyVoice 合成失败（{type(e).__name__}"
        if http_code:
            base += f" HTTP {http_code}"
        if name:
            base += f" code={name}"
        base += f"）：{msg[:300]}"
        if hint:
            base += f"（{hint}）"
        if name == "InvalidApiKey" or "apikey" in msg.lower():
            return base + "——请核对 aliyun_dashscope_key（百炼北京地域）"
        return base

    @staticmethod
    def _voice_gone(e: Exception) -> bool:
        """判断合成报错是否为『复刻音色在云端已不存在』（1 年未用被自动清理等）→ 触发重注册。"""
        m = str(e).lower()
        if "voice" not in m and "音色" not in m:
            return False
        return any(k in m for k in ("not exist", "not found", "no such", "expired",
                                    "deleted", "不存在", "已删除", "已过期", "invalid voice"))

    # ------------------------------------------------------------------ 音色注册

    async def ensure_voice_id(self, pid: str) -> str:
        """返回该 persona 的云端音色 ID；没有则用参考音频注册并写回 registry。

        全局锁内二次确认，避免并发重复建音色（免费额度只有 1000 次，重复注册纯浪费）。
        簿记字段（写在 persona 上，VoiceStore 按 mtime 热重载）：
          cosyvoice_voice_id        云端音色 ID（{target_model}-{prefix}-{唯一标识}）
          cosyvoice_voice_model     注册时绑定的合成模型（合成必须同模型，换配置不误伤旧音色）
          cosyvoice_voice_status    ready | deploying | failed
          cosyvoice_voice_created_at ISO 时间戳
        """
        async with self._enroll_lock:
            vid = str((self.store.persona(pid) or {}).get(self.VOICE_FIELD) or "").strip()
            if vid:
                return vid
            print(f"[cosyvoice] 音色 {pid} 还没有云端复刻音色，开始注册（上传参考音频 → OSS → "
                  f"建音色 → 审核等待，全程可能 10~60 秒，每音色只发生一次）…", flush=True)
            t0 = time.monotonic()
            vid = await self._enroll(pid)
            dt = time.monotonic() - t0
            print(f"[cosyvoice] 音色 {pid} 注册完成 → {self.VOICE_FIELD}={vid}"
                  f"（耗时 {dt:.0f} 秒，已写回 registry）", flush=True)
            return vid

    async def _enroll(self, pid: str) -> str:
        """注册单个 persona 的复刻音色：备音频 → 传 OSS → 签名直链 → create_voice → 等审核 → 写回。

        供 synthesize 首次调用与 enroll_all.py 批量脚本共用；失败时 registry 不留半截字段。"""
        # 1) 参考音频：best_clip 锚点 + 必要时拼接（见 _prepare_ref_audio 的选片策略）
        wav, is_temp, note = await self._prepare_ref_audio(pid)
        try:
            # 2) 上传私有桶并生成 24 小时带签名直链（复刻接口只收公网 URL）
            key = f"{OSS_KEY_PREFIX}{pid}.wav"
            url = await self._upload_ref_audio(wav, key)
            print(f"[cosyvoice] 参考音频已上传 OSS：{key}（{note}）→ 24 小时签名直链已生成", flush=True)

            # 3) create_voice（REST）；language_hints 用锚点切片的语种提示复刻（v3.5/v3 系列支持）
            best = self.store.best_clip(pid) or {}
            lang = str(best.get("prompt_lang") or "").lower()[:2]
            payload = {"model": "voice-enrollment",
                       "input": {"action": "create_voice",
                                 "target_model": self.target_model,
                                 "prefix": _sanitize_prefix(pid),
                                 "url": url}}
            # v3.5/v3 系列专属参数（v2/v1 不支持，传了会报错；见模块 docstring 的支持集）
            if self.target_model in _HINTS_LANG_MODELS and lang in (
                    "zh", "en", "fr", "de", "ja", "ko", "ru", "pt", "th", "id", "vi"):
                payload["input"]["language_hints"] = [lang]
            if self.target_model in _PREPROCESS_MODELS:
                payload["input"]["enable_preprocess"] = self.preprocess
                payload["input"]["max_prompt_audio_length"] = MAX_PROMPT_AUDIO_LENGTH
            r = await self._rest_post(ENROLL_URL, payload, "创建音色(create_voice)",
                                      timeout=httpx.Timeout(120.0, connect=10.0))
            body = self._rest_json(r, "创建音色(create_voice)")
            if r.status_code != 200:
                raise RuntimeError(self._rest_error(r, body))
            vid = str(((body.get("output") or {}).get("voice_id")) or "").strip()
            if not vid:
                raise RuntimeError(f"创建音色响应里没有 output.voice_id：{body}")
            print(f"[cosyvoice] create_voice 成功：voice_id={vid}"
                  f"（target_model={self.target_model}，request_id={body.get('request_id', '-')}）"
                  "，等待平台审核…", flush=True)

            # 4) 等审核（DEPLOYING → OK；一般几秒到几分钟）
            status = await self._wait_voice_ok(vid)
            fields = {self.VOICE_FIELD: vid,
                      "cosyvoice_voice_model": self.target_model,
                      "cosyvoice_voice_created_at": datetime.now().isoformat(timespec="seconds")}
            if status == "UNDEPLOYED":
                fields["cosyvoice_voice_status"] = "failed"
                await self._save_persona_fields(pid, fields)
                raise RuntimeError(
                    f"音色 {vid} 审核未通过（UNDEPLOYED）：常见原因是参考音频含背景音乐/噪音/"
                    f"多人声或敏感内容。请给 {pid} 换更清晰的切片（10~20 秒单人干声），"
                    f"删除 registry 里该 persona 的 {self.VOICE_FIELD} 后重试")
            if status == "OK":
                fields["cosyvoice_voice_status"] = "ready"
            else:  # 超时仍 DEPLOYING / 查询失败：先写回，放行试合成（服务端最终裁决）
                fields["cosyvoice_voice_status"] = "deploying"
                print(f"[cosyvoice] 音色 {vid} 仍在审核中（{ENROLL_WAIT_TIMEOUT:.0f} 秒内未转 OK），"
                      "已写入 registry 并直接尝试合成——若合成报音色不可用，稍等片刻再点", flush=True)
            await self._save_persona_fields(pid, fields)
            return vid
        finally:
            if is_temp:
                wav.unlink(missing_ok=True)  # 清掉 ffmpeg 拼出来的临时参考音频

    async def _wait_voice_ok(self, vid: str) -> str:
        """轮询 query_voice 直到 OK / UNDEPLOYED（终态）或超时。

        查询接口本身异常（网络/鉴权抖动）不中断流程：打印警告继续轮询，
        超时后返回最后状态（或 "DEPLOYING"）交由调用方放行试合成。"""
        t0 = time.monotonic()
        status = "DEPLOYING"
        while True:
            try:
                r = await self._rest_post(
                    ENROLL_URL,
                    {"model": "voice-enrollment", "input": {"action": "query_voice",
                                                            "voice_id": vid}},
                    "查询音色(query_voice)", timeout=httpx.Timeout(30.0, connect=10.0))
                body = self._rest_json(r, "查询音色(query_voice)")
                if r.status_code == 200:
                    self._auth_failed = False
                    st = str((body.get("output") or {}).get("status") or "").strip().upper()
                    if st in ("OK", "UNDEPLOYED", "DEPLOYING"):
                        status = st
                    if st in ("OK", "UNDEPLOYED"):
                        return st
                else:
                    print(f"[cosyvoice] query_voice 失败（忽略，稍后重查）："
                          f"{self._rest_error(r, body)}", flush=True)
            except RuntimeError as e:
                print(f"[cosyvoice] query_voice 异常（忽略，稍后重查）：{e}", flush=True)
            if time.monotonic() - t0 >= ENROLL_WAIT_TIMEOUT:
                return status
            await asyncio.sleep(ENROLL_POLL_INTERVAL)

    # ------------------------------------------------------------------ 参考音频准备

    async def _prepare_ref_audio(self, pid: str):
        """挑参考音频产出上传文件。返回 (wav 路径, 是否临时文件, 说明文本)。

        选片策略（CosyVoice 官方推荐 10~20 秒；我们的切片单条 3~10 秒常不够）：
        - 锚点恒为 store.best_clip（score 最高片段，冻结接口）；
        - ffmpeg 可用：从锚点开始续拼【同语种】切片（_clip_rank 排序）到 ~12 秒
          （上限 20 秒）——宁短勿混语种（复刻要求音频语种一致）；
        - ffmpeg 不可用 / 无可拼片段：直接上传锚点原文件（16k 单声道 wav，服务端裁决）；
        - 拼接统一转 16k 单声道 16bit（≥16kHz 达标，与切片原生格式一致）。
        """
        clip = self.store.best_clip(pid)
        if clip is None:
            raise VoiceNotReadyError(
                f"音色 {pid} 没有可用的参考音频（registry/meta 里没有 clips，或文件不存在），"
                f"无法注册 CosyVoice 声音复刻")
        best_dur = _f(clip.get("dur"))
        if best_dur and best_dur < REF_MIN_SECONDS:
            print(f"[cosyvoice] [warn] {pid} 最佳切片仅 {best_dur:.1f} 秒（CosyVoice 要求"
                  f"至少 {REF_MIN_SECONDS:.0f} 秒连续清晰人声，推荐 10~20 秒），"
                  "注册可能被拒——将尽量拼接补足", flush=True)

        src_dir = self.voices_dir / pid
        best_path = src_dir / clip["file"]
        # 候选：registry 与 meta 的 clips 合并去重（与 volc/_prepare_prompt 同一套）
        merged = {}
        for src in ((self.store.persona(pid) or {}).get("clips") or [],
                    self.store.meta(pid).get("clips") or []):
            for c in src:
                f = c.get("file")
                if f and (f not in merged or _clip_rank(c) > _clip_rank(merged[f])):
                    merged[f] = c
        clips = [c for c in sorted(merged.values(), key=_clip_rank, reverse=True)
                 if (src_dir / str(c.get("file") or "")).is_file()]
        # 只拼与锚点同语种的切片（en 优先级由 best_clip 决定；语种混拼有训练失败风险）
        anchor_lang = str(clip.get("prompt_lang") or "").lower()[:2]
        same_lang = [c for c in clips
                     if str(c.get("prompt_lang") or "").lower()[:2] == anchor_lang]

        picked, total = [clip], best_dur
        for c in same_lang:
            if total >= REF_TARGET_SECONDS or len(picked) >= 6:
                break
            if c.get("file") == clip.get("file"):  # 锚点已入列，按文件名判重（不依赖对象同一性）
                continue
            d = _f(c.get("dur"))
            if d < 0.8 or total + d > REF_MAX_SECONDS:
                continue
            picked.append(c)
            total += d

        if len(picked) == 1:
            # 单片（已是 16k 单声道 wav，无需动 ffmpeg）
            return best_path, False, f"单片 {clip['file']} {best_dur:.1f}s"

        try:
            ff = find_ffmpeg()
        except Exception:
            print(f"[cosyvoice] ffmpeg 不可用，退回单片上传（拼接 10~20 秒需 ffmpeg）", flush=True)
            return best_path, False, f"单片 {clip['file']} {best_dur:.1f}s（无 ffmpeg 未拼接）"

        # ffmpeg 拼接：归一 16k 单声道再 concat（输入采样率不一致也能拼）
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        out = self.tmp_dir / f"cosyvoice_ref_{_sanitize_prefix(pid)}_{uuid.uuid4().hex[:6]}.wav"
        inputs = []
        for c in picked:
            inputs += ["-i", str(src_dir / c["file"])]
        parts = "".join(
            f"[{i}:a]aformat=sample_rates=16000:channel_layouts=mono[a{i}];"
            for i in range(len(picked)))
        labels = "".join(f"[a{i}]" for i in range(len(picked)))
        cmd = [ff, "-y", "-loglevel", "error", *inputs,
               "-filter_complex", f"{parts}{labels}concat=n={len(picked)}:v=0:a=1[out]",
               "-map", "[out]", "-t", str(int(REF_MAX_SECONDS)),
               "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(out)]
        try:
            proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True)
        except Exception as e:
            print(f"[cosyvoice] 参考音频拼接异常，改用单片：{e}", flush=True)
            return best_path, False, f"单片 {clip['file']} {best_dur:.1f}s（拼接异常）"
        if proc.returncode != 0 or not out.is_file():
            print(f"[cosyvoice] 参考音频拼接失败，改用单片："
                  f"{proc.stderr.decode('utf-8', 'replace')[-200:]}", flush=True)
            return best_path, False, f"单片 {clip['file']} {best_dur:.1f}s（拼接失败）"
        return out, True, f"{len(picked)} 片拼接 {total:.1f}s（语种 {anchor_lang or '未知'}）"

    # ------------------------------------------------------------------ OSS 上传

    def _oss_bucket(self):
        """懒建 oss2.Bucket（阻塞 SDK；bucket 操作全部走 asyncio.to_thread）。

        endpoint 没写 scheme 时补 https://（oss2 对裸 endpoint 默认走 http，
        签名直链也会变成 http——统一走 https 更安全）。"""
        if self._bucket is None:
            if oss2 is None:
                raise EngineUnavailableError("oss2 库未安装（pip install oss2）")
            endpoint = self.oss_endpoint
            if not endpoint.startswith(("http://", "https://")):
                endpoint = "https://" + endpoint
            self._bucket = oss2.Bucket(oss2.Auth(self.oss_ak, self.oss_sk),
                                       endpoint, self.oss_bucket_name,
                                       connect_timeout=OSS_CONNECT_TIMEOUT)
        return self._bucket

    def _oss_put_sync(self, path: Path, key: str):
        """上传参考音频到私有桶（覆盖写，重复注册幂等）并返回 24 小时签名直链。"""
        bucket = self._oss_bucket()
        data = path.read_bytes()
        if len(data) > 10 * 1024 * 1024:
            raise RuntimeError(
                f"参考音频 {path.name} 有 {len(data) / 1048576:.1f}MB，超过 CosyVoice 10MB 上限")
        bucket.put_object(key, data, headers={"Content-Type": "audio/wav"})
        url = bucket.sign_url("GET", key, OSS_URL_EXPIRES)
        return url

    async def _upload_ref_audio(self, wav: Path, key: str) -> str:
        """上传 + 签名（阻塞 IO 放线程）；OSS 错误翻译成中文排查提示。"""
        try:
            url = await asyncio.to_thread(self._oss_put_sync, wav, key)
        except Exception as e:
            raise RuntimeError(self._fmt_oss_error(e, key)) from e
        return url

    @staticmethod
    def _fmt_oss_error(e: Exception, key: str) -> str:
        """把 oss2 异常翻译成中文可读错误（桶名/Endpoint/AK-SK/权限四类高频坑）。"""
        status = getattr(e, "status", None)
        code = str(getattr(e, "code", "") or "")
        details = str(getattr(e, "details", "") or str(e))
        if code == "NoSuchBucket" or status == 404:
            return (f"OSS 桶不存在（HTTP {status}）：请核对 config.json 的 aliyun_oss_bucket"
                    f"（要传 {key}）与 aliyun_oss_endpoint 是否为同一地域（北京 = "
                    f"oss-cn-beijing.aliyuncs.com）")
        if code in ("InvalidAccessKeyId", "SignatureDoesNotMatch") or status == 403:
            return (f"OSS 鉴权失败（HTTP {status} {code}）：请核对 aliyun_oss_ak / aliyun_oss_sk"
                    f"（RAM 控制台创建的 AccessKey ID/Secret，Secret 只显示一次）；"
                    f"若用 RAM 子账号需授予该桶的读写权限。{details[:200]}")
        if "timed out" in str(e).lower() or "timeout" in str(e).lower():
            return (f"OSS 连接超时：请检查 aliyun_oss_endpoint（外网访问 Endpoint，形如 "
                    f"oss-cn-beijing.aliyuncs.com）与网络（{type(e).__name__}: {e}）")
        if "Name or service not known" in str(e) or "nodename nor servname" in str(e):
            return (f"OSS 域名解析失败：aliyun_oss_endpoint 大概率填错"
                    f"（当前值无法解析。{e}）")
        return f"OSS 上传失败（{type(e).__name__}: {str(e)[:300]}）"

    # ------------------------------------------------------------------ REST 层

    async def _rest_post(self, url: str, payload: dict, what: str,
                         timeout=httpx.USE_CLIENT_DEFAULT) -> httpx.Response:
        """复刻/查询接口统一 POST（Bearer 鉴权头在 startup 已挂在 client 上）。"""
        if not self.client:
            raise EngineUnavailableError("CosyVoice 引擎未初始化（startup 未调用）")
        try:
            return await self.client.post(url, json=payload, timeout=timeout)
        except httpx.HTTPError as e:
            raise RuntimeError(
                f"百炼{what}网络异常（{type(e).__name__}）：请检查网络后重试"
                f"（入口 {DASHSCOPE_BASE}）") from e

    @staticmethod
    def _rest_json(r: httpx.Response, what: str) -> dict:
        try:
            return r.json()
        except Exception:
            raise RuntimeError(f"{what}返回非 JSON（HTTP {r.status_code}）：{r.text[:200]}") from None

    def _rest_error(self, r: httpx.Response, body: dict) -> str:
        """REST 业务错误 → 中文消息；鉴权类同时置 _auth_failed（is_up → False）。"""
        code = str(body.get("code") or "")
        if r.status_code in (401, 403) or code.startswith("InvalidApiKey"):
            self._auth_failed = True
        msg = _fmt_rest_error(r)
        if self._auth_failed:
            return (f"{msg}——百炼侧鉴权失败，请核对 aliyun_dashscope_key"
                    f"（百炼控制台华北 2-北京地域创建的 sk- 开头 Key）")
        return msg

    # ------------------------------------------------------------------ 工具

    async def _normalize_mp3(self, raw: bytes) -> bytes:
        """引擎请求的就是 mp3；万一拿到 wav 头（RIFF）就用 ffmpeg 转 mp3（契约兜底）。"""
        if not raw.startswith(b"RIFF"):
            return raw
        wav = self.tmp_dir / f"cosyvoice_{uuid.uuid4().hex[:12]}.wav"
        mp3 = self.tmp_dir / f"cosyvoice_{uuid.uuid4().hex[:12]}.mp3"
        try:
            wav.write_bytes(raw)
            cmd = [find_ffmpeg(), "-y", "-loglevel", "error", "-i", str(wav),
                   "-codec:a", "libmp3lame", "-b:a", "128k", str(mp3)]
            proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True)
            if proc.returncode != 0 or not mp3.is_file():
                raise RuntimeError(
                    f"ffmpeg 转 mp3 失败: {proc.stderr.decode('utf-8', 'replace')[-300:]}")
            return mp3.read_bytes()
        finally:
            wav.unlink(missing_ok=True)
            mp3.unlink(missing_ok=True)

    async def _save_persona_fields(self, pid: str, updates: dict):
        """把音色簿记字段写回 registry.json（值为 None 表示删除该键）。

        写入走 voice_batch.py 的跨进程写保护协议（同一把 registry.lock）：锁内重读
        磁盘态、只改目标 persona 的字段、原子替换整文件，避免与爬虫/批量转写的
        长跑写者互相抹条目。文件锁与磁盘 IO 都是阻塞操作 → 放线程跑，不卡事件循环。"""
        await asyncio.to_thread(self._save_persona_fields_sync, pid, updates)

    def _save_persona_fields_sync(self, pid: str, updates: dict):
        reg_path = self.voices_dir / "registry.json"
        lock_f = _acquire_registry_lock(self.voices_dir / "registry.lock")
        if lock_f is None:
            print("[cosyvoice] [warn] registry.lock 获取失败（或平台无 msvcrt），"
                  "无锁写回 registry.json（存在并发写者时可能互相覆盖）", flush=True)
        try:
            try:
                reg = json.loads(reg_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                reg = {"version": 1, "engine_default": "gpt_sovits", "personas": {}}
            except Exception as e:
                raise RuntimeError(f"registry.json 读取失败，{self.VOICE_FIELD} 无法持久化：{e}") from e
            persons = reg.get("personas")
            if not isinstance(persons, dict):
                persons = {}
                reg["personas"] = persons
            per = persons.get(pid)
            if not isinstance(per, dict):
                per = {}
                persons[pid] = per
            for k, v in updates.items():
                if v is None:
                    per.pop(k, None)
                else:
                    per[k] = v
            # tmp 名带进程号（与 voice_batch 的约定一致），避免与并行写者撞名
            tmp = reg_path.parent / f"{reg_path.name}.{os.getpid()}.tmp"
            tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(reg_path)
        finally:
            _release_registry_lock(lock_f)

    async def _probe_reachability(self):
        """startup 时的尽力探测（不抛错、不影响服务起停）：

        - DashScope 入口连通性（3 秒超时，仅看域名可达，不鉴权）；
        - OSS 桶可达 + AK/SK 有效（head_bucket，阻塞调用放线程）。
        目的是把「Key 填错/桶名填错」这类配置错误在启动日志里直接点出来，
        免得用户第一次点合成才发现；探测失败只打印警告。"""
        try:
            await self.client.get(DASHSCOPE_BASE, timeout=3.0)
            print(f"[engine] 百炼入口 {DASHSCOPE_BASE} 可达", flush=True)
        except Exception as e:
            print(f"[engine] [warn] 百炼入口暂不可达（{type(e).__name__}）："
                  "网络恢复前合成会失败", flush=True)
            return
        if oss2 is None:
            return

        def _info():
            # get_bucket_info：桶不存在/无权限时抛 OssError，正常返回桶元数据
            return self._oss_bucket().get_bucket_info()

        try:
            info = await asyncio.to_thread(_info)
            print(f"[engine] OSS 桶 {self.oss_bucket_name} 可访问（AK/SK 有效，地域 "
                  f"{getattr(info, 'location', '?')}）", flush=True)
        except Exception as e:
            if getattr(e, "status", None) in (401, 403) or \
                    str(getattr(e, "code", "")) in ("InvalidAccessKeyId", "SignatureDoesNotMatch"):
                self._auth_failed = True
            print(f"[engine] [warn] OSS 桶探测失败：{self._fmt_oss_error(e, OSS_KEY_PREFIX + '<pid>.wav')}",
                  flush=True)
