"""브라우저 시험용 대역 두 개(표준 라이브러리만; 제품 이미지 안에서 돈다).
  gate     <포트> <판정 JSON 파일>   release-gate(I4)의 대역: GET /v1/decisions → ListEnvelope{items: [GateDecision], next_cursor}. 문진표 qs-ui2 에만 판정이 있다
  platform <포트> <서비스 포트> [mutate]  platform 공개 입구의 대역: 쿠키로 사람을 고르고 /oauth2/userinfo 를 주며, 클라이언트의 신원 헤더는 버리고
                                          게이트웨이 토큰과 신원 헤더를 붙여 서비스로 전달한다. mutate 면 판정 조회 경로가 404(변이 확인용)."""
import http.server
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

WHO = {"kim": ("u-kim", "dr-kim", "author"), "park": ("u-park", "dr-park", "clinical-reviewer")}


class Quiet(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        print("STUB", a[1:] if len(a) > 1 else a, flush=True)

    def send(self, st, body=b"", ctype="application/json", extra=None):
        self.send_response(st)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)


def gate(port, decision_file):
    decision = json.load(open(decision_file, encoding="utf-8"))

    class H(Quiet):
        def do_GET(self):
            if not self.path.startswith("/v1/decisions"):
                return self.send(404, b"{}")
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            items = [decision] if q.get("question_set_id") == ["qs-ui2"] else []
            self.send(200, json.dumps({"items": items, "next_cursor": None}).encode())
    http.server.ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()


def platform(port, qa_port, mutate):
    err = json.dumps({"code": "NOT_FOUND", "message": "x", "retryable": False, "pipeline_schema_version": "3.4"}).encode()

    class H(Quiet):
        def who(self):
            c = self.headers.get("Cookie", "")
            return next((v for k, v in WHO.items() if f"who={k}" in c), None)

        def handle_any(self):
            try:
                who = self.who()
                if self.path.startswith("/oauth2/userinfo"):
                    if who is None:
                        return self.send(401)
                    return self.send(200, json.dumps({"user": who[0], "preferredUsername": who[1], "groups": ["/" + who[2]]}).encode())
                if who is None:
                    return self.send(401, json.dumps({"code": "PERMISSION_DENIED", "message": "로그인이 필요하다", "retryable": False,
                                                      "pipeline_schema_version": "3.4"}).encode())
                if mutate and self.path.startswith("/v1/set-decisions"):
                    return self.send(404, err)
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else None
                req = urllib.request.Request(f"http://127.0.0.1:{qa_port}{self.path}", data=body, method=self.command)
                for k in ("Content-Type", "Accept"):
                    if self.headers.get(k):
                        req.add_header(k, self.headers[k])
                req.add_header("Authorization", "Bearer gw-token")
                req.add_header("X-Openit-User", who[0])
                req.add_header("X-Openit-Roles", who[2])
                try:
                    r = urllib.request.urlopen(req, timeout=60)
                except urllib.error.HTTPError as e:
                    r = e
                data = r.read()
                self.send(r.status, data, r.headers.get("Content-Type", "application/json"),
                          {k: v for k, v in (("Retry-After", r.headers.get("Retry-After")), ("Content-Security-Policy", r.headers.get("Content-Security-Policy"))) if v})
            except Exception as e:      # noqa: BLE001  대역의 결함이 빈 응답으로만 보이지 않게
                self.send(502, ("대역 오류 " + type(e).__name__).encode(), "text/plain")
        do_GET = do_POST = handle_any
    http.server.ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()


if __name__ == "__main__":
    if sys.argv[1] == "gate":
        gate(int(sys.argv[2]), sys.argv[3])
    else:
        platform(int(sys.argv[2]), int(sys.argv[3]), len(sys.argv) > 4 and sys.argv[4] == "mutate")
