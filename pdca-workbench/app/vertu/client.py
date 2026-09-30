# -*- coding: utf-8 -*-
"""vertu-cli 异步子进程封装与运行时健康检查。"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from loguru import logger


def resolve_vertu_command() -> str:
    """解析 CLI 路径。实际命令是 vps-work，见 app.config.resolve_cli_command。"""
    from app.config import resolve_cli_command

    return resolve_cli_command()


async def run_vertu(
    args: list[str],
    timeout: float = 45.0,
) -> tuple[int, str, str]:
    """
    异步执行 vertu 命令。

    @param args vertu 子命令参数（不含 vertu 本身）
    @param timeout 超时秒数
    @returns (exit_code, stdout, stderr)
    """
    import sys
    bin_path = resolve_vertu_command()
    # Windows .cmd/.bat 文件必须经 cmd /c 执行，否则 asyncio 子进程无法识别
    if sys.platform == "win32" and bin_path.lower().endswith((".cmd", ".bat")):
        try:
            _reject_cmd_metachars(args)
        except ValueError as exc:
            logger.error("{}", exc)
            return -1, "", str(exc)
        cmd = ["cmd", "/c", bin_path, *args]
    else:
        cmd = [bin_path, *args]
    logger.debug("vertu-cli exec: {}", " ".join(cmd))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            proc.returncode or 0,
            stdout_b.decode("utf-8", errors="replace"),
            stderr_b.decode("utf-8", errors="replace"),
        )
    except asyncio.TimeoutError:
        # 必须真的杀掉子进程：以前只返回超时，命令仍在后台跑完——对 im +send-* 这类
        # 写操作就是「调用方以为失败、消息其实发出去了」，重试还会再发一条（2026-09-20 审查）。
        logger.warning("vertu-cli 超时，终止子进程: {}", " ".join(cmd))
        try:
            proc.kill()
            await proc.wait()
        except (ProcessLookupError, OSError) as exc:  # 进程可能已退出
            logger.debug("vertu-cli 终止时进程已结束: {}", exc)
        return -1, "", f"timeout after {timeout}s"
    except OSError as exc:
        logger.error("vertu-cli 执行失败: {}", exc)
        return -1, "", str(exc)


# Windows 上 vertu-cli 是 .cmd，必须经 cmd /c 启动，而 cmd.exe 会二次解析参数：
# 参数里出现引号 + & | ^ < > % ! 就能拼出额外命令（2026-09-20 审查实测可执行 echo）。
# 这里直接拒绝这类参数——调用方应该用 --body-file/env 传自由文本，而不是塞进命令行。
_CMD_METACHARS = set('"&|^<>%')


def _reject_cmd_metachars(args: list[str]) -> None:
    """经 cmd /c 传参前做一次白名单式检查，命中直接拒绝（不执行）。"""
    for item in args:
        text = str(item)
        hit = sorted({ch for ch in text if ch in _CMD_METACHARS})
        if hit:
            raise ValueError(f"参数含 cmd 特殊字符 {hit}，拒绝执行以免命令注入: {text[:60]!r}")


def run_vertu_sync(
    args: list[str],
    timeout: float = 60.0,
) -> tuple[int, str, str]:
    """同步执行 vertu-cli，供调度线程/后台线程复用。

    @returns (exit_code, stdout, stderr)；执行失败返回 (-1, "", 错误信息)。
    """
    command = resolve_vertu_command()
    cmd = [command, *args]
    if sys.platform == "win32" and command.lower().endswith((".cmd", ".bat")):
        try:
            _reject_cmd_metachars(args)
        except ValueError as exc:
            logger.error("{}", exc)
            return -1, "", str(exc)
        cmd = ["cmd", "/c", command, *args]
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("vertu-cli 同步调用失败: {}", exc)
        return -1, "", str(exc)
    return (
        completed.returncode or 0,
        completed.stdout or "",
        completed.stderr or "",
    )


def run_vertu_sync_json(args: list[str], timeout: float = 60.0) -> dict | list | None:
    """同步调用 vertu-cli 并解析 JSON，供调度线程和遗留同步函数复用。"""
    code, stdout, stderr = run_vertu_sync(args, timeout=timeout)
    if code != 0 or not stdout.strip():
        logger.warning(
            "vertu-cli 同步调用无有效输出 code={} stderr={}",
            code,
            stderr[:200],
        )
        return None
    try:
        return json.loads(stdout.strip())
    except json.JSONDecodeError:
        logger.warning("vertu-cli 同步输出不是 JSON: {}", stdout[:200])
        return None


async def run_vertu_sandbox(
    code: str,
    params: dict | None = None,
    timeout: float = 45.0,
) -> dict | list | None:
    """旧 sandbox 兼容入口；vertu-cli 2.x 不再提供任意 Odoo sandbox。"""
    del code, params, timeout
    raise RuntimeError("vertu-cli 2.x 不支持 odoo data sandbox，请改用业务快捷命令")


async def run_vertu_json(
    args: list[str],
    timeout: float = 45.0,
) -> dict | list | None:
    """执行 vertu 并解析 JSON 输出。

    vertu sandbox 命令在有 permission notices 时返回 exit code 255（非错误），
    因此优先尝试解析 stdout JSON，仅在 stdout 为空时才把非零 exit code 视为失败。
    """
    command = args[0] if args else ""
    if command and command in _MISSING_COMMANDS:
        return None
    code, stdout, stderr = await run_vertu(args, timeout=timeout)
    err = stderr or ""
    if code != 0 and command and "unknown command" in err.lower():
        _MISSING_COMMANDS.add(command)
        logger.warning("vps-work 没有子命令 {}，本进程不再调用", command)
        return None
    text = stdout.strip()
    if text:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass
        logger.warning("vertu-cli 输出非 JSON: {}", text[:200])
        return None
    if code != 0:
        logger.warning("vertu-cli 非零退出 {} (无 stdout): {}", code, stderr)
    return None


_HEALTH_CACHE: dict = {"ts": 0.0, "value": None}
_MISSING_COMMANDS: set[str] = set()


def cli_command_missing(name: str) -> bool:
    """本进程里已经确认 vertu-cli 没有的子命令。"""
    return name in _MISSING_COMMANDS


async def vertu_health(force: bool = False) -> dict:
    """返回脱敏的 vertu-cli 安装与认证状态，结果缓存 60 秒。"""
    now = time.monotonic()
    cached = _HEALTH_CACHE.get("value")
    if not force and cached and now - float(_HEALTH_CACHE.get("ts") or 0) < 60:
        return dict(cached)

    command = resolve_vertu_command()
    resolved = Path(command).is_file() or bool(shutil.which(command))
    if not resolved:
        value = {"ok": False, "installed": False, "auth_mode": None, "detail": "vps-work 未安装"}
    else:
        # `auth status` in vertu-cli 2.1.x still requires a local
        # ~/.vertu/vps-service.json even when complete Agent credentials are
        # supplied through environment variables. Containers intentionally do
        # not persist that human-login file, so validate the server-backed
        # scopes endpoint instead.
        code, stdout, stderr = await run_vertu(["auth", "scopes", "--json"], timeout=12.0)
        try:
            payload = json.loads(stdout.strip()) if stdout.strip() else {}
        except json.JSONDecodeError:
            payload = {}
        scopes = payload.get("userScopes")
        authorized = bool(code == 0 and payload.get("login") and isinstance(scopes, list))
        agent_app_id = payload.get("agentAppId")
        value = {
            "ok": authorized,
            "installed": True,
            "auth_mode": "agent" if agent_app_id else "session",
            "never_expires": bool(agent_app_id),
            "detail": None if authorized else "vps-work 凭据不可用",
        }
        if code != 0:
            logger.warning("vertu-cli auth scopes 失败: {}", (stderr or "")[:200])
    _HEALTH_CACHE.update({"ts": now, "value": value})
    return dict(value)
