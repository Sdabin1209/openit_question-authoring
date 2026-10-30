"""동시 쓰기의 의미(낙관적 잠금): 같은 다음 버전을 여러 쓰기가 동시에 노리면 정확히 하나만 성공한다.
모든 저장소 구현(store 픽스처)에서 같은 시험을 돈다."""
import threading

import pytest
from boundary_contracts.errors import BoundaryError

from question_authoring.wiring.service import AuthoringService
from helpers import bank_hash
from helpers import AUTHOR, publish


def _variant(svc, store, base_ref, i):
    bank = store.get_question_bank(base_ref)
    bank = dict(bank, version=base_ref["version"] and int(base_ref["version"]) + 1)
    bank["question_set_id"] = bank.get("question_set_id", base_ref["id"])
    bank["_variant"] = i  # 내용을 다르게 해 해시가 다르게 한다
    ref = {"id": base_ref["id"], "version": str(int(base_ref["version"]) + 1), "content_hash": bank_hash(bank)}
    review = {"review_id": f"rv-conc-{i}", "question_set": ref}
    return bank, ref, review


def test_concurrent_next_version_exactly_one_wins(store, svc):
    ref = publish(svc)
    n = 8
    items = [_variant(svc, store, ref, i) for i in range(n)]
    results, barrier = [], threading.Barrier(n)

    def run(it):
        barrier.wait()
        try:
            store.commit_version(it[0], it[1], it[2], None, None)
            results.append("ok")
        except BoundaryError as e:
            results.append(e.code)

    ts = [threading.Thread(target=run, args=(it,)) for it in items]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert results.count("ok") == 1, results
    assert results.count("CONFLICT") == n - 1, results
    assert [v for v, _ in store._version_rows(ref["id"])] == [1, 2]
