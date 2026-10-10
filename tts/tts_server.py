# -*- coding: utf-8 -*-
r"""
IdolTalk 本地 TTS 代理服务：OpenAI 兼容接口 → 本地 GPT-SoVITS（引擎可插拔）。

用法（Python 不在 PATH，用绝对路径）：
  D:\AI\Python312\python.exe tts_server.py            # 端口/引擎读 config.json（默认 8780）
  D:\AI\Python312\python.exe tts_server.py --port 8780 --engine gpt_sovits --gptsovits-url http://127.0.0.1:9880

接口（OpenAI 兼容，CORS 全放行——应用是 file:// 页面，Origin 为 null）：
  POST /v1/audio/speech  {"model","voice","input","instructions","response_format","speed"}
                         voice=persona-id；缓存命中或合成成功 → audio/mpeg 字节
  GET  /v1/voices        {"voices":[{"id","status","engine","gender"}],"ready_count","total_personas"}
  GET  /healthz          {"engine":"up|down","queue":N}
应用里『AI 配音接口』填：http://127.0.0.1:8780/v1/audio/speech

缓存：cache/<pid>/sha1("engine|voice|speed|text").mp3（与 warm_cache.py 的算法一致，
speed 用 Python 浮点 repr，如 1.0 / 0.8），原子写入 + .json 旁车记录。
"""
import argparse
import asyncio
import hashlib
import io
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from engines import EngineUnavailableError, VoiceNotReadyError, VoiceStore, load_engine

BASE_DIR = Path(__file__).resolve().parent


def load_config() -> dict:
    """读脚本同目录的 config.json（缺省值兜底）；相对 cache_dir 挂到脚本目录下。"""
    cfg = {}
    p = BASE_DIR / "config.json"
    if p.is_file():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[warn] config.json 解析失败，改用默认值：{e}", flush=True)
    cfg.setdefault("port", 8780)
    cfg.setdefault("engine_default", "gpt_sovits")
    cfg.setdefault("gptsovits_url", "http://127.0.0.1:9880")
    cfg.setdefault("voices_dir", r"C:\Users\95242\Desktop\IdolTalk-voices")
    cfg.setdefault("cache_dir", "cache")
    cd = Path(cfg["cache_dir"])
    if not cd.is_absolute():
        cd = BASE_DIR / cd
    cfg["cache_dir"] = str(cd)
    return cfg


def oai_body(message: str, code: str, etype: str = "invalid_request_error") -> dict:
    """OpenAI 风格错误体。"""
    return {"error": {"message": message, "type": etype, "code": code}}


def engine_error(e: Exception):
    """引擎异常 → (HTTP 状态码, 错误体)。"""
    if isinstance(e, EngineUnavailableError):
        return 503, oai_body(str(e), "engine_unavailable", "server_error")
    if isinstance(e, VoiceNotReadyError):
        return 400, oai_body(str(e), "voice_not_ready")
    return 502, oai_body(f"合成失败：{e}", "synthesis_failed", "server_error")


def _write_sidecar(path: Path, text: str, engine: str, mp3: Path):
    """缓存旁车信息 {text, engine, dur, created}（128k CBR → 时长≈字节数/16000）。"""
    data = {"text": text, "engine": engine,
            "dur": round(mp3.stat().st_size / 16000.0, 2),
            "created": datetime.now().isoformat(timespec="seconds")}
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _wav_to_mp3(wav: Path, mp3: Path):
    """流式收完后的 WAV→MP3（走线程池调用）。ffmpeg 解析器复用引擎里的实现。"""
    import subprocess
    from engines.gpt_sovits import find_ffmpeg
    cmd = [find_ffmpeg(), "-y", "-loglevel", "error", "-i", str(wav),
           "-codec:a", "libmp3lame", "-b:a", "128k", str(mp3)]
    proc = subprocess.run(cmd, capture_output=True, timeout=180)
    if proc.returncode != 0 or not mp3.is_file() or mp3.stat().st_size == 0:
        raise RuntimeError(f"wav→mp3 失败: {proc.stderr.decode('utf-8', 'replace')[-200:]}")


def create_app(cfg: dict, engine, store: VoiceStore) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app):
        await engine.startup()
        fb = cfg.get("fallback_engine")
        if fb is not None:  # 备用引擎也要 startup（建 HTTP 客户端等），否则回退时秒失败
            try:
                await fb.startup()
            except Exception as e:
                print(f"[warn] 备用引擎启动失败：{e}", flush=True)
        yield
        try:
            await engine.shutdown()
        except Exception:
            pass
        if fb is not None:
            try:
                await fb.shutdown()
            except Exception:
                pass

    app = FastAPI(title="IdolTalk TTS 代理", lifespan=lifespan)
    # file:// 页面 Origin 为 null，必须全放行（含预检）
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.state.cfg = cfg
    app.state.engine = engine
    app.state.store = store
    app.state.cache_dir = Path(cfg["cache_dir"])
    app.state.pending = {}  # cache-key -> Future：并发去重 + /healthz 的 queue 计数

    @app.get("/")
    async def root():
        return {"service": "idol-tts", "speech": "/v1/audio/speech",
                "voices": "/v1/voices", "health": "/healthz"}

    @app.get("/healthz")
    async def healthz():
        try:
            up = await engine.is_up()
        except Exception:
            up = False
        return {"engine": "up" if up else "down", "queue": len(app.state.pending)}

    @app.get("/v1/voices")
    async def voices():
        reg = store.registry()
        default_engine = reg.get("engine_default") or cfg["engine_default"]
        out = [{"id": pid,
                "status": per.get("status", "missing"),
                "engine": per.get("engine") or default_engine,
                "gender": per.get("gender", "female")}
               for pid, per in sorted((reg.get("personas") or {}).items())]
        # 可用 = 有参考音频（sourced 起即可合成，validated/ready 为更高质量终态）；
        # missing/failed 不计入。注意整条管线的自动终态是 validated，没有代码会置 ready。
        usable = ("sourced", "transcribed", "validated", "ready")
        return {"voices": out,
                "ready_count": sum(1 for v in out if v["status"] in usable),
                "total_personas": int(cfg.get("total_personas", 132))}

    @app.post("/v1/audio/speech")
    async def speech(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content=oai_body("请求体不是合法 JSON", "bad_request"))
        text = str(body.get("input") or "").strip()
        voice = str(body.get("voice") or "").strip()
        fmt = str(body.get("response_format") or "mp3").strip().lower()
        if body.get("instructions"):
            print(f"[debug] 忽略 instructions：{str(body['instructions'])[:60]}", flush=True)
        if not voice:
            return JSONResponse(status_code=400,
                                content=oai_body("缺少 voice 字段（应为 persona-id，如 aespa-giselle）", "missing_voice"))
        if not text:
            return JSONResponse(status_code=400, content=oai_body("缺少 input 文本", "missing_input"))
        if fmt not in ("mp3", ""):
            return JSONResponse(status_code=400,
                                content=oai_body(f"只支持 response_format=mp3（收到 {fmt}）", "unsupported_format"))
        try:
            speed = float(body.get("speed", 1.0))
        except (TypeError, ValueError):
            return JSONResponse(status_code=400, content=oai_body("speed 必须是数字", "invalid_speed"))
        speed = min(max(speed, 0.5), 2.0)  # 引擎安全范围

        if store.persona(voice) is None:
            print(f"[tts] 404 未知音色: {voice!r}（registry 里没有这个 persona-id）", flush=True)
            return JSONResponse(status_code=404,
                                content=oai_body(f"未知音色 {voice}：registry.json 里没有这个 persona-id",
                                                 "voice_not_found"))

        # 缓存 key 与 warm_cache.py 一致：sha1("engine|voice|speed|text")，speed 为浮点 repr
        key = hashlib.sha1(f"{engine.name}|{voice}|{speed}|{text}".encode("utf-8")).hexdigest()
        cache_file = app.state.cache_dir / voice / f"{key}.mp3"
        if cache_file.is_file():  # 热缓存命中
            return FileResponse(cache_file, media_type="audio/mpeg")

        pending = app.state.pending
        waited = pending.get(key)
        if waited is not None:  # 同 key 已有请求在合成 → 等它，不重复合成
            outcome = await waited
            if not outcome.get("ok"):
                return JSONResponse(status_code=outcome["status"], content=outcome["body"])
            return FileResponse(cache_file, media_type="audio/mpeg")

        fut = asyncio.get_running_loop().create_future()
        pending[key] = fut
        t0 = time.monotonic()
        try:
            try:
                tmp_mp3 = await engine.synthesize(text, voice, speed)
            except Exception as e_primary:
                fb = cfg.get("fallback_engine")
                if fb is None:
                    raise
                print(f"[tts] 主引擎失败（{str(e_primary)[:100]}），自动回退 {fb.name}", flush=True)
                tmp_mp3 = await fb.synthesize(text, voice, speed)
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp_mp3, cache_file)  # 原子落盘
            _write_sidecar(cache_file.with_suffix(".json"), text, engine.name, cache_file)
            fut.set_result({"ok": True})
            print(f"[tts] {voice} 合成 {time.monotonic() - t0:.1f}s → {cache_file.name}", flush=True)
            return FileResponse(cache_file, media_type="audio/mpeg")
        except Exception as e:
            status, err_body = engine_error(e)
            fut.set_result({"ok": False, "status": status, "body": err_body})
            print(f"[tts] {voice} 合成失败（{status}）：{e}", flush=True)
            return JSONResponse(status_code=status, content=err_body)
        finally:
            pending.pop(key, None)

    @app.get("/v1/audio/stream")
    async def stream(voice: str = "", text: str = "", speed: float = 1.0, slow: int = 0):
        """流式朗读（本地引擎专用，边合成边播，首音 ~3-4 秒；完成后自动写缓存）。

        应用里 <audio src="…/v1/audio/stream?voice=<pid>&text=…"> 直接边下边播。
        """
        text = (text or "").strip()
        voice = (voice or "").strip()
        if not voice or not text:
            return JSONResponse(status_code=400, content=oai_body("缺少 voice 或 text 参数", "bad_request"))
        if store.persona(voice) is None:
            print(f"[tts] 404 未知音色（stream）: {voice!r}", flush=True)
            return JSONResponse(status_code=404,
                                content=oai_body(f"未知音色 {voice}：registry.json 里没有这个 persona-id",
                                                 "voice_not_found"))
        try:
            spd = min(max(float(speed), 0.5), 2.0)
        except (TypeError, ValueError):
            spd = 1.0
        if slow:
            spd = min(spd, 0.8)

        key = hashlib.sha1(f"{engine.name}|{voice}|{spd}|{text}".encode("utf-8")).hexdigest()
        cache_file = app.state.cache_dir / voice / f"{key}.mp3"
        if cache_file.is_file():
            return FileResponse(cache_file, media_type="audio/mpeg")

        stream_fn = getattr(engine, "synthesize_stream", None)
        if stream_fn is None:
            return JSONResponse(status_code=400,
                                content=oai_body("当前引擎不支持流式合成", "stream_unsupported"))

        collected = []

        async def gen():
            completed = False
            try:
                async for chunk in stream_fn(text, voice, spd):  # WAV 直通（最平滑）
                    collected.append(chunk)
                    yield chunk
                completed = True
            except Exception as e:
                print(f"[tts] 流式合成失败 {voice}: {e}", flush=True)
            finally:
                if completed and collected:  # 只在完整生成后写缓存（断流不缓存半成品）
                    try:
                        wav_bytes = b"".join(collected)
                        tmp_dir = Path(cfg["cache_dir"]) / "tmp"
                        tmp_dir.mkdir(parents=True, exist_ok=True)
                        wav_tmp = tmp_dir / f"{key}.wav"
                        mp3_tmp = tmp_dir / f"{key}.mp3"
                        wav_tmp.write_bytes(wav_bytes)
                        await asyncio.to_thread(_wav_to_mp3, wav_tmp, mp3_tmp)
                        cache_file.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(mp3_tmp, cache_file)
                        _write_sidecar(cache_file.with_suffix(".json"), text, engine.name, cache_file)
                        print(f"[tts] 流式完成 {voice} → 缓存 {cache_file.name}"
                              f"（wav {len(wav_bytes)//1024}KB → mp3 {cache_file.stat().st_size//1024}KB）", flush=True)
                        wav_tmp.unlink(missing_ok=True)
                    except Exception as e:
                        print(f"[tts] 缓存写入失败: {e}", flush=True)

        return StreamingResponse(gen(), media_type="audio/wav")

    return app


