"""텍스트 양식 추출기의 입력 크기 시험(전수 점검 P1): 제곱 시간 입력이 몇 초 안에 끝나고, 정상 파싱 결과는 그대로다."""
import random
import re
import threading
import time
import urllib.request

import pytest

from conftest import example_text
from question_authoring.components.extract_textform import ExtractionFailure
from question_authoring.components.extract_textform import (MAX_LINE_CHARS, MAX_QUESTIONS, TextFormExtractor, _kv_split)
from helpers import stop_http
from question_authoring.components.link_authoring_server import MAX_BODY, Api, make_server
from question_authoring.wiring.service import Actor

LIMIT = 5.0   # 초. 이전 코드는 160,000자 한 줄에 62.9초, 슬롯 16,000줄에 26초, 문항 10,000줄에 44초였다(점검 실측)
Q = "문항 Q1: 체온\n"


def run(data: bytes):
    t = time.process_time()      # 왜 CPU 시간: 이 시험은 계산량을 본다. 벽시계는 다른 프로세스와의 경쟁(부하)에 흔들린다
    try:
        r = TextFormExtractor().extract(data, "f.txt")
    except ExtractionFailure as e:
        r = e
    return r, time.process_time() - t


def test_long_whitespace_line_is_linear():
    for body in (b"x" + b" " * 160_000 + b"y", b"x" + b" " * 8_000_000 + b"y"):
        r, took = run(Q.encode() + body + b"\n")
        assert took < LIMIT, took
        assert not isinstance(r, Exception) and any(f.kind == "UNPARSED" for f in r.flags)   # 읽지 않은 줄은 알린다


def test_many_slot_lines():
    data = (Q + "".join(f"슬롯 S{i}: 필수\n" for i in range(16_000))).encode()
    r, took = run(data)
    assert took < LIMIT and isinstance(r, ExtractionFailure)      # 문항당 슬롯 한도 초과는 추측하지 않고 알린다


def test_many_slots_within_limit_are_fast():
    data = (Q + "".join(f"슬롯 S{i}: 필수\n다시 묻기: x\n" for i in range(150))).encode()
    r, took = run(data)
    assert took < LIMIT and len(r.bank["questions"][0]["slots"]) == 150


def test_many_question_lines():
    r, took = run(("".join(f"문항 Q{i}: 이름\n" for i in range(10_000))).encode())
    assert took < LIMIT and isinstance(r, ExtractionFailure)
    r, took = run(("".join(f"문항 Q{i}: 이름\n" for i in range(500))).encode())   # 정상 규모(수십 개)의 수십 배
    assert took < LIMIT and len(r.bank["questions"]) == 500 and len(r.flags) > 0
    r, took = run(("".join(f"문항 Q{i}: 이름\n" for i in range(MAX_QUESTIONS))).encode())
    assert took < LIMIT   # 한도 안이라도 확인할 항목(플래그)이 한도를 넘으면 화면 입력으로 이어 간다


@pytest.mark.parametrize("kind", ["blank", "comment", "short_lines", "prereq_dots", "risk_spaces"])
def test_8mib_inputs_finish_fast(kind):
    n = MAX_BODY
    base = (Q + "순서: 1\n").encode()
    if kind == "blank":
        data = base + b"\n" * n
    elif kind == "comment":
        data = base + b"# " + b"x" * (n - 4) + b"\n"
    elif kind == "short_lines":
        data = base + b"a:b\n" * (n // 4)
    elif kind == "prereq_dots":
        data = base + ("선행 조건: " + "a." * 1500 + "\n").encode() * (n // 3000)
    else:
        data = (Q + "슬롯 S1: 필수\n").encode() + ("위험 기준: = x" + " " * 3000 + "y\n").encode() * (n // 3020)
    data = data[:n]
    r, took = run(data)
    assert took < 10.0, (kind, took)


def test_time_budget_is_enforced():
    ex = TextFormExtractor(); ex.time_budget = -1.0   # 음수: 첫 검사에서 항상 초과(0.0은 시계 해상도에 기댄다)
    with pytest.raises(ExtractionFailure):
        ex._run(("".join(f"문항 Q{i}: 이름\n" for i in range(2000))).encode())


def test_example_form_result_unchanged():
    r = TextFormExtractor().extract(example_text(), "f.txt")
    assert len(r.bank["questions"]) >= 2 and r.bank["questions"][0]["question_id"] == "Q1"
    assert [f.kind for f in r.flags].count("UNPARSED") == 0


_OLD_KV = re.compile(r"^([^:：]+?)\s*[:：]\s*(.*)$")
_OLD_PRE = re.compile(r"^(\S+?)\.(\S+?)\s*=\s*(\S+)$")


def test_kv_and_prerequisite_match_the_old_regexes():
    rnd = random.Random(7)
    alpha = ["a", "b", ":", "：", " ", ".", "=", "가", "1", "\t"]
    for _ in range(4000):
        s = "".join(rnd.choice(alpha) for _ in range(rnd.randint(1, 10))).strip()
        if not s:
            continue
        if s.count("=") > 1:   # 둘 이상의 "="는 새 구현이 더 보수적으로 플래그한다(옛 정규식은 되돌림으로 읽었다)
            continue
        m = _OLD_KV.match(s)
        assert _kv_split(s) == ((m.group(1).strip(), m.group(2).strip()) if m else (None, None)), repr(s)
        # 선행 조건: 새 구현은 TextFormExtractor 안에 있으므로 문항 한 줄로 돌려 비교한다
        old = _OLD_PRE.match(s)
        out = TextFormExtractor().extract(f"문항 Q1: n\n선행 조건: {s}\n".encode(), "f.txt").bank["questions"][0].get("prerequisite", "UNSET")
        if old and not any(c in s for c in "\n"):
            assert out == {"slot": {"question_id": old.group(1), "repeat_key": "", "slot_id": old.group(2)}, "value": old.group(3)}, repr(s)
        else:
            assert not isinstance(out, dict) or out == "UNSET" or out is None, repr(s)


def test_healthz_answers_while_a_large_upload_is_extracted(svc):
    api = Api(svc, {"tok": Actor("dr-kim", "question-author")})
    srv = make_server(api, "127.0.0.1", 0, request_timeout=60)
    threading.Thread(target=srv.run, daemon=True).start()
    port = srv.effective_port
    big = (Q + "".join(f"슬롯 S{i}: 필수\n" for i in range(150)) + "a:b\n" * 1_500_000)[:MAX_BODY].encode()
    done = {}

    def upload():
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/materials?question_set_id=qs-big&filename=f.txt",
                                     data=big, headers={"Authorization": "Bearer tok"}, method="POST")
        t = time.monotonic()
        try:
            urllib.request.urlopen(req, timeout=120).read()
        except Exception as e:
            done["err"] = repr(e)[:80]
        done["secs"] = time.monotonic() - t
    th = threading.Thread(target=upload); th.start()
    worst = 0.0
    while th.is_alive():
        t = time.monotonic()
        assert urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=60).status == 200
        worst = max(worst, time.monotonic() - t)
        time.sleep(0.05)
    th.join()
    stop_http(srv)
    # healthz가 업로드 처리에 막혔다면 업로드가 끝날 때까지 기다렸을 것이다(worst가 업로드 시간과 같아진다).
    # 판정은 시간 자체가 아니라 비율이다: 부하로 둘이 같이 늘어도 지나가고, 막혔다면 worst ~= 업로드 시간이라 실패한다. 1초는 빠른 업로드를 위한 바닥이다
    assert "err" not in done, done
    assert worst < max(1.0, 0.5 * done["secs"]), (worst, done)
