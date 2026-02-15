"""Claude CLI 실행기 — asyncio.subprocess 기반, 스트리밍 출력, 파일 첨부 지원."""
import asyncio
import logging
import os
from pathlib import Path
from server.config import CLAUDE_CMD, CLAUDE_MODEL, CLAUDE_TIMEOUT, MAX_CONCURRENT

logger = logging.getLogger(__name__)

# 동시 실행 제한 세마포어
_semaphore = asyncio.Semaphore(MAX_CONCURRENT)

# 이미지 확장자
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
# 텍스트 확장자 (이미지가 아닌 모든 허용 파일)
TEXT_EXTENSIONS = {
    ".txt", ".py", ".js", ".ts", ".md", ".json", ".csv", ".yaml", ".yml",
    ".html", ".css", ".xml", ".log", ".sh", ".sql", ".java", ".go", ".rs",
    ".c", ".cpp", ".h", ".rb", ".php", ".swift", ".kt", ".toml", ".cfg",
    ".ini", ".env", ".pdf",
}


def _build_message_with_files(message: str, file_ids: list[str] | None, upload_dir: Path) -> str:
    """파일 내용을 메시지에 포함시킨 텍스트를 반환 (텍스트 파일만)."""
    if not file_ids:
        return message

    parts = []
    for fid in file_ids:
        # 안전한 파일 탐색 (traversal 방지)
        file_path = upload_dir / fid
        if not file_path.exists() or not file_path.resolve().parent == upload_dir.resolve():
            continue
        ext = file_path.suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            continue  # 이미지는 별도 처리
        if ext == ".pdf":
            parts.append(f"\n\n--- 첨부 파일: {fid} (PDF, 내용 미리보기 불가) ---")
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            # 너무 큰 파일은 잘라내기
            if len(content) > 50000:
                content = content[:50000] + "\n... (잘림, 50000자 초과)"
            parts.append(f"\n\n--- 첨부 파일: {fid} ---\n```\n{content}\n```")
        except Exception as e:
            logger.warning("파일 읽기 실패 %s: %s", fid, e)

    if parts:
        return message + "".join(parts)
    return message


def _get_image_files(file_ids: list[str] | None, upload_dir: Path) -> list[str]:
    """이미지 파일 경로 목록 반환."""
    if not file_ids:
        return []
    images = []
    for fid in file_ids:
        file_path = upload_dir / fid
        if not file_path.exists() or not file_path.resolve().parent == upload_dir.resolve():
            continue
        if file_path.suffix.lower() in IMAGE_EXTENSIONS:
            images.append(str(file_path))
    return images


async def run_claude(message: str, file_ids: list[str] | None = None, upload_dir: Path | None = None) -> str:
    """Claude CLI를 실행하고 전체 응답을 반환."""
    async with _semaphore:
        return await _execute(message, file_ids, upload_dir)


async def stream_claude(message: str, file_ids: list[str] | None = None, upload_dir: Path | None = None,
                        search_context: str | None = None):
    """Claude CLI를 실행하고 청크 단위로 yield (WebSocket용)."""
    async with _semaphore:
        _upload_dir = upload_dir or Path("/tmp")
        full_message = _build_message_with_files(message, file_ids, _upload_dir)
        images = _get_image_files(file_ids, _upload_dir)

        # 웹 검색/딥 리서치 컨텍스트 주입
        if search_context:
            full_message = search_context + "\n\n---\n\n[사용자 질문]\n" + full_message

        cmd = [CLAUDE_CMD, "--print", "--model", CLAUDE_MODEL]
        # 이미지 파일 추가
        for img in images:
            cmd.extend(["--file", img])
        cmd.append(full_message)

        logger.info("Claude 실행: %s %s (files=%s)", CLAUDE_CMD, CLAUDE_MODEL, file_ids)

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


async def _execute(message: str, file_ids: list[str] | None = None, upload_dir: Path | None = None) -> str:
    """Claude CLI 실행 후 전체 출력 반환."""
    _upload_dir = upload_dir or Path("/tmp")
    full_message = _build_message_with_files(message, file_ids, _upload_dir)
    images = _get_image_files(file_ids, _upload_dir)

    cmd = [CLAUDE_CMD, "--print", "--model", CLAUDE_MODEL]
    for img in images:
        cmd.extend(["--file", img])
    cmd.append(full_message)

    logger.info("Claude 실행: %s %s (files=%s)", CLAUDE_CMD, CLAUDE_MODEL, file_ids)

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
