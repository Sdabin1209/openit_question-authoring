"""R1.8 자기 영역 게이트웨이 출구의 시험용 대역(표준 라이브러리만). python 컨테이너 안에서 돈다.
CONNECT 줄을 표준 출력에 기록하고, 허용 이름 하나(ALLOWED)만 같은 docker 망의 UPSTREAM(host:port)으로 터널을 연다. 나머지는 403."""
import re
import socket
import sys
import threading

ALLOWED, UPSTREAM_HOST, UPSTREAM_PORT, LISTEN = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])


def pipe(a, b):
    try:
        while True:
            d = a.recv(65536)
            if not d:
                break
            b.sendall(d)
    except OSError:
        pass
    finally:
        try:
            b.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def serve(c):
    with c:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = c.recv(4096)
            if not chunk:
                return
            data += chunk
        line = data.split(b"\r\n", 1)[0].decode()
        print("CONNECT-LINE " + line, flush=True)
        m = re.match(r"CONNECT ([\w.-]+):(\d+) HTTP/1\.1", line)
        if not m or m.group(1) != ALLOWED:
            c.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            return
        up = socket.create_connection((UPSTREAM_HOST, UPSTREAM_PORT), timeout=30)
        c.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        t = threading.Thread(target=pipe, args=(up, c), daemon=True)
        t.start()
        pipe(c, up)
        t.join(30)
        up.close()


s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", LISTEN))
s.listen(16)
print("LISTENING", flush=True)
while True:
    conn, _ = s.accept()
    threading.Thread(target=serve, args=(conn,), daemon=True).start()
