# -*- coding: utf-8 -*-
"""
火山引擎（豆包声音复刻）引擎：openspeech.bytedance.com 云端复刻 + 云端合成，返回 mp3。

接口契约（引擎走 v3 复刻路线；2026-10 对照官方文档核对。旧 V1 合成路已移除——
官方标记『不推荐』，且其 cluster 取值在现行文档中唯一有出处的是 volcano_tts
（标准音色合成），与复刻音色并不对应，按历史值 volcano_icl 实现会直接失败）：
1) 复刻训练提交（V3 voice_clone，现行官方接口，注册出复刻 2.0 音色）：
   POST https://openspeech.bytedance.com/api/v3/tts/voice_clone
   Headers: X-Api-App-Id（或 X-Api-App-Key，见下）+ X-Api-Access-Key + X-Api-Request-Id
   鉴权头名两份官方文档互相矛盾：旧版控制台鉴权参考示例（docs/6561/2534847）用
   X-Api-App-Id，声音复刻 API v3 的 Python 示例（docs/6561/2534906）用 X-Api-App-Key
   ——_v3_post 按每个接口自动双试（先 X-Api-App-Id，鉴权失败换 X-Api-App-Key），
   记住能通的那种，无法联网预实测的环境下两种头名都能落到。
   Body: {"speaker_id": "custom_speaker_id", "custom_speaker_id": <自定义名>,
          "audio": {"data": <base64>, "format": "wav"}, "language": int}
   音频：wav/mp3/ogg/m4a/aac（≤10MB），建议 14~30 秒、24k 单声道、单人低噪
   （不再走 V1 mega_tts 老上传兜底：其 speaker_id 必须是控制台发放的预付费/免费
   音色槽位 ID（官方『下单及使用指南』），程序无法编造——旧实现编造 "S_it_<pid>"
   对真实账号必然失败；预付费槽位请在控制台训练后，把 S_* 音色 ID 手填到 registry
   该 persona 的 volc_voice_id 直接使用）
2) 复刻状态查询（V3）：POST /api/v3/tts/get_voice
   Body: {"speaker_id": "custom_speaker_id", "custom_speaker_id": <自定义名>} → status
   （V1 注册的音色走 /api/v1/mega_tts/audio/status，Body: {"app_id", "speaker_id"}）
   status：0 不存在 / 1 训练中 / 2 成功 / 3 失败 / 4 已激活（2、4 可合成）
3) 合成主路（V3 SSE 单向流式，现行官方接口；voice 参数名为 speaker，无 cluster 参数）：
   POST https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse
   Headers: X-Api-App-Id（或 X-Api-App-Key，自动双试）+ X-Api-Access-Key
            + X-Api-Request-Id + X-Api-Resource-Id
            （seed-icl-2.0 = v3 注册的复刻 2.0 音色；seed-icl-1.0 = V1 注册的 1.0 音色）
   Body: {"user": {"uid"}, "req_params": {"text", "speaker",
          "audio_params": {"format": "mp3", "speech_rate": [-50,100]}}}
   SSE 行 data:{...}：code=0 带音频块（base64），code=20000000 合成结束
   音色不可用（code=45000000/45001107）→ 手填 ID 换另一代际 resource 重试一次；
   本引擎注册的音色（有 volc_voice_api 簿记）直接清本地簿记自愈，下次点击自动重注册

凭据（config.json，冻结键）：volc_appid / volc_token / volc_voice_type。
volc_cluster 为冻结的兼容键：v3 路线不参与任何请求（v3 全家没有 cluster 参数，
历史值 volcano_icl 在现行官方文档查无出处），引擎仅读取保留、不发出去。
音色 ID：registry persona 的 "volc_voice_id"（首次合成自动注册并写回 registry.json，
        附 volc_voice_api = v1|v3、volc_voice_status = training|ready|failed 簿记字段）。
凭据缺失：startup 不崩溃，is_up() = False，synthesize 抛 EngineUnavailableError。
"""
import asyncio
import base64
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx

from . import (DEFAULT_VOICES_DIR, EngineUnavailableError, TTSEngine, VoiceNotReadyError,
               VoiceStore, _clip_rank)
from .gpt_sovits import find_ffmpeg  # 与 gpt_sovits / voice_batch 保持同一份 ffmpeg 解析

VOLC_UNCONFIGURED = ("火山引擎声音复刻未配置：请在 config.json 填写 volc_appid / volc_token"
                     "（火山引擎控制台 → 语音技术 → 大模型语音合成，页面里的『APP ID』和"
                     "『Access Token』）")

_BASE = "https://openspeech.bytedance.com"
URL_STATUS_V1 = _BASE + "/api/v1/mega_tts/audio/status"   # 复刻状态查询（V1，仅历史 v1 簿记音色用）
URL_CLONE_V3 = _BASE + "/api/v3/tts/voice_clone"          # 复刻训练提交（V3，现行，主路）
URL_GETVOICE_V3 = _BASE + "/api/v3/tts/get_voice"         # 复刻状态查询（V3，现行）
URL_TTS_V3 = _BASE + "/api/v3/tts/unidirectional/sse"     # 合成主路（V3 SSE）

