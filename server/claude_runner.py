"""Claude CLI 실행기 — asyncio.subprocess 기반, 스트리밍 출력."""
import asyncio
import logging
from server.config import CLAUDE_CMD, CLAUDE_MODEL, CLAUDE_TIMEOUT, MAX_CONCURRENT

logger = logging.getLogger(__name__)

# 동시 실행 제한 세마포어
_semaphore = asyncio.Semaphore(MAX_CONCURRENT)


async def run_claude(message: str) -> str:
    """Claude CLI를 실행하고 전체 응답을 반환."""
    async with _semaphore:
        return await _execute(message)


async def stream_claude(message: str):
    """Claude CLI를 실행하고 청크 단위로 yield (WebSocket용)."""
    async with _semaphore:
        cmd = [CLAUDE_CMD, "--print", "--model", CLAUDE_MODEL, message]
        logger.info("Claude 실행: %s %s", CLAUDE_CMD, CLAUDE_MODEL)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            assert proc.stdout is not None
            while True:
                chunk = await asyncio.wait_for(
                    proc.stdout.read(1024),
                    timeout=CLAUDE_TIMEOUT,
                )
                if not chunk:
                    break
                yield chunk.decode("utf-8", errors="replace")

            await asyncio.wait_for(proc.wait(), timeout=10)

            if proc.returncode != 0 and proc.stderr:
                stderr = await proc.stderr.read()
                err_msg = stderr.decode("utf-8", errors="replace").strip()
                if err_msg:
                    logger.error("Claude stderr: %s", err_msg)
                    yield f"\n\n[Error: {err_msg}]"

        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            yield "\n\n[Error: 타임아웃 (300초 초과)]"
            logger.error("Claude 타임아웃")


async def _execute(message: str) -> str:
    """Claude CLI 실행 후 전체 출력 반환."""
    cmd = [CLAUDE_CMD, "--print", "--model", CLAUDE_MODEL, message]
    logger.info("Claude 실행: %s %s", CLAUDE_CMD, CLAUDE_MODEL)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=CLAUDE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError("Claude CLI 타임아웃 (300초 초과)")

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Claude CLI 에러: {err}")

    return stdout.decode("utf-8", errors="replace")
