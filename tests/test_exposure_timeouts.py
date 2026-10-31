"""노출 선언(deploy-exposure.json)의 gateway 시한과 이 서버 쪽 시한의 관계(U427). 실제 시간을 재지 않고 설정 값만 비교한다."""
import json
import pathlib


ROOT = pathlib.Path(__file__).resolve().parent.parent
DECL = json.loads((ROOT / "deploy-exposure.json").read_text(encoding="utf-8"))
EXPOSED = [r for r in DECL["routes"] if r["auth"]["kind"] == "account"]


def test_authoring_routes_declare_the_gateway_time_chain_limits():
    """deploy ADR 0048(시간 사슬, D119)이 정한 값: 저작 경로 dial 1000ms, response_header 13000ms."""
    assert EXPOSED
    for r in EXPOSED:
        assert r["timeouts_ms"] == {"dial": 1000, "response_header": 13000}, r["paths"]


def test_request_deadline_is_inside_the_gateway_header_limit():
    """U427의 알려진 간극(큰 자료 쓰기 최악 31초·62초 > 13초)은 요청 전체 마감(D120)으로 닫혔다: 서버는 마감(기본 12초)에서 503으로 끝낸다.
    설정 값끼리 비교한다. 각 문장은 남은 시간을 상한으로 쓴다(tests/test_postgres_specific.py::test_remaining_request_time_caps_each_statement)."""
    from question_authoring.wiring.deadline import DEFAULT_REQUEST_DEADLINE_SECONDS
    header_ms = EXPOSED[0]["timeouts_ms"]["response_header"]
    assert DEFAULT_REQUEST_DEADLINE_SECONDS * 1000 < header_ms