# V3 接口的鉴权头名候选（官方两份文档矛盾，按 2534847 优先 X-Api-App-Id，运行时双试）
_AUTH_APP_ID_HDR = "X-Api-App-Id"
_AUTH_APP_KEY_HDR = "X-Api-App-Key"

# 语种 → 复刻接口 language 枚举（cn=0 en=1 ja=2 es=3 id=4 pt=5 de=6 fr=7 ko=8）
_LANG_ENUM = {"zh": 0, "en": 1, "ja": 2, "es": 3, "id": 4, "pt": 5, "de": 6, "fr": 7, "ko": 8}

# 复刻音色训练状态（V3 文档明确，V1 按同一枚举解析）：2/4 可合成
_VOICE_STATUS_CN = {0: "不存在", 1: "训练中", 2: "训练成功", 3: "训练失败", 4: "已激活"}

# V1 接口（mega_tts 注册/状态）业务错误码（官方 V1 返回码说明表节选）
_V1_CODE_CN = {
    3001: "无效的请求（参数值非法）",
    3003: "并发超限（试用并发额度用尽，稍后重试或开通正式版）",
    3005: "后端服务忙，请稍后重试",
    3006: "相同 reqid 重复请求（服务中断）",
    3010: "文本长度超限（本引擎已自动分段）",
    3011: "无效文本（空文本 / 纯标点 / 语种与音色不匹配）",
    3030: "处理超时，请重试",
    3031: "后端处理错误，请重试",
    3032: "等待音频超时，请重试",
    3040: "后端链路连接错误，请重试",
    3050: "音色不存在（音色未注册或已过期删除）",
}

# V3 复刻接口常见错误码（官方错误码表节选）
_V3_CODE_CN = {
    45001001: "请求参数有误（字段缺失 / 格式不对 / 不符合约束）",
    45001101: "音频上传失败（网络问题或超时），可重试",
    45001102: "ASR 转写失败，请换更清晰的参考音频",
    45001104: "声纹检测未通过，请更换参考音频",
    45001105: "音频数据解析失败（base64 或格式问题）",
    45001107: "SpeakerID 未找到（音色不存在或已被删除）",
    45001108: "音频转码失败（格式不支持或数据损坏）",
    45001109: "参考文本与音频内容不匹配（WER 检测）",
    45001114: "参考音频质量较差（背景噪声大 / 人声过弱）",
    45001122: "未检测到人声",
    45001123: "音色训练次数已用尽（控制台额度）",
    45001127: "参考音频未通过内容审核",
    45000000: "音色鉴权失败（speaker 未授权或不存在）",
    55000000: "服务端通用错误，可重试",
}


# V3 合成『音色不存在/未授权』类业务码（45001107 = SpeakerID 未找到）：
# 命中即视为音色在服务端已不可用 → 触发换代际重试 / 本地簿记自愈清理
_VOICE_MISSING_CODES = {45000000, 45001107}


class _VoiceMissingError(RuntimeError):
    """V3 合成报『音色不存在或不可用』→ 换另一代际 resource 重试。"""


def _f(x, default=0.0):
    """容错的 float()。"""
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _sanitize_id(pid: str) -> str:
    """persona-id → 云端音色代号可用的字符集（[0-9a-zA-Z_]，去首尾下划线）。"""
    s = re.sub(r"[^0-9a-zA-Z_]", "_", pid or "").strip("_")
    s = re.sub(r"_+", "_", s)
    return s[:60] or "voice"


def _split_text(text: str, max_bytes: int = 400) -> list:
    """按句切分（官方建议单次 <300 字符，留足余量；V3 单次上限未在现行文档确认，
    保守沿用 400 字节切分，多段由 ffmpeg 无损拼接）。
    先按句末标点打包成段，单句仍超长时按空格硬切；返回非空段落列表。"""
    text = (text or "").strip()
    if not text:
        return []

    def blen(s):
        return len(s.encode("utf-8"))

    sents = []
    for para in re.split(r"\n+", text):
        para = para.strip()
        if not para:
            continue
        buf = ""
        for p in re.split(r"(?<=[.!?])\s+", para):
            p = p.strip()
            if not p:
                continue
            if buf and blen(buf) + blen(p) + 1 > max_bytes:
                sents.append(buf)
                buf = p
            else:
                buf = (buf + " " + p) if buf else p
        if buf:
            sents.append(buf)
    out = []
    for s in sents:
        if blen(s) <= max_bytes:
            out.append(s)
            continue
        buf = ""
        for w in s.split(" "):
            if blen(w) > max_bytes:  # 单个超长词（无空格串）→ 按字符硬切
                if buf:
                    out.append(buf)
                    buf = ""
                for i in range(0, len(w), max_bytes):
                    out.append(w[i:i + max_bytes])
                continue
            if buf and blen(buf) + blen(w) + 1 > max_bytes:
                out.append(buf)
                buf = w
            else:
                buf = (buf + " " + w) if buf else w
        if buf:
            out.append(buf)
    return out