def main():
    ap = argparse.ArgumentParser(description="IdolTalk 本地 TTS 代理服务（OpenAI 兼容 → GPT-SoVITS）")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    ap.add_argument("--port", type=int, default=None, help="端口（默认读 config.json → 8780）")
    ap.add_argument("--engine", default=None, help="引擎（默认读 config.json → gpt_sovits）")
    ap.add_argument("--gptsovits-url", dest="gptsovits_url", default=None,
                    help="GPT-SoVITS api_v2 地址（默认 http://127.0.0.1:9880）")
    args = ap.parse_args()

    cfg = load_config()
    if args.port:
        cfg["port"] = args.port
    if args.engine:
        cfg["engine_default"] = args.engine
    if args.gptsovits_url:
        cfg["gptsovits_url"] = args.gptsovits_url

    store = VoiceStore(cfg["voices_dir"])
    cfg["store"] = store  # 引擎与服务层共用同一份 registry 缓存
    try:
        engine = load_engine(cfg["engine_default"], cfg)
    except ValueError as e:
        print(f"[fatal] {e}", flush=True)
        sys.exit(1)
    # 备用引擎：主引擎失败（云端欠费/超限/断网等）时自动回退，保证永远有声音
    fallback = None
    fb_name = (cfg.get("engine_fallback") or "").strip()
    if fb_name and fb_name != engine.name:
        try:
            fallback = load_engine(fb_name, cfg)
            print(f"[tts] 备用引擎已加载: {fallback.name}（主引擎失败时自动回退）", flush=True)
        except Exception as e:
            print(f"[warn] 备用引擎 {fb_name} 加载失败：{e}", flush=True)
    cfg["fallback_engine"] = fallback

    app = create_app(cfg, engine, store)
    port = int(cfg["port"])
    print(f"[tts] 引擎={engine.name}  音色库={cfg['voices_dir']}  缓存={cfg['cache_dir']}", flush=True)
    print(f"[tts] 服务地址 http://{args.host}:{port}；应用里『AI 配音接口』填 "
          f"http://127.0.0.1:{port}/v1/audio/speech", flush=True)
    uvicorn.run(app, host=args.host, port=port, log_level="info")


if __name__ == "__main__":
    main()
