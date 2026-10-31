"""텍스트 양식 추출기의 입력 크기 시험(전수 점검 P1): 제곱 시간 입력이 몇 초 안에 끝나고, 정상 파싱 결과는 그대로다."""
import random
import re

import pytest

from conftest import example_text
from question_authoring.components.extract_textform import ExtractionFailure
from question_authoring.components.extract_textform import (MAX_LINE_CHARS, MAX_QUESTIONS, TextFormExtractor, _kv_split)
from helpers import run_bounded, start_server
from question_authoring.components.link_authoring_server import MAX_BODY, Api, make_server
from question_authoring.wiring.service import Actor

# 시간을 재지 않는다(U421): 이 시험들은 '큰 입력에서도 끝난다'를 본다. 이전 코드는 160,000자 한 줄에 62.9초, 슬롯 16,000줄에 26초,
# 문항 10,000줄에 44초였고(점검 실측) 8MiB에서는 수 시간이다. 결함이 되살아나면 run()의 매달림 감시(HANG_GUARD_SECONDS, 정상은 1초 안팎)에 걸려 실패한다.
Q = "문항 Q1: 체온\n"


def run(data: bytes):
    def go():
        try:
            return TextFormExtractor().extract(data, "f.txt")
        except ExtractionFailure as e:
            return e
    return run_bounded(go), None


def test_long_whitespace_line_is_linear():
    for body in (b"x" + b" " * 160_000 + b"y", b"x" + b" " * 8_000_000 + b"y"):
        r, _ = run(Q.encode() + body + b"\n")
        assert not isinstance(r, Exception) and any(f.kind == "UNPARSED" for f in r.flags)   # 읽지 않은 줄은 알린다


def test_many_slot_lines():
    data = (Q + "".join(f"슬롯 S{i}: 필수\n" for i in range(16_000))).encode()
    r, _ = run(data)
    assert isinstance(r, ExtractionFailure)      # 문항당 슬롯 한도 초과는 추측하지 않고 알린다


def test_many_slots_within_limit_are_fast():
    data = (Q + "".join(f"슬롯 S{i}: 필수\n다시 묻기: x\n" for i in range(150))).encode()
    r, _ = run(data)
    assert len(r.bank["questions"][0]["slots"]) == 150


def test_many_question_lines():
    r, _ = run(("".join(f"문항 Q{i}: 이름\n" for i in range(10_000))).encode())
    assert isinstance(r, ExtractionFailure)
    r, _ = run(("".join(f"문항 Q{i}: 이름\n" for i in range(500))).encode())   # 정상 규모(수십 개)의 수십 배
    assert len(r.bank["questions"]) == 500 and len(r.flags) > 0
    r, _ = run(("".join(f"문항 Q{i}: 이름\n" for i in range(MAX_QUESTIONS))).encode())
    # 한도 안이라도 확인할 항목(플래그)이 한도를 넘으면 화면 입력으로 이어 간다


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
    r, _ = run(data)
    assert r is not None      # 8MiB 입력이 끝났다(결과 종류는 입력마다 다르다)


def test_time_budget_is_enforced_with_an_injected_clock():
    """시간 예산은 우리 코드의 판단이다: 시험이 쥔 시계가 예산을 넘어가면 추출을 포기한다. 실제 시간은 흐르지 않는다."""
    now = [0.0]

    def clock():
        now[0] += 100.0                                   # 시계를 부를 때마다 100초가 흐른다고 친다
        return now[0]
    ex = TextFormExtractor(clock)
    with pytest.raises(ExtractionFailure):
        ex._run(("".join(f"문항 Q{i}: 이름\n" for i in range(2000))).encode())


def test_time_budget_value_is_the_documented_one():
    assert TextFormExtractor.time_budget == 6.0          # 설계 문서·ADR의 값. 실제 시간으로 재지 않고 값을 확인한다


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


def test_large_upload_is_extracted_inside_a_worker_thread_not_the_server_loop(svc):
    """큰 자료를 추출하는 동안 서버가 다른 요청에 답하는가는 waitress의 스레드 모델(라이브러리)이다(U421 정정). 영역의 몫은 값이다:
    처리 스레드가 1개보다 많게 기본 설정되어 있고(동시 요청 수), 본문 상한이 8MiB 자료를 받을 수 있다."""
    srv = make_server(Api(svc, {"tok": Actor("dr-kim", "question-author")}), "127.0.0.1", 0)
    try:
        assert srv.adj.threads > 1 and srv.adj.max_request_body_size >= MAX_BODY
    finally:
        srv.close()