def _fmt_api_error(http_status, code, msg) -> str:
    """把火山返回的 (HTTP 状态, 业务码, message) 拼成一句中文可读错误。"""
    msg = str(msg or "")
    code = code if code is not None else ""
    hint = _V1_CODE_CN.get(code) or _V3_CODE_CN.get(code) or ""
    low = msg.lower()
    if http_status in (401, 403) or "authenticate" in low or "requested grant" in low:
        hint = ("鉴权失败：请核对 config.json 的 volc_appid / volc_token"
                "（v3 请求头由本引擎自动组装，appid 头名两种都会自动尝试）")
    elif "quota exceeded" in low:
        hint = "用量或并发超限：试用额度用完 / 并发超限，需开通正式版或稍后重试"
    elif "init engine instance" in low:
        hint = ("音色与接口参数不匹配：确认音色已注册且代际正确"
                "（V1 注册音色配 seed-icl-1.0，V3 注册配 seed-icl-2.0）")
    if hint:
        return f"HTTP {http_status} code={code} {msg}（{hint}）"
    return f"HTTP {http_status} code={code} {msg}"


def _auth_failed(r: httpx.Response) -> bool:
    """响应是否属于鉴权类失败（用于触发 X-Api-App-Id → X-Api-App-Key 换头重试）。"""
    if r.status_code in (401, 403):
        return True
    try:
        j = r.json()
    except Exception:
        return False
    msg = str(j.get("message") or j.get("status_msg")
              or (j.get("base_resp") or {}).get("status_msg") or "")
    low = msg.lower()
    return ("authenticate" in low or "requested grant" in low or "unauthorized" in low)


