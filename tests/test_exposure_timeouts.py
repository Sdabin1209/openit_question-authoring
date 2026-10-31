"""노출 선언(deploy-exposure.json)의 gateway 시한과 이 서버 쪽 시한의 관계(U427). 실제 시간을 재지 않고 설정 값만 비교한다."""
import json
import pathlib

from question_authoring.components import store_postgres as sp

ROOT = pathlib.Path(__file__).resolve().parent.parent
DECL = json.loads((ROOT / "deploy-exposure.json").read_text(encoding="utf-8"))
EXPOSED = [r for r in DECL["routes"] if r["auth"]["kind"] == "account"]


def test_authoring_routes_declare_the_gateway_time_chain_limits():
    """deploy ADR 0048(시간 사슬, D119)이 정한 값: 저작 경로 dial 1000ms, response_header 13000ms."""
    assert EXPOSED
    for r in EXPOSED:
        assert r["timeouts_ms"] == {"dial": 1000, "response_header": 13000}, r["paths"]


def _worst_ms(*statement_limits_s):
    """문장별 서버 쪽 상한 + 클라이언트 감시 1초(store_postgres._run의 budget + 1.0)의 합."""
    return int(sum(s + 1.0 for s in statement_limits_s) * 1000)


def test_server_side_worst_case_exceeds_the_header_limit_for_large_writes_known_gap():
    """알려진 간극(헤드가 시간 사슬을 다시 정한다, 보고함): PostgreSQL 운영 구성에서 큰 자료 쓰기 문장 하나의 최악은 상한 + 1초다.
    자료 올리기(/v1/materials)는 원본 자료와 초안을 큰 자료 경로로 저장하므로 최악이 문장 둘이다. 기본 설정 값 기준.
    이 간극이 사슬에서 해소되면(기본값이나 선언이 바뀌면) 이 시험을 `<`로 뒤집는다."""
    header_ms = EXPOSED[0]["timeouts_ms"]["response_header"]
    one = _worst_ms(sp.DEFAULT_LARGE_STATEMENT_TIMEOUT)
    upload = _worst_ms(sp.DEFAULT_LARGE_STATEMENT_TIMEOUT, sp.DEFAULT_LARGE_STATEMENT_TIMEOUT)
    assert (one, upload) == (31000, 62000)
    assert one > header_ms and upload > header_ms


def test_plain_statement_worst_case_is_below_the_header_limit_per_statement():
    """일반 문장 하나의 최악(상한 + 1초)은 선언한 response_header보다 짧다. 한 요청의 문장 수는 요청마다 다르다(여기서는 값 하나만 비교)."""
    header_ms = EXPOSED[0]["timeouts_ms"]["response_header"]
    assert _worst_ms(sp.DEFAULT_STATEMENT_TIMEOUT) < header_ms
