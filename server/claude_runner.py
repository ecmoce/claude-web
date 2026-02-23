"""Claude CLI 실행기 — stream-json 모드, 양방향 통신, 권한 요청 지원."""
import asyncio
import json
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

class ClaudeProcess:
    """Claude CLI 프로세스 관리 클래스"""
    def __init__(self):
        self.proc = None
        self.session_id = None
        self.permission_handlers = {}  # tool_use_id -> callback
        
    async def start(self, message: str, file_ids: list[str] | None = None, 
                   upload_dir: Path | None = None, search_context: str | None = None,
                   model: str | None = None, resume_session_id: str | None = None):
        """Claude CLI를 stream-json 모드로 시작"""
        _upload_dir = upload_dir or Path("/tmp")
        full_message = _build_message_with_files(message, file_ids, _upload_dir)
        images = _get_image_files(file_ids, _upload_dir)

        # 웹 검색/딥 리서치 컨텍스트 주입
        if search_context:
            full_message = search_context + "\n\n---\n\n[사용자 질문]\n" + full_message

        cmd = [
            CLAUDE_CMD,
            "--print",
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--verbose",
            "--permission-mode", "bypassPermissions",
            "--model", model or CLAUDE_MODEL
        ]
        
        # 이전 세션 이어가기
        if resume_session_id:
            cmd.extend(["--resume", resume_session_id])
        
        # 이미지 파일 추가
        for img in images:
            cmd.extend(["--file", img])

        logger.info("Claude 실행: %s (model=%s, files=%s)", 
                   " ".join(cmd), model or CLAUDE_MODEL, file_ids)

        self.proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        # 첫 메시지 전송
        initial_message = {
            "type": "user",
            "message": {"role": "user", "content": full_message}
        }
        await self._write_json(initial_message)
        
    async def _write_json(self, data: dict):
        """stdin에 JSON 라인 전송"""
        if not self.proc or not self.proc.stdin:
            return
        try:
            json_line = json.dumps(data, ensure_ascii=False) + "\n"
            self.proc.stdin.write(json_line.encode('utf-8'))
            await self.proc.stdin.drain()
        except Exception as e:
            logger.error("stdin 쓰기 실패: %s", e)
    
    async def read_output(self):
        """stdout에서 JSON 라인 스트림 읽기"""
        if not self.proc or not self.proc.stdout:
            yield {"type": "error", "content": "프로세스가 시작되지 않았습니다"}
            return
            
        byte_buffer = b""
        line_count = 0
        try:
            while True:
                chunk = await asyncio.wait_for(
                    self.proc.stdout.read(8192),
                    timeout=CLAUDE_TIMEOUT,
                )
                if not chunk:
                    break
                    
                byte_buffer += chunk
                
                # 줄 단위로 처리
                while b"\n" in byte_buffer:
                    line, byte_buffer = byte_buffer.split(b"\n", 1)
                    line_count += 1
                    
                    # 너무 많은 라인 방지 (DoS 방지)
                    if line_count > 10000:
                        logger.error("너무 많은 출력 라인: %d", line_count)
                        yield {"type": "error", "content": "출력이 너무 깁니다"}
                        return
                        
                    if line.strip():
                        try:
                            text_line = line.decode('utf-8', errors='replace')
                            data = json.loads(text_line)
                            yield data
                        except (UnicodeDecodeError, json.JSONDecodeError) as e:
                            logger.warning("JSON 파싱 실패 (line %d): %s", line_count, e)
                            # 파싱 실패한 라인 출력 (디버깅용, 처음 100자만)
                            sample = text_line[:100] + ('...' if len(text_line) > 100 else '')
                            logger.debug("파싱 실패 라인: %s", sample)
                            continue
                            
            # 프로세스 종료 대기
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                logger.warning("프로세스 종료 대기 타임아웃")
            
        except asyncio.TimeoutError:
            logger.error("Claude CLI 타임아웃 (%ds)", CLAUDE_TIMEOUT)
            if self.proc:
                try:
                    self.proc.terminate()
                    await asyncio.wait_for(self.proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    self.proc.kill()
                    await self.proc.wait()
            yield {"type": "error", "content": f"타임아웃 ({CLAUDE_TIMEOUT}초 초과)"}
        except Exception as e:
            logger.error("Claude CLI 실행 에러: %s", e)
            yield {"type": "error", "content": f"Claude 실행 오류: {str(e)}"}
    
    async def send_permission_response(self, tool_use_id: str, allowed: bool):
        """권한 요청에 대한 응답 전송"""
        # 여러 가지 형식을 시도해보자
        formats_to_try = [
            # 형식 1: {"type":"permission","permission":{"tool_use_id":"...","allowed":true}}
            {
                "type": "permission",
                "permission": {
                    "tool_use_id": tool_use_id,
                    "allowed": allowed
                }
            },
            # 형식 2: {"type":"permission","tool_use_id":"...","allowed":true}
            {
                "type": "permission",
                "tool_use_id": tool_use_id,
                "allowed": allowed
            },
            # 형식 3: {"type":"user","permission":{"tool_use_id":"...","allowed":true}}
            {
                "type": "user",
                "permission": {
                    "tool_use_id": tool_use_id,
                    "allowed": allowed
                }
            },
            # 형식 4: {"type":"tool_permission","tool_use_id":"...","allowed":true}
            {
                "type": "tool_permission",
                "tool_use_id": tool_use_id,
                "allowed": allowed
            }
        ]
        
        # 모든 형식을 시도
        for i, response in enumerate(formats_to_try, 1):
            await self._write_json(response)
            logger.info("권한 응답 형식 %d 전송: %s -> %s", i, tool_use_id, allowed)
            # 약간의 지연 추가
            await asyncio.sleep(0.1)
    
    async def close(self):
        """프로세스 종료"""
        if self.proc:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
                    await self.proc.stdin.wait_closed()
            except Exception:
                pass
            
            if self.proc.returncode is None:
                self.proc.terminate()
                try:
                    await asyncio.wait_for(self.proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    self.proc.kill()
                    await self.proc.wait()


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
    """Claude CLI를 실행하고 전체 응답을 반환 (비스트리밍, 하위 호환용)."""
    async with _semaphore:
        return await _execute(message, file_ids, upload_dir)


async def stream_claude(message: str, file_ids: list[str] | None = None, upload_dir: Path | None = None,
                        search_context: str | None = None, model: str | None = None):
    """Claude CLI를 stream-json 모드로 실행하고 이벤트 단위로 yield."""
    async with _semaphore:
        proc = ClaudeProcess()
        try:
            await proc.start(message, file_ids, upload_dir, search_context, model)
            
            async for event in proc.read_output():
                # 세션 ID 추출 (첫 번째 init 메시지에서)
                if event.get("type") == "system" and event.get("subtype") == "init":
                    proc.session_id = event.get("session_id")
                
                yield event
                
        except Exception as e:
            logger.error("stream_claude 에러: %s", e)
            yield {"type": "error", "content": str(e)}
        finally:
            await proc.close()


async def _execute(message: str, file_ids: list[str] | None = None, upload_dir: Path | None = None) -> str:
    """Claude CLI 실행 후 전체 출력 반환 (기존 --print 모드, 하위 호환용)."""
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