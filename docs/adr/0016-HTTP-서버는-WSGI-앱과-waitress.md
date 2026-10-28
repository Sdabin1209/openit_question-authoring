# ADR 0016: HTTP 서버는 WSGI 앱과 waitress

- 상태: 채택 (2026-10-29, D111). ADR 0012(표준 라이브러리 ThreadingHTTPServer)를 대체(Supersedes)한다.
- 날짜: 2026-10-29

## 맥락
ADR 0012의 서버는 연결 상한·소켓 timeout·본문 상한·인증 전 본문 미수신·우아한 종료를 직접 유지했다. Python 문서는 `http.server`를 운영용으로 권하지 않는다. 헤드 결정 D111: 직접 만든 서버 인프라를 표준 도구로 바꾼다.

## 결정
`Api.handle()`을 WSGI 앱(PEP 3333, `http_api.wsgi_app`)으로 감싸고 waitress 3.0.2가 서빙한다(`make_server`). 로직(`Api`, `service.py`)은 그대로다.

## 왜 (표준과 근거)
- WSGI는 Python 웹 서버 인터페이스 표준(PEP 3333)이다. 이 서비스는 단일 프로세스·다중 스레드(서비스 객체와 초안 잠금이 프로세스 안에 있다, ADR 0008)라 prefork 서버(gunicorn)가 아니라 스레드 풀 서버인 waitress가 맞다. waitress는 순수 Python이고 의존이 없다.
- 직접 만든 코드 삭제: 요청 세마포어, 소켓 timeout 핸들러, Content-Length 검증, 본문 크기 검사, 요청 줄 파서(`BaseHTTPRequestHandler`).

## 설정값과 출처
| 설정 | 값 | 출처 |
| --- | --- | --- |
| `threads` | `QA_MAX_CONCURRENT_REQUESTS`, 기본 32 | 값 32는 ADR 0007 그대로: 관리자 소수라는 가정의 여유값, **측정 안 함**. waitress 기본은 4 |
| `channel_timeout` | `QA_REQUEST_TIMEOUT_SECONDS` 기본 30(정수 초로 반올림) | ADR 0007 그대로, **측정 안 함** |
| `cleanup_interval` | 1 | 제약: 기본 30초면 channel_timeout이 최대 30초 늦게 적용된다(waitress 문서). 정수 초의 최솟값 |
| `max_request_body_size` | 8MiB(`MAX_BODY`) | ADR 0007 그대로, 근거 기록 없음 |
| `connection_limit` | waitress 기본 100 | **측정 안 함**, 기본값 수용. 이전에는 연결 상한이 없었다 |
| `clear_untrusted_proxy_headers` | True | 이 서비스는 프록시 헤더를 쓰지 않으므로 X-Forwarded-* 를 지운다(waitress 3의 경고를 명시적으로 끈다) |

## 검토한 대안과 버리는 이유
- gunicorn(prefork): 프로세스마다 잠금·SQLite 연결이 따로여서 ADR 0008의 단일 프로세스 전제를 깬다.
- ASGI/프레임워크: 이 규모에서 얻는 것이 불명확(비교 측정 안 함).
- 현상 유지: 직접 유지보수할 한도 코드가 남는다.

## 결과: 동작이 바뀌는 곳 (시험 `test_server_properties.py`, `test_fault_paths.py`)
- 본문 상한 초과: 앱의 400에서 waitress의 **413**(헤더 단계, RFC 9110 15.5.14)으로. 계약 CONVENTIONS 9절 표(INVALID_RECORD=400)와 어긋나 헤드에게 알린다. 413은 plain text 본문이다.
- 요청 한도 초과: 즉시 503 + Retry-After에서 **줄 서서 기다림**으로. 핸들러 스레드 `QA_MAX_CONCURRENT_REQUESTS`개가 처리 중이면 나머지는 큐에서 기다린다.
- 인증 전 본문 미수신이 사라진다: waitress는 요청 본문을 다 받은 뒤(512KiB 넘으면 임시 파일에 쌓으며, 최대 8MiB) 앱을 부른다. 인증 없는 호출의 업로드도 channel_timeout 안에서 받는다.
- 종료: 새 연결을 받지 않고 **앱이 처리 중인 요청**과 응답 전송을 유예 시간(기본 8초, 측정 안 함)까지 기다린다(`drain`). 본문을 받는 중인 요청은 처리 중이 아니라 기다리지 않는다. 유예를 넘기면 `os._exit(0)`(waitress 정상 종료가 스레드를 5초 더 기다려 docker stop 10초를 넘기기 때문).
- 지원하지 않는 메서드는 JSON 404(이전: 501 HTML).
- HTTP/1.1 keep-alive.

## 되돌리는 조건
다중 프로세스가 필요해지면(ADR 0008의 잠금을 저장소로 옮긴 뒤) prefork 서버를 검토하고 이 ADR을 대체한다.
