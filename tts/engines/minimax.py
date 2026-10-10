# -*- coding: utf-8 -*-
"""
MiniMax（海螺）声音复刻引擎：上传参考音频 → voice_clone 建音色 → T2A v2 合成，返回 mp3。

API 契约（2026-10 官方文档 platform.minimaxi.com，交叉核对官方 MiniMax-MCP / mmx-cli 源码）：
  1) POST {host}/v1/files/upload   multipart：file=<音频>, purpose=voice_clone|prompt_audio
     → {"file": {"file_id": ...}, "base_resp": {...}}
     硬性限制：克隆音频 10 秒~5 分钟、示例音频 <8 秒、≤20MB、mp3/m4a/wav。
  2) POST {host}/v1/voice_clone    JSON：{"file_id", "voice_id"[, "clone_prompt": {"prompt_audio",
     "prompt_text"}, "need_noise_reduction"]}，base_resp.status_code==0 即成功。
     voice_id 规则：长度 [8,256]、首字符必须英文字母、可用字母数字 - _、末位不可为 -/_;
     复刻音色是临时音色：7 天内不在任意合成接口使用会被删除（本引擎克隆后立刻合成，自然转正）。
  3) POST {host}/v1/t2a_v2         JSON：{"model", "text", "voice_setting": {"voice_id","speed","vol",
     "pitch"}, "audio_setting": {"sample_rate","bitrate","format","channel"},
     "language_boost", "output_format": "hex"}
     → {"data": {"audio": "<hex 编码的 mp3 字节>"}, "extra_info": {...}, "trace_id": ...}
鉴权：Authorization: Bearer {minimax_api_key}；GroupId 按国内平台文档惯例挂在 URL 查询参数
（?GroupId={minimax_group_id}）。业务错误通常仍是 HTTP 200 + base_resp.status_code（1004 鉴权失败、
1002/1039 限流、1008 余额不足、2013 参数不合法、2038 无复刻权限），trace_id 在响应体里。

config.json 键（冻结接口）：minimax_api_key / minimax_group_id；
可选附加键：minimax_host（默认 https://api.minimaxi.com，国际站改 https://api.minimax.io 时
GroupId 仍照填）、minimax_model（默认 speech-2.8-hd 最新 HD 复刻模型，追求低延迟可改
speech-2.6 / speech-02-turbo）、minimax_noise_reduction（默认 true，参考源来自抖音带底噪）。
克隆出的音色 id 存回 registry persona 的 "minimax_voice_id" 字段（引擎名 + "_voice_id"）。
"""
import asyncio
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

import httpx

try:
    import msvcrt  # Windows 跨进程文件锁（registry 并发写防护），非 Windows 退化为无锁
except ImportError:
    msvcrt = None

from . import EngineUnavailableError, TTSEngine, VoiceNotReadyError, VoiceStore
from .gpt_sovits import find_ffmpeg  # ffmpeg 定位逻辑与 gpt_sovits 保持一致

MINIMAX_UNCONFIGURED = (
    "MiniMax（海螺）未配置：请在 config.json 填 minimax_api_key 与 minimax_group_id 两键"
    "（platform.minimaxi.com 账户管理 → 接口密钥 / 基本信息获取）"
)

# MiniMax 复刻的硬性时长限制（秒）
CLONE_MIN_SECONDS = 10.0    # 克隆音频最少 10 秒
CLONE_TARGET_SECONDS = 12.0  # 多段短切片拼接时的目标总时长（留余量）
PROMPT_MAX_SECONDS = 8.0    # 示例音频必须小于 8 秒
CONCAT_MAX_CLIPS = 6        # 最多拼接多少段（同时限制总时长与上传体积）

# 常见错误码的可操作提示（拼进异常消息，方便用户自己修）
_HINTS = {
    1004: "鉴权失败：请检查 minimax_api_key（以及与 minimax_host 是否同一区域）",
    1008: "账户余额不足，请先充值",
    1002: "触发限流，请稍后重试",
    1039: "触发 TPM 限流，请稍后重试",
    2013: "入参不合法（对照：voice_id 长度 8~256 且字母开头；克隆音频 10s~5min；mp3/m4a/wav）",
    2038: "无复刻权限：请先在 platform.minimaxi.com 完成个人实名或企业认证",
}


def _clone_rank(c: dict) -> tuple:
    """克隆素材的切片排序键：score 高者优先，其次时长长（克隆不需要转写文本）。"""
    return (float(c.get("score") or 0), float(c.get("dur") or 0))


def _end_with_punct(s: str) -> str:
    """MiniMax 要求示例音频的对应文本以标点结尾；缺标点时补一个句号。"""
    s = " ".join((s or "").split())
    if not s:
        return s
    return s if s[-1] in ".,!?;:。！？；：" else s + "."


def _acquire_registry_lock(lock_path: Path):
    """拿 voices_dir/registry.lock 的跨进程排它锁（1 字节 msvcrt.locking）。

    协议与 voice_batch.py / idol-crawler 的 fetch_idol_voices.py 完全一致（同一把锁）：
    registry.json 有多个长跑写者，整文件 load-modify-save 不加锁会互相静默抹掉对方
    刚写入的条目。LK_LOCK 拿不到时最多自旋约 10 秒，仍失败返回 None（调用方退化为
    无锁写入并警告，不阻塞主流程）。"""
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


class MiniMaxEngine(TTSEngine):
    """云端引擎：合成走 MiniMax API 可并发（无本地单卡瓶颈，无需全局串行），
    只有「首次克隆音色 + 写回 registry」用 asyncio.Lock 全局串行，防止重复建音色。"""

    name = "minimax"
    # 冻结接口：云端音色 id 存 registry persona 的 引擎名 + "_voice_id"
    VOICE_FIELD = "minimax_voice_id"

    def __init__(self, config: dict):
        self.host = str(config.get("minimax_host") or "https://api.minimaxi.com").rstrip("/")
        self.api_key = str(config.get("minimax_api_key") or "").strip()
        self.group_id = str(config.get("minimax_group_id") or "").strip()
        self.model = str(config.get("minimax_model") or "speech-2.8-hd").strip()
        self.noise_reduction = bool(config.get("minimax_noise_reduction", True))
        self.creds_ok = bool(self.api_key and self.group_id)
        self.voices_dir = Path(config.get("voices_dir") or r"C:\Users\95242\Desktop\IdolTalk-voices")
        cd = Path(config.get("cache_dir") or "cache")
        self.cache_dir = cd if cd.is_absolute() else Path(__file__).resolve().parent.parent / cd
        # 服务层会传入共享的 VoiceStore；单独调用（voice_batch 等）时自建
        self.store = config.get("store") or VoiceStore(self.voices_dir)
        self.client = None
        self.tmp_dir = self.cache_dir / "tmp"
        self._clone_lock = asyncio.Lock()

    # --------------------------------------------------------------- 生命周期

    async def startup(self):
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0), headers=headers)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - 86400  # 顺手清掉超过一天的残留临时文件
        for f in self.tmp_dir.iterdir():
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass
        if self.creds_ok:
            print(f"[engine] MiniMax（海螺）@ {self.host} —— 已配置（model={self.model}，"
                  f"降噪={'开' if self.noise_reduction else '关'}）；"
                  "没有 minimax_voice_id 的音色会在首次合成时自动克隆并写回 registry", flush=True)
        else:
            print(f"[engine] MiniMax（海螺）—— 未配置：{MINIMAX_UNCONFIGURED}"
                  "；/healthz 会报 down，合成请求返回 503", flush=True)

    async def shutdown(self):
        if self.client:
            await self.client.aclose()
            self.client = None

    async def is_up(self) -> bool:
        if not (self.client and self.creds_ok):
            return False
        try:  # 任何 HTTP 响应（含 404/403）都说明 API 域名可达
            await self.client.get(self.host + "/", timeout=3.0)
            return True
        except Exception:
            return False

    def list_ready(self) -> list:
        return [pid for pid, per in sorted((self.store.registry().get("personas") or {}).items())
                if per.get("status") == "ready"]

    # --------------------------------------------------------------- 合成主流程

    async def synthesize(self, text: str, voice_id: str, speed: float = 1.0) -> Path:
        if not self.creds_ok:
            raise EngineUnavailableError(MINIMAX_UNCONFIGURED)
        persona = self.store.persona(voice_id)
        if not persona:
            raise VoiceNotReadyError(f"音色 {voice_id} 不在 registry.json 里，无法合成")
        cloud_vid = persona.get(self.VOICE_FIELD) or await self._ensure_cloud_voice(voice_id)
        try:
            return await self._t2a(text, cloud_vid, speed, persona)
        except RuntimeError as e:
            if not self._voice_gone(e):
                raise
            # 复刻音色在云端失效（如临时音色 7 天未用被删）：清掉本地 id，重建一次再合成
            print(f"[engine] minimax：音色 {cloud_vid} 在平台侧已失效，重新克隆——{e}", flush=True)
            await self._save_registry_voice(voice_id, None)
            cloud_vid = await self._ensure_cloud_voice(voice_id)
            return await self._t2a(text, cloud_vid, speed, persona)

    async def _t2a(self, text: str, cloud_vid: str, speed: float, persona: dict) -> Path:
        # 语速：克隆音色自带说话节奏，默认不补偿（与 gpt_sovits 的 speed_scale=1.12 互不影响），
        # 需要按爱豆微调时在 registry persona 里写 minimax_speed_scale
        try:
            scale = float(persona.get("minimax_speed_scale") or 1.0)
        except (TypeError, ValueError):
            scale = 1.0
        spd = round(min(max(float(speed) * scale, 0.5), 2.0), 3)
        payload = {
            "model": self.model,
            "text": text,
            "stream": False,
            "output_format": "hex",
            "voice_setting": {"voice_id": cloud_vid, "speed": spd, "vol": 1.0, "pitch": 0},
            "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
            "language_boost": "English",  # 冻结契约：应用是英语陪练，正文恒为英文
        }
        body = await self._api("POST", "/v1/t2a_v2", json_body=payload)
        data = body.get("data") or {}
        audio = data.get("audio")
        if not audio:
            raise RuntimeError(f"MiniMax 未返回音频（data.audio 为空）：{body.get('base_resp')}")
        raw = await self._decode_audio(audio)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        mp3 = self.tmp_dir / f"{uuid.uuid4().hex[:12]}.mp3"
        mp3.write_bytes(raw)
        return mp3

    async def _decode_audio(self, audio) -> bytes:
        """data.audio 正常是 hex 编码的音频字节；防御性支持返回 URL 的情况（下载后返回）。"""
        if isinstance(audio, str) and audio[:4].lower() == "http":
            try:
                r = await self.client.get(audio, timeout=60.0)
                r.raise_for_status()
            except httpx.HTTPError as e:
                raise RuntimeError(f"下载 MiniMax 音频 URL 失败：{e}") from e
            return r.content
        try:
            raw = bytes.fromhex(audio)
        except (TypeError, ValueError) as e:
            raise RuntimeError(f"MiniMax 音频既不是 hex 也不是 URL，无法解码：{str(audio)[:80]}") from e
        if len(raw) < 64:
            raise RuntimeError(f"MiniMax 返回的音频异常短（{len(raw)} 字节）")
        return raw

    # --------------------------------------------------------------- HTTP 层

    async def _api(self, method: str, path: str, *, json_body=None, data=None, files=None,
                   timeout=httpx.USE_CLIENT_DEFAULT) -> dict:
        """统一请求 MiniMax 开放平台：Bearer 鉴权头 + GroupId 查询参数，并检查 base_resp
        （其业务错误通常仍返回 HTTP 200，必须看 status_code）。

        timeout 默认值必须是 httpx.USE_CLIENT_DEFAULT 而不是 None：httpx 0.28 里显式传
        timeout=None 会把 connect/read/write/pool 全部置 None（关闭一切超时），完全绕过
        startup() 配置的客户端级 Timeout(120.0, connect=10.0)——合成热路径一旦网络停滞
        就会无限挂起（tts_server 的调用方没有 asyncio.wait_for 兜底）。需要放宽的接口
        （克隆/上传）由调用点显式传 Timeout 对象。"""
        if not self.client:
            raise EngineUnavailableError("MiniMax 引擎未初始化（startup 未调用）")
        params = {"GroupId": self.group_id} if self.group_id else None
        if timeout is None:  # 防御：裸 None 在 httpx 0.28 = 全超时关闭，按客户端默认配置处理
            timeout = httpx.USE_CLIENT_DEFAULT
        try:
            r = await self.client.request(method, self.host + path, params=params,
                                          json=json_body, data=data, files=files, timeout=timeout)
        except httpx.HTTPError as e:
            raise EngineUnavailableError(f"MiniMax API 连不上（{type(e).__name__}）：{e}") from e
        try:
            body = r.json()
        except Exception:
            raise RuntimeError(f"MiniMax 返回非 JSON（HTTP {r.status_code}）：{r.text[:200]}") from None
        br = body.get("base_resp") or {}
        code = br.get("status_code")
        if r.status_code >= 400 or (code is not None and code != 0):
            msg = str(br.get("status_msg") or r.text[:200])
            hint = _HINTS.get(code, "")
            trace = body.get("trace_id") or r.headers.get("trace-id") or ""
            raise RuntimeError(f"MiniMax 接口错误 {code}（HTTP {r.status_code}）：{msg}"
                               + (f" [trace_id={trace}]" if trace else "")
                               + (f" —— {hint}" if hint else ""))
        return body

    # --------------------------------------------------------------- 音色克隆

    async def _ensure_cloud_voice(self, pid: str) -> str:
        """全局锁内二次确认音色是否已建，避免并发重复克隆；未建则克隆并写回 registry。"""
        async with self._clone_lock:
            vid = (self.store.persona(pid) or {}).get(self.VOICE_FIELD)
            if vid:
                return vid
            print(f"[engine] minimax：音色 {pid} 还没有云端克隆音色，开始用参考音频创建"
                  "（每音色仅一次，首次合成会稍慢）…", flush=True)
            vid = await self._clone_voice(pid)
            await self._save_registry_voice(pid, vid)
            print(f"[engine] minimax：音色 {pid} 克隆完成 → minimax_voice_id={vid} 已写回 registry", flush=True)
            return vid

    async def _clone_voice(self, pid: str) -> str:
        """上传克隆音频（必要时 ffmpeg 拼接）→ 上传示例音频（可选）→ voice_clone。"""
        srcs, prompt = self._plan_clone_audio(pid)
        # 1) 上传待克隆音频：多段短切片先拼接成一条 ≥10 秒的 wav
        upload_path = srcs[0] if len(srcs) == 1 else await self._concat_wav(srcs)
        file_id = await self._upload_file(upload_path, "voice_clone")
        # 2) 调用复刻接口
        payload = {
            "file_id": file_id,
            "voice_id": self._voice_id_for(pid),
            "need_noise_reduction": self.noise_reduction,  # 抖音源带底噪，默认降噪换干净音色
        }
        if prompt is not None:
            p_file_id = await self._upload_file(self.voices_dir / pid / prompt["file"], "prompt_audio")
            payload["clone_prompt"] = {
                "prompt_audio": p_file_id,
                "prompt_text": _end_with_punct(prompt.get("prompt_text") or ""),
            }
        # 克隆/上传放宽总超时到 300 秒，但 connect 仍保持 10 秒（连接阶段卡 300 秒无法接受）
        await self._api("POST", "/v1/voice_clone", json_body=payload,
                        timeout=httpx.Timeout(300.0, connect=10.0))
        return payload["voice_id"]

    async def _upload_file(self, path: Path, purpose: str):
        """上传本地音频 → file_id（purpose=voice_clone / prompt_audio）。"""
        content = await asyncio.to_thread(path.read_bytes)
        ctype = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4"}.get(
            path.suffix.lower(), "application/octet-stream")
        body = await self._api("POST", "/v1/files/upload", data={"purpose": purpose},
                               files={"file": (path.name, content, ctype)},
                               timeout=httpx.Timeout(300.0, connect=10.0))
        file_id = (body.get("file") or {}).get("file_id")
        if file_id is None:
            raise RuntimeError(f"MiniMax 文件上传响应里没有 file_id：{body}")
        return file_id

    async def _concat_wav(self, srcs: list) -> Path:
        """把多段短切片用 ffmpeg 拼成一条 wav（统一 32k 单声道），满足克隆音频 ≥10 秒的硬性要求。"""
        inputs = []
        for p in srcs:
            inputs += ["-i", str(p)]
        # 每路先 aformat 归一到 32k/mono 再 concat（输入采样率/声道不一致也能拼）
        parts = "".join(f"[{i}:a]aformat=sample_rates=32000:channel_layouts=mono[a{i}];"
                        for i in range(len(srcs)))
        labels = "".join(f"[a{i}]" for i in range(len(srcs)))
        out = self.tmp_dir / f"clone_{uuid.uuid4().hex[:12]}.wav"
        cmd = [find_ffmpeg(), "-y", "-loglevel", "error", *inputs,
               "-filter_complex", f"{parts}{labels}concat=n={len(srcs)}:v=0:a=1[out]",
               "-map", "[out]", str(out)]
        proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True)
        if proc.returncode != 0 or not out.is_file():
            raise RuntimeError(f"ffmpeg 拼接参考音频失败：{proc.stderr.decode('utf-8', 'replace')[-300:]}")
        return out

    def _plan_clone_audio(self, pid: str):
        """选克隆素材：单条 ≥10 秒直接用；否则按排名拼多条到 ~12 秒；另挑一条 <8 秒且带转写的
        切片当 clone_prompt 示例音频（官方文档说明可提升相似度与稳定性）。
        返回 (素材 Path 列表, 示例切片 dict 或 None)。"""
        merged = {}
        for src in ((self.store.persona(pid) or {}).get("clips") or [],
                    self.store.meta(pid).get("clips") or []):
            for c in src:
                f = c.get("file")
                if f and (f not in merged or _clone_rank(c) > _clone_rank(merged[f])):
                    merged[f] = c
        clips = [c for c in sorted(merged.values(), key=_clone_rank, reverse=True)
                 if (self.voices_dir / pid / c["file"]).is_file()]
        if not clips:
            raise VoiceNotReadyError(f"音色 {pid} 没有磁盘上存在的参考音频，无法为 MiniMax 克隆")
        known_dur = any(float(c.get("dur") or 0) > 0 for c in clips)
        best = clips[0]
        srcs, used, total = [], set(), 0.0
        if float(best.get("dur") or 0) >= CLONE_MIN_SECONDS or not known_dur:
            # 单条达标（或时长未知，交给平台判断）：直接上传原文件，不动 ffmpeg
            srcs = [self.voices_dir / pid / best["file"]]
            used, total = {best["file"]}, float(best.get("dur") or 0)
        else:
            for c in clips:  # 短切片按排名拼接，凑到目标时长
                if len(srcs) >= CONCAT_MAX_CLIPS or total >= CLONE_TARGET_SECONDS:
                    break
                srcs.append(self.voices_dir / pid / c["file"])
                used.add(c["file"])
                total += float(c.get("dur") or 0)
            if total < CLONE_MIN_SECONDS:
                raise VoiceNotReadyError(
                    f"音色 {pid} 的参考音频合计仅 {total:.1f} 秒，不满足 MiniMax 复刻最低 "
                    f"{CLONE_MIN_SECONDS:.0f} 秒（要求 10 秒~5 分钟）：请先补充更长的 clips")
        # 示例音频（<8 秒且带转写），优先选不在克隆音频里的那条
        prompt = None
        for c in clips:
            if (c.get("prompt_text") and 0 < float(c.get("dur") or 0) < PROMPT_MAX_SECONDS
                    and c["file"] not in used):
                prompt = c
                break
        if prompt is None:
            for c in clips:
                if c.get("prompt_text") and 0 < float(c.get("dur") or 0) < PROMPT_MAX_SECONDS:
                    prompt = c
                    break
        return srcs, prompt

    # --------------------------------------------------------------- registry 写回

    async def _save_registry_voice(self, pid: str, vid):
        """把克隆音色 id 写回 registry.json（vid=None 表示删除该字段，音色云端失效清缓存用）。

        写入走 voice_batch.py 的跨进程写保护协议（同一把 registry.lock）：锁内重读磁盘态、
        只修改目标 persona 的 minimax_voice_id 字段、原子替换整文件，避免与爬虫/批量转写
        的长跑写者互相抹条目。文件锁的获取（msvcrt.LK_LOCK 最长自旋约 10 秒）和磁盘 I/O
        都是阻塞操作，放到线程里跑，不卡事件循环（这是 App 点击触发的热路径）。"""
        await asyncio.to_thread(self._save_registry_voice_sync, pid, vid)

    def _save_registry_voice_sync(self, pid: str, vid):
        reg_path = self.voices_dir / "registry.json"
        lock_f = _acquire_registry_lock(self.voices_dir / "registry.lock")
        if lock_f is None:
            print("[engine] minimax：[warn] registry.lock 获取失败（或平台无 msvcrt），"
                  "无锁写回 registry.json（存在并发写者时可能互相覆盖）", flush=True)
        try:
            try:
                reg = json.loads(reg_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                reg = {"version": 1, "engine_default": "gpt_sovits", "personas": {}}
            except Exception as e:
                raise RuntimeError(f"registry.json 读取失败，minimax_voice_id 无法持久化：{e}") from e
            per = (reg.get("personas") or {}).get(pid)
            if per is None:
                raise VoiceNotReadyError(f"音色 {pid} 不在 registry.json 里，无法写回克隆音色 id")
            if vid:
                per[self.VOICE_FIELD] = vid
            else:
                per.pop(self.VOICE_FIELD, None)
            # tmp 名带进程号（与 voice_batch 的 registry.json.<os.getpid()>.tmp 约定一致），
            # 避免与并行写者的固定 registry.json.tmp 撞名
            tmp = reg_path.parent / f"{reg_path.name}.{os.getpid()}.tmp"
            tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(reg_path)
        finally:
            _release_registry_lock(lock_f)

    # --------------------------------------------------------------- 纯工具

    @staticmethod
    def _voice_id_for(pid: str) -> str:
        """persona-id → 合规的自定义 voice_id：长度 [8,256]、首字符英文字母、
        只含字母数字 - _、末位不可为 -/_。大多数 persona-id 本身就合规（原样使用）。"""
        vid = re.sub(r"[^0-9A-Za-z_-]", "-", (pid or "").strip()).strip("-_")
        if not vid:
            vid = "idol"
        if not vid[0].isalpha():
            vid = "idol-" + vid
        if len(vid) < 8:
            vid = "idol-" + vid
        vid = vid[:200].rstrip("-_")
        if len(vid) < 8:
            vid = vid.ljust(8, "x")
        return vid

    @staticmethod
    def _voice_gone(e: Exception) -> bool:
        """判断合成报错是否为「克隆音色在平台侧已不存在」（临时音色 7 天未用会被删除等）。"""
        m = str(e).lower()
        if "voice" not in m and "音色" not in m:
            return False
        return any(k in m for k in ("not exist", "not found", "no such", "expired",
                                    "deleted", "不存在", "已删除", "已过期"))