class VolcEngine(TTSEngine):
    """火山引擎声音复刻引擎（name=volc）。

    与 gpt_sovits 同一抽象：synthesize 返回 cache/tmp 下的临时 mp3，
    调用方（tts_server）负责移到最终位置；云端引擎无需本地后端，
    is_up() 只看凭据是否配好（探测要快）。
    """
    name = "volc"

    def __init__(self, config: dict):
        self.appid = str(config.get("volc_appid") or "").strip()
        self.token = str(config.get("volc_token") or "").strip()
        # 冻结的兼容键：v3 路线不参与任何请求（v3 无 cluster 参数），仅读取保留
        self.cluster = str(config.get("volc_cluster") or "volcano_icl").strip() or "volcano_icl"
        # 全局兜底音色（用户在控制台手动建好的 voice_type；仅在 persona 连参考音频都没有时使用）
        self.default_voice_type = str(config.get("volc_voice_type") or "").strip()
        self.voices_dir = Path(config.get("voices_dir") or DEFAULT_VOICES_DIR)
        cd = Path(config.get("cache_dir") or "cache")
        self.cache_dir = cd if cd.is_absolute() else Path(__file__).resolve().parent.parent / cd
        self.store = config.get("store") or VoiceStore(self.voices_dir)
        self.client = None
        self._reg_lock = asyncio.Lock()  # registry.json 写回串行化
        # 各 v3 接口（按 URL 记）实测可用的鉴权头名：X-Api-App-Id / X-Api-App-Key
        # （官方两份文档矛盾，见模块 docstring；记住后同一接口不再双试）
        self._v3_auth = {}

    # ------------------------------------------------------------------ 生命周期

    @property
    def configured(self) -> bool:
        return bool(self.appid and self.token)

    async def startup(self):
        # 凭据缺失也不崩溃：服务照常起，/healthz 报 down，合成请求才返回 503
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
        tmp = self.cache_dir / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - 86400  # 清掉超过一天的残留临时文件
        for f in tmp.iterdir():
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass
        if self.configured:
            print(f"[engine] 火山引擎声音复刻(volc) —— 已配置 appid={self.appid}"
                  f"（v3 复刻路线：X-Api-App-Id/Access-Key 鉴权 + seed-icl-2.0 合成，"
                  f"云端直连，无需本地服务）", flush=True)
        else:
            print("[engine] 火山引擎声音复刻(volc) —— 未配置 volc_appid/volc_token"
                  "（/healthz 会报 down，合成请求返回 503；请在 config.json 填入火山控制台的 "
                  "APP ID 与 Access Token）", flush=True)

    async def shutdown(self):
        if self.client:
            await self.client.aclose()
        self.client = None

    async def is_up(self) -> bool:
        # 云端引擎：凭据齐 + 客户端在即为 up（鉴权对错要到真正合成才知道，错误会给中文提示）
        return bool(self.client and self.configured)

    def list_ready(self) -> list:
        return [pid for pid, per in sorted((self.store.registry().get("personas") or {}).items())
                if per.get("status") == "ready"]

    # ------------------------------------------------------------------ 合成主流程

    async def synthesize(self, text: str, voice_id: str, speed: float = 1.0) -> Path:
        if not self.client or not self.configured:
            raise EngineUnavailableError(VOLC_UNCONFIGURED)
        persona = self.store.persona(voice_id)
        if persona is None:
            raise VoiceNotReadyError(f"未知音色 {voice_id}：registry.json 里没有这个 persona-id")

        # 1) 拿（或自动注册）云端音色 ID
        voice_type = await self._ensure_voice_type(voice_id, persona)

        # 2) 语速：全局默认 +12%（与 gpt_sovits 一致，2026-10-05 验收：克隆语速偏慢），
        #    registry 里每位爱豆可用 speed_scale 单独覆盖；speech_rate 合法区间 [-50,100]
        try:
            scale = float((persona or {}).get("speed_scale", 1.12))
        except (TypeError, ValueError):
            scale = 1.12
        speed_ratio = round(min(max(_f(speed, 1.0) * scale, 0.5), 2.0), 3)

        # 3) 分段合成（分段防单次文本超长；多段由 ffmpeg 无损拼接）
        segs = _split_text(text)
        if not segs:
            raise RuntimeError("合成文本为空（没有可朗读的有效字符）")
        # 重新取 persona：_ensure_voice_type 可能刚把注册簿记写回 registry（热重载）
        persona = self.store.persona(voice_id) or persona
        # 合成 resource 按注册代际选：v3 注册 = 复刻 2.0 → seed-icl-2.0；V1 注册（历史
        # 簿记）→ seed-icl-1.0；手填的音色 ID（含控制台预付费槽位 S_*）没有簿记，默认按
        # 2.0 试，被拒再换 1.0 重试一次——代际未知才值得多付一次往返，有簿记的代际是
        # 确定的，重试只会给每次点击白加延迟（用户核心诉求：点击低延迟）
        api = str((persona or {}).get("volc_voice_api") or "").strip().lower()
        managed = api in ("v1", "v3")
        resource = "seed-icl-1.0" if api == "v1" else "seed-icl-2.0"
        parts = []
        for seg in segs:
            try:
                part = await self._synth_v3(seg, voice_type, speed_ratio, resource)
            except _VoiceMissingError as e:
                if managed:
                    # 簿记代际被拒 → 音色已被服务端删除/未授权：清簿记自愈，不再重试
                    await self._raise_voice_unavailable(voice_id, persona, voice_type, e)
                alt = "seed-icl-1.0" if resource == "seed-icl-2.0" else "seed-icl-2.0"
                print(f"[volc] V3 合成报音色不可用（resource={resource}），换 {alt} 重试一次：{e}",
                      flush=True)
                try:
                    part = await self._synth_v3(seg, voice_type, speed_ratio, alt)
                except _VoiceMissingError as e2:
                    await self._raise_voice_unavailable(voice_id, persona, voice_type, e2)
            parts.append(await self._normalize_mp3(part))

        # 4) 落盘：单片直存；多片用 ffmpeg concat 无损拼接（失败退化顺序拼接）
        tmp_dir = self.cache_dir / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tag = uuid.uuid4().hex[:12]
        mp3 = tmp_dir / f"volc_{tag}.mp3"
        if len(parts) == 1:
            mp3.write_bytes(parts[0])
        else:
            seg_files = []
            try:
                for i, part in enumerate(parts):
                    f = tmp_dir / f"volc_{tag}_{i:02d}.mp3"
                    f.write_bytes(part)
                    seg_files.append(f)
                try:
                    await self._concat_mp3(seg_files, mp3)
                except Exception:
                    with mp3.open("wb") as fh:  # 兜底：mp3 帧流顺序拼接（绝大多数播放器可解）
                        for part in parts:
                            fh.write(part)
            finally:
                for f in seg_files:
                    f.unlink(missing_ok=True)
        return mp3

    # ------------------------------------------------------------------ 音色注册 / 状态

    async def _raise_voice_unavailable(self, pid: str, persona: dict, voice_type: str,
                                       err: Exception):
        """合成报『音色不存在/未授权』且已无可用代际时的收尾。

        本引擎注册的音色（有 volc_voice_api 簿记）→ 先清掉本地 volc_voice_* 字段再抛错，
        下一次点击自动重新注册（自愈；火山对注册后 7 天未合成的音色会自动删除）；
        手填的音色 ID（控制台槽位等）没有簿记 → 不动本地数据，只抛错（避免误删）。"""
        if str((persona or {}).get("volc_voice_api") or "").strip().lower() in ("v1", "v3"):
            await self._save_persona(pid, {"volc_voice_id": None, "volc_voice_api": None,
                                           "volc_voice_status": None})
            print(f"[volc] 音色 {voice_type} 已被服务端删除或未授权，本地 volc_voice_id 已清除"
                  f"（下次合成将自动重新注册）", flush=True)
            raise RuntimeError(
                f"火山音色 {voice_type} 不可用（可能已被服务端删除或未授权）。本地 volc_voice_id "
                f"已清除，请再发起一次合成以自动重新注册。原始报错：{err}") from err
        raise RuntimeError(
            f"火山音色 {voice_type} 不可用（seed-icl-1.0/2.0 均被拒，可能已被服务端删除或未"
            f"授权；该 ID 为手工填写，本地未清除，请核对控制台音色详情）。原始报错：{err}") from err

    async def _ensure_voice_type(self, pid: str, persona: dict) -> str:
        """返回该 persona 的云端音色 ID；没有则用最佳参考音频注册并写回 registry。

        簿记字段（写在 persona 上，VoiceStore 按 mtime 热重载）：
          volc_voice_id     云端音色代号（V1: S_it_*；V3: custom_it_*）
          volc_voice_api    注册路径 "v1" | "v3"（决定状态查询接口与合成 resource 代际）
          volc_voice_status training | ready | failed
        """
        vid = str((persona or {}).get("volc_voice_id") or "").strip()
        if not vid:
            # 有参考音频 → 自动注册；一条都没有 → 用 config 全局 volc_voice_type 兜底
            clip = self.store.best_clip(pid)
            if not clip:
                if self.default_voice_type:
                    print(f"[volc] 音色 {pid} 无参考音频，退回 config 全局 volc_voice_type="
                          f"{self.default_voice_type}（注意：这不是该爱豆本人的复刻音色）", flush=True)
                    return self.default_voice_type
                raise VoiceNotReadyError(
                    f"音色 {pid} 没有云端 volc_voice_id，也没有可用参考音频（registry/meta 里"
                    f"没有 clips，或文件不存在），无法注册火山声音复刻")
            vid, api = await self._register_persona_voice(pid)
            await self._save_persona(pid, {"volc_voice_id": vid, "volc_voice_api": api,
                                           "volc_voice_status": "training",
                                           "volc_voice_created_at": datetime.now().isoformat(timespec="seconds")})
            persona = self.store.persona(pid) or persona

        managed = bool((persona or {}).get("volc_voice_api"))  # 是否本引擎注册（决定能否自动清）
        status = (persona or {}).get("volc_voice_status")
        # 手填的音色 ID（没有 api 簿记）默认视为可用，直接合成让接口裁决
        if status == "ready" or (not managed and not status):
            return vid

        api = str((persona or {}).get("volc_voice_api") or "v1").strip().lower()
        st = await self._wait_voice_ready(vid, api)
        if st in (2, 4):
            await self._save_persona(pid, {"volc_voice_status": "ready"})
            return vid
        if st == 3:
            await self._save_persona(pid, {"volc_voice_status": "failed"})
            raise RuntimeError(
                f"火山声音复刻训练失败（speaker={vid}）：请换更清晰（10~30 秒、单人、低噪、"
                f"情绪平稳）的参考音频，删除 registry 中该 persona 的 volc_voice_* 字段后重试")
        if st == 0 and managed:
            # 服务端查无此音色（注册未生效 / 过期被自动删除）→ 清掉本地 ID，本次报错，
            # 下一次点击会自动重新注册（火山对注册后 7 天未合成的音色会自动删除）
            await self._save_persona(pid, {"volc_voice_id": None, "volc_voice_api": None,
                                     "volc_voice_status": None})
            raise RuntimeError(
                f"火山侧不存在音色 {vid}（复刻注册未生效或已过期删除）：本地 volc_voice_id "
                f"已清除，请再发起一次合成以自动重新注册")
        if st == 1:
            raise RuntimeError(
                f"火山声音复刻训练中（speaker={vid}，通常几十秒到几分钟）：音色 ID 已存入 "
                f"registry，请稍等片刻后重发本句即可，无需重新上传训练音频")
        # 状态未知（查询接口异常/字段对不上）→ 直接试合成，让合成接口给最终裁决
        return vid

    async def _wait_voice_ready(self, speaker: str, api: str, timeout: float = 45.0):
        """轮询复刻训练状态（每 5 秒一次，最多 timeout 秒）。
        返回最后状态码（0=不存在 2/4=可合成，3=失败）；ICL 复刻通常几秒内转 2。
        status=0（服务端明确『查无此音色』）按终态处理：至多复查一次（防注册刚提交
        的传播延迟）即返回，让调用方的清理分支立刻执行——否则幽灵音色场景下
        首点点击会被拖满整个 timeout 才报错（用户核心诉求：点击低延迟）。"""
        t0 = time.monotonic()
        st = None
        zero_seen = 0
        while True:
            st = await self._query_voice_status(speaker, api)
            if st in (2, 3, 4):
                return st
            if st == 0:
                zero_seen += 1
                if zero_seen >= 2:  # 复查一次仍不存在 → 终态返回
                    return st
            if time.monotonic() - t0 >= timeout:
                return st
            print(f"[volc] 复刻训练中 speaker={speaker} 状态={_VOICE_STATUS_CN.get(st, st)}，"
                  f"5 秒后重查", flush=True)
            await asyncio.sleep(5)

    async def _register_persona_voice(self, pid: str):
        """上传参考音频注册云端音色：V3 voice_clone（现行官方接口，注册出复刻 2.0 音色；
        鉴权头名矛盾由 _v3_post 自动双试）。返回 (speaker_id, api)。

        V1 mega_tts 老上传不作为自动注册路：其 speaker_id 必须是控制台发放的预付费/
        免费音色槽位 ID（官方『下单及使用指南』），程序无法编造（旧实现编造
        "S_it_<pid>" 对真实账号必然注册失败）。预付费槽位用户请在控制台训练后把
        S_* 音色 ID 手填到 registry 该 persona 的 volc_voice_id 直接使用。"""
        wav, lang, is_temp = await self._prepare_prompt(pid)
        try:
            b64 = base64.b64encode(wav.read_bytes()).decode("ascii")
            speaker3 = "custom_it_" + _sanitize_id(pid)
            err = ""
            try:
                r = await self._v3_post(
                    URL_CLONE_V3,
                    {"speaker_id": "custom_speaker_id",
                     "custom_speaker_id": speaker3,
                     "audio": {"data": b64, "format": "wav"},
                     "language": lang},
                    "复刻训练提交(V3)", timeout=120.0)
                j = self._json(r, "复刻训练提交(V3)")
                if r.status_code == 200 and j.get("status") in (1, 2, 4):
                    print(f"[volc] 复刻已提交(V3 voice_clone) speaker={speaker3} language={lang}", flush=True)
                    return speaker3, "v3"
                err = _fmt_api_error(r.status_code, j.get("code"), j.get("message"))
            except Exception as e:
                # 含 _json 对非 JSON 响应（网关 502/504 HTML、空 body）抛的 RuntimeError
                # 与网络异常——统一并入错误信息，不向上逃逸中断流程
                err = f"{type(e).__name__}: {e}"
            raise RuntimeError(
                f"火山声音复刻注册失败（{pid}）：{err}\n"
                f"排查：volc_appid/volc_token 是否正确；『大模型语音合成 / 声音复刻』是否开通并"
                f"有余量（后付费支持自定义音色名 custom_*；预付费/免费槽位需在控制台训练，"
                f"把 S_* 音色 ID 手填到该 persona 的 volc_voice_id）；参考音频需为 10~30 秒"
                f"清晰单人 wav。")
        finally:
            if is_temp:
                wav.unlink(missing_ok=True)

    async def _query_voice_status(self, speaker: str, api: str):
        """查云端音色状态码；查询本身失败返回 None（由调用方决定直接试合成）。"""
        if api == "v3":
            try:
                r = await self._v3_post(
                    URL_GETVOICE_V3,
                    {"speaker_id": "custom_speaker_id", "custom_speaker_id": speaker},
                    "复刻状态查询(V3)", timeout=30.0)
                j = self._json(r, "复刻状态查询(V3)")
                if r.status_code == 200:
                    return j.get("status") if isinstance(j.get("status"), int) else None
                print(f"[volc] 复刻状态查询(V3)失败："
                      f"{_fmt_api_error(r.status_code, j.get('code'), j.get('message'))}", flush=True)
            except Exception as e:
                print(f"[volc] 复刻状态查询(V3)异常：{type(e).__name__}: {e}", flush=True)
            return None
        try:
            r = await self.client.post(
                URL_STATUS_V1,
                headers={"Authorization": f"Bearer;{self.token}",
                         "Resource-Id": "volc_mega_tts",
                         "Content-Type": "application/json"},
                json={"app_id": self.appid, "speaker_id": speaker},
                timeout=30.0)
            j = self._json(r, "复刻状态查询(V1)")
            br = j.get("base_resp") or {}
            if r.status_code == 200 and br.get("status_code") == 0:
                d = j.get("data") or {}
                st = d.get("status_code", d.get("status"))
                return int(st) if isinstance(st, (int, float)) else None
            print(f"[volc] 复刻状态查询(V1)失败："
                  f"{_fmt_api_error(r.status_code, br.get('status_code'), br.get('status_msg'))}",
                  flush=True)
        except Exception as e:
            print(f"[volc] 复刻状态查询(V1)异常：{type(e).__name__}: {e}", flush=True)
        return None

    # ------------------------------------------------------------------ 参考音频准备

    async def _prepare_prompt(self, pid: str):
        """挑参考音频产出复刻上传用的 wav。返回 (wav路径, language枚举, 是否临时文件) 或 None。

        - 候选 = registry 与 meta 的 clips 合并去重（沿用 VoiceStore 的 _clip_rank 排序）；
          本应用是英语陪练，优先 prompt_lang=en 的切片（贴合英语输出场景）；
        - 转写文本互为包含的重复切片只留最长一条（同一抖音视频多镜像场景）；
        - ffmpeg 可用：合并到 14~28 秒并统一转 24k 单声道（官方最佳实践，贴合模型 prompt）；
          ffmpeg 不可用：退回单片原始文件（接口也接受，效果略降）。
        """
        merged = {}
        for src in ((self.store.persona(pid) or {}).get("clips") or [],
                    self.store.meta(pid).get("clips") or []):
            for c in src:
                f = c.get("file")
                if f and (f not in merged or _clip_rank(c) > _clip_rank(merged[f])):
                    merged[f] = c
        clips = [c for c in merged.values()
                 if (self.voices_dir / pid / str(c.get("file") or "")).is_file()]
        if not clips:
            return None
        clips.sort(key=_clip_rank, reverse=True)
        # 稳定二次排序：英语参考优先（输出恒为英语，参考语种与输出语种一致最贴近本音）
        clips.sort(key=lambda c: 0 if str(c.get("prompt_lang") or "").lower().startswith("en") else 1)
        # 同源去重：短片段的转写是长片段转写的子串 → 丢短留长
        texts = [str(c.get("prompt_text") or "").strip().lower() for c in clips]
        keep = []
        for i, c in enumerate(clips):
            t = texts[i]
            if t and any(t != texts[j] and t in texts[j] for j in range(len(texts))):
                continue
            keep.append(c)
        clips = keep or clips

        best = clips[0]
        lang = _LANG_ENUM.get(str(best.get("prompt_lang") or "").lower()[:2], 1)
        src_dir = self.voices_dir / pid
        try:
            ff = find_ffmpeg()
        except Exception:
            ff = None
        if ff is None:
            return src_dir / best["file"], lang, False

        # 选片：只入与最佳切片同语种的片段（官方复刻 2.0 要求『音频内容需要和语种
        # 一致』，en 片段不足时续入 ko 片段会有训练失败/克隆音质劣化风险——宁短勿混，
        # 同语种不足 15 秒就接受较短总时长或单片）；总量仍不超 28 秒（过长服务端会
        # 截断且可能保留瑕疵段）。language 与实际入选片段语种因此恒一致（都取 best 的）
        best_lang = str(best.get("prompt_lang") or "").lower()[:2]
        same_lang = [c for c in clips
                     if str(c.get("prompt_lang") or "").lower()[:2] == best_lang]
        picked, total = [], 0.0
        for c in same_lang:
            d = _f(c.get("dur"))
            if d < 0.8:
                continue
            if total >= 15.0 or total + d > 28.0:
                break
            picked.append(c)
            total += d
        if not picked:
            picked = [best]
            total = _f(best.get("dur"))

        tmp_dir = self.cache_dir / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        out = tmp_dir / f"volc_prompt_{_sanitize_id(pid)}_{uuid.uuid4().hex[:6]}.wav"
        cmd = [ff, "-y", "-loglevel", "error"]
        for c in picked:
            cmd += ["-i", str(src_dir / c["file"])]
        if len(picked) > 1:
            joined = "".join(f"[{i}:a]" for i in range(len(picked)))
            cmd += ["-filter_complex", f"{joined}concat=n={len(picked)}:v=0:a=1"]
        cmd += ["-t", "28", "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(out)]
        try:
            proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True)
        except Exception as e:
            print(f"[volc] 参考音频合并异常，改用单片：{e}", flush=True)
            return src_dir / best["file"], lang, False
        if proc.returncode != 0 or not out.is_file():
            print(f"[volc] 参考音频合并失败，改用单片："
                  f"{proc.stderr.decode('utf-8', 'replace')[-200:]}", flush=True)
            return src_dir / best["file"], lang, False
        print(f"[volc] 复刻参考音频已备好：{len(picked)} 片共 {total:.1f}s → 24k 单声道 {out.name}",
              flush=True)
        return out, lang, True

    # ------------------------------------------------------------------ 云端合成

    async def _v3_post(self, url: str, payload: dict, what: str,
                       timeout: float = 60.0, resource: str = None) -> httpx.Response:
        """V3 接口统一 POST（处理官方文档的鉴权头名矛盾）。

        2534847（旧版控制台鉴权示例）用 X-Api-App-Id，2534906（声音复刻 v3）的
        Python 示例用 X-Api-App-Key——实现时两种都实测的要求在无凭据环境下落成
        运行时双试：先 X-Api-App-Id，返回鉴权类失败再换 X-Api-App-Key 重发一次；
        得到非鉴权失败的响应即按 URL 记住该头名，同一接口后续请求不再双试。
        resource 非空时附带 X-Api-Resource-Id（合成主路用 seed-icl-1.0/2.0）。"""
        variants = [self._v3_auth[url]] if url in self._v3_auth \
            else [_AUTH_APP_ID_HDR, _AUTH_APP_KEY_HDR]
        r = None
        for i, hdr in enumerate(variants):
            headers = {"X-Api-Access-Key": self.token,
                       "X-Api-Request-Id": uuid.uuid4().hex,
                       "Content-Type": "application/json",
                       hdr: self.appid}
            if resource:
                headers["X-Api-Resource-Id"] = resource
            try:
                r = await self.client.post(url, headers=headers, json=payload, timeout=timeout)
            except httpx.HTTPError as e:
                raise EngineUnavailableError(
                    f"火山引擎{what}网络异常（{type(e).__name__}）：请检查网络后重试") from e
            failed = _auth_failed(r)
            if url not in self._v3_auth and not failed:
                self._v3_auth[url] = hdr
            if not failed or i == len(variants) - 1:
                return r
        return r

    async def _synth_v3(self, text: str, voice_type: str, speed_ratio: float,
                        resource: str) -> bytes:
        """V3 SSE 单向流式合成（主路）：逐行收 data:{...}，base64 拼接成整段。

        voice 用 speaker 字段；X-Api-Resource-Id 选复刻代际（seed-icl-2.0 = v3 注册、
        seed-icl-1.0 = V1 注册）；v3 请求没有 cluster 参数。"""
        # speech_rate：[-50,100]，0=1.0x、100=2.0x、-50=0.5x
        payload = {
            "user": {"uid": "idoltalk"},
            "req_params": {
                "text": text,
                "speaker": voice_type,
                "audio_params": {"format": "mp3",
                                 "speech_rate": int(max(-50, min(100, round((speed_ratio - 1.0) * 100))))},
            },
        }
        r = await self._v3_post(URL_TTS_V3, payload, "合成(V3)", timeout=60.0, resource=resource)
        if r.status_code != 200:
            j = {}
            try:
                j = r.json()
            except Exception:
                pass
            code, msg = j.get("code"), j.get("message") or r.text[:200]
            if code in _VOICE_MISSING_CODES:
                raise _VoiceMissingError(
                    f"code={code}：{msg}（speaker={voice_type}, resource={resource}）")
            raise RuntimeError(f"火山 V3 合成失败：{_fmt_api_error(r.status_code, code, msg)}")
        chunks = []
        for line in r.text.splitlines():
            line = line.strip()
            if not line or line.startswith("event:") or line.startswith(":"):
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            code = obj.get("code", -1)
            if code == 20000000:  # 合成结束标志
                break
            if code != 0:
                if code in _VOICE_MISSING_CODES:
                    raise _VoiceMissingError(
                        f"code={code}：{obj.get('message', '')}"
                        f"（speaker={voice_type}, resource={resource}）")
                raise RuntimeError(
                    f"火山 V3 合成失败：{_fmt_api_error(200, code, obj.get('message'))}")
            d = obj.get("data")
            if d:
                try:
                    chunks.append(base64.b64decode(d))
                except Exception:
                    pass
        raw = b"".join(chunks)
        if not raw:
            raise RuntimeError(
                f"火山 V3 合成未返回音频（speaker={voice_type}, resource={resource}）：常见原因是"
                f"音色与 resource 代际不匹配（V1 注册的音色要用 seed-icl-1.0）、复刻音色未训练完成")
        return raw

    # ------------------------------------------------------------------ 工具

    async def _normalize_mp3(self, raw: bytes) -> bytes:
        """引擎请求的是 mp3；万一拿到 wav 头（RIFF）就用 ffmpeg 转 mp3（契约：wav 用 find_ffmpeg 转）。"""
        if not raw.startswith(b"RIFF"):
            return raw
        tmp_dir = self.cache_dir / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tag = uuid.uuid4().hex[:12]
        wav = tmp_dir / f"volc_{tag}.wav"
        mp3 = tmp_dir / f"volc_{tag}.mp3"
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

    @staticmethod
    async def _concat_mp3(files, out: Path):
        """ffmpeg concat 解复用器无损拼接多段 mp3（-c copy 不重编码，无音质损耗）。"""
        ff = find_ffmpeg()
        lst = out.parent / (out.stem + "_list.txt")
        try:
            lst.write_text("".join("file " + repr(str(f)) + "\n" for f in files), encoding="utf-8")
            cmd = [ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                   "-i", str(lst), "-c:a", "copy", str(out)]
            proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True)
            if proc.returncode != 0 or not out.is_file():
                raise RuntimeError(
                    f"ffmpeg 拼接 mp3 失败: {proc.stderr.decode('utf-8', 'replace')[-300:]}")
        finally:
            lst.unlink(missing_ok=True)

    async def _save_persona(self, pid: str, updates: dict):
        """把音色字段写回 registry.json（原子替换；VoiceStore 按 mtime 自动热重载）。
        值为 None 表示删除该键。调用点都在事件循环内（synthesize 全链路）。"""
        async def _write():
            async with self._reg_lock:
                p = self.voices_dir / "registry.json"
                if not p.is_file():
                    reg = {"version": 1, "personas": {}}
                else:
                    try:
                        reg = json.loads(p.read_text(encoding="utf-8"))
                    except Exception as e:
                        # 半截/损坏 JSON（并发写截断 / 读盘失败）：绝不能把整表重置成空
                        # 覆盖写回（会一次性抹掉全部 persona 的 clips/voice id）。内存里
                        # VoiceStore 的 last-good 副本还有内容 → 以它为基底合并本次更新
                        # 写回（写回即自愈）；内存也没有内容（如重启后文件本就损坏）→
                        # 跳过本次写回，保留损坏文件待后续/人工修复
                        mem = self.store.registry()
                        if mem.get("personas"):
                            print(f"[volc] registry.json 解析失败（{e}），以内存副本为基底写回",
                                  flush=True)
                            reg = json.loads(json.dumps(mem, ensure_ascii=False))
                        else:
                            print(f"[volc] registry.json 解析失败且内存无副本，跳过本次写回（{e}）",
                                  flush=True)
                            return
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
                tmp = p.parent / (p.name + ".tmp")
                tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
                os.replace(tmp, p)
        try:
            await _write()
        except OSError as e:  # 写盘失败只警告：合成流程不被 registry 写回拖垮
            print(f"[volc] registry.json 写回失败（{e}）", flush=True)

    @staticmethod
    def _json(r: httpx.Response, what: str) -> dict:
        try:
            return r.json()
        except Exception:
            raise RuntimeError(f"{what} 返回非 JSON（HTTP {r.status_code}）：{r.text[:200]}")
