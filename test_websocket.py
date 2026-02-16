#!/usr/bin/env python3
"""WebSocket 클라이언트 테스트 스크립트"""
import asyncio
import json
import websockets

async def test_websocket():
    uri = "ws://localhost:8451/ws"
    
    async with websockets.connect(uri) as websocket:
        print("WebSocket 연결됨")
        
        # 연결 확인
        response = await websocket.recv()
        print("받은 메시지:", response)
        
        # 파일 생성 요청 전송 (권한 요청이 예상됨)
        message = {
            "message": "Create a test file called hello.txt with content 'Hello World'",
            "conversation_id": "test-conv-001"
        }
        await websocket.send(json.dumps(message))
        print("메시지 전송:", message)
        
        # 응답 수신
        while True:
            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                data = json.loads(response)
                print(f"받은 이벤트 [{data.get('type')}]:", data)
                
                # 권한 요청 응답
                if data.get('type') == 'permission_request':
                    tool_use_id = data.get('tool_use_id')
                    print(f"권한 요청 발견! tool_use_id: {tool_use_id}")
                    
                    # 권한 허용 응답
                    permission_response = {
                        "type": "permission_response",
                        "tool_use_id": tool_use_id,
                        "allowed": True
                    }
                    await websocket.send(json.dumps(permission_response))
                    print("권한 허용 응답 전송:", permission_response)
                
                # 완료 시 종료
                if data.get('type') == 'done':
                    print("스트리밍 완료!")
                    break
                    
            except asyncio.TimeoutError:
                print("타임아웃")
                break
            except json.JSONDecodeError as e:
                print("JSON 파싱 오류:", e)
                print("원본 응답:", response)

if __name__ == "__main__":
    asyncio.run(test_websocket())