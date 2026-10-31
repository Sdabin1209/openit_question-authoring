"""L24: PostgreSQL에서 업로드(ingest_material)·편집·확인과 purge가 실제로 동시에 도는 경쟁 시험. TEST_DATABASE_URL이 없으면 SKIP.

경쟁 가능한 지점(코드 읽기, store/postgres.py, store/base.py purge, service.py):
 R1 purge는 한 트랜잭션이 아니다(스탬프 -> 초안 목록 -> 초안별 삭제 -> 원본 목록 -> 원본별 삭제). 단계 사이에 쓰기가 끼어든다.
 R2 초안 삭제: 목록에서 읽은 뒤 삭제 전에 편집이 저장되면 지우지 않아야 한다(record 동일 조건부 삭제).
 R3 원본 삭제: 업로드(put_source가 저장 시각 갱신)와 초안 저장(참조) 사이의 틈에서도 새 자료를 지우지 않아야 한다.
 R4 모든 쓰기는 같은 advisory lock으로 직렬화된다. 연결이 여럿(스레드별 store, 별도 프로세스)이어도 같아야 한다.
 R5 오래 방치된 초안을 확인(confirm)하는 중에 purge가 그 초안과 원본을 지우면 버전이 없는 원본을 가리킬 수 있다.
시험은 고정 sleep을 쓰지 않는다. 시작은 Barrier, 끝은 정해진 반복 횟수(또는 프로세스 종료)로 정한다. 반복 횟수는 QA_RACE_ROUNDS로 늘린다."""
from helpers import HANG_GUARD_SECONDS
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone

from conftest import pg_schema, example_text
from helpers import AUTHOR
from question_authoring.__main__ import purge_once
from question_authoring.wiring.service import AuthoringService
from question_authoring.wiring.stores import PostgresQuestionStore

ROUNDS = int(os.environ.get("QA_RACE_ROUNDS", "12"))


def _old(days=40):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


class _AgeGate:
    """'나이 올리기'(시간 여행)는 진행 중인 업로드가 없을 때만 한다. 진행 중 업로드의 '원본은 있고 참조는 아직 없음' 틈은 보존 기간 안의
    새 자료라 purge 대상이 아니다(저장 시각을 방금 갱신했다). 시간 여행이 그 가정을 깨면 시험이 거짓 실패한다(test_ops_hardening의 SQLite 시험과 같은 이유).
    purge 자체는 이 게이트와 무관하게 업로드와 자유롭게 경쟁한다."""
    def __init__(self):
        self.cv = threading.Condition()
        self.active = 0
        self.aging = False
        self.finished = 0
        self.seen = 0

    def enter(self):
        with self.cv:
            self.cv.wait_for(lambda: not self.aging)
            self.active += 1

    def leave(self):
        with self.cv:
            self.active -= 1
            self.finished += 1
            self.cv.notify_all()

    def finish(self, done):
        """종료를 알린다: age()에서 기다리는 스레드를 깨운다."""
        with self.cv:
            done.set()
            self.cv.notify_all()

    def age(self, store, done):
        with self.cv:
            # 업로드가 하나 끝날 때마다 한 번만 나이를 올린다(굶김 방지: 올리기만 반복하면 업로드가 진행하지 못한다)
            # 조건(업로드 없음 + 새로 끝난 업로드 있음, 또는 종료)이 참이 될 때까지 기다린다. 깨우는 쪽은 leave()와 finish()다.
            # 시간 제한(HANG_GUARD_SECONDS)은 매달림 감시일 뿐 판정이 아니다
            self.cv.wait_for(lambda: (self.active == 0 and self.finished != self.seen) or done.is_set(), HANG_GUARD_SECONDS)
            if done.is_set() and not (self.active == 0 and self.finished != self.seen):
                return
            self.seen = self.finished
            self.aging = True
        try:
            store._write(lambda c: c.execute("UPDATE blobs SET created_at=%s", (_old(),)))
        finally:
            with self.cv:
                self.aging = False
                self.cv.notify_all()


def _material(k: int) -> bytes:
    # 짝수: 모든 스레드가 같은 자료(같은 해시를 동시에 다시 올림), 홀수: 매번 다른 자료(읽지 못해 FALLBACK 초안)
    return example_text() if k % 2 == 0 else b"unreadable-material-%d-" % k + os.urandom(8).hex().encode()


def _check_drafts_have_sources(store, drafts):
    for did, h in drafts:
        d = store.load_draft(did)
        assert d is not None, f"초안이 사라졌다: {did}"
        assert d["source"]["source_hash"] == h
        assert store.get_source(h) is not None, f"초안이 참조하는 원본이 사라졌다: {h}"


def test_pg_concurrent_uploads_and_purge_threads(validators):
    """여러 연결(스레드마다 store 하나)의 업로드와 purge가 동시에 돈다. 참조된 원본은 사라지지 않고 어떤 호출도 오류를 내지 않는다."""
    n_up, n_purge = 3, 2
    with pg_schema() as (url, schema):
        stores = [PostgresQuestionStore(url, schema=schema) for _ in range(n_up + n_purge + 1)]
        ups, purgers, ager = stores[:n_up], stores[n_up:n_up + n_purge], stores[-1]
        gate = _AgeGate()
        barrier = threading.Barrier(n_up + n_purge + 1)
        errors, made = [], []
        mlock = threading.Lock()
        done = threading.Event()

        def uploader(idx, st):
            svc = AuthoringService(st, require_two_person=False)
            try:
                barrier.wait(HANG_GUARD_SECONDS)
                for k in range(ROUNDS):
                    gate.enter()
                    try:
                        out = svc.ingest_material(AUTHOR, f"qs-up{idx}", "form.txt", _material(k), accept_phi_warning=True)
                    finally:
                        gate.leave()
                    d = st.load_draft(out["draft_id"])
                    with mlock:
                        made.append((out["draft_id"], d["source"]["source_hash"]))
                    # 초안이 참조하는 원본은 이 시점에도 이후에도 있어야 한다
                    assert st.get_source(d["source"]["source_hash"]) is not None, "참조된 원본이 사라졌다"
            except BaseException as e:   # noqa: BLE001 - 스레드 안의 실패를 모아 주 스레드에서 단언한다
                errors.append(repr(e)[:300])

        def purger(st):
            try:
                barrier.wait(HANG_GUARD_SECONDS)
                while not done.is_set():
                    purge_once(st)
            except BaseException as e:   # noqa: BLE001
                errors.append(repr(e)[:300])

        def aging():
            try:
                barrier.wait(HANG_GUARD_SECONDS)
                while not done.is_set():
                    gate.age(ager, done)
            except BaseException as e:   # noqa: BLE001
                errors.append(repr(e)[:300])

        ths = [threading.Thread(target=uploader, args=(i, st)) for i, st in enumerate(ups)]
        bg = [threading.Thread(target=purger, args=(st,)) for st in purgers] + [threading.Thread(target=aging)]
        for t in ths + bg:
            t.start()
        for t in ths:
            t.join(HANG_GUARD_SECONDS)
        gate.finish(done)
        for t in bg:
            t.join(HANG_GUARD_SECONDS)
        assert not any(t.is_alive() for t in ths + bg), "스레드가 끝나지 않았다(교착 의심)"
        assert not errors, errors
        assert len(made) == n_up * ROUNDS
        _check_drafts_have_sources(ager, made)
        for s in stores:
            s.close()


def test_pg_purge_vs_edit_of_same_draft_never_loses_the_edit(validators):
    """R2: 오래된 OPEN 초안을 purge가 지우려는 순간 편집(새 updated_at 저장)이 겹친다. 어느 순서로 끝나도 편집된 초안은 남아 있어야 한다."""
    with pg_schema() as (url, schema):
        sp, se = PostgresQuestionStore(url, schema=schema), PostgresQuestionStore(url, schema=schema)
        svc = AuthoringService(se, require_two_person=False)
        for r in range(ROUNDS * 2):
            d = svc.create_draft(AUTHOR, f"qs-e{r}")
            d["updated_at"] = _old()
            se.save_draft(d)
            barrier = threading.Barrier(2)
            errs = []

            def edit():
                try:
                    barrier.wait(HANG_GUARD_SECONDS)
                    svc._touch(dict(d))   # 편집: 같은 draft_id, 새 updated_at(= 서비스의 모든 편집이 하는 저장)
                except BaseException as e:   # noqa: BLE001
                    errs.append(repr(e)[:300])

            def purge():
                try:
                    barrier.wait(HANG_GUARD_SECONDS)
                    purge_once(sp)
                except BaseException as e:   # noqa: BLE001
                    errs.append(repr(e)[:300])
            ts = [threading.Thread(target=edit), threading.Thread(target=purge)]
            [t.start() for t in ts]
            [t.join(HANG_GUARD_SECONDS) for t in ts]
            assert not errs, errs
            got = se.load_draft(d["draft_id"])
            assert got is not None and got["updated_at"] > _old(1), f"라운드 {r}: 편집된 초안이 사라졌거나 옛 값으로 남았다"
        sp.close(); se.close()


_PURGER = (
    "import os,sys\n"
    "from question_authoring.__main__ import purge_once\n"
    "from question_authoring.wiring.stores import PostgresQuestionStore\n"
    "s=PostgresQuestionStore(os.environ['TEST_DATABASE_URL'], schema=sys.argv[1])\n"
    "for _ in range(int(sys.argv[2])): purge_once(s)\n"
    "s.close()\n")


def test_pg_upload_vs_purge_in_separate_processes(validators):
    """R4: purge가 별도 프로세스(다른 연결·다른 파이썬)다. 프로세스 안 RLock이 아니라 DB의 advisory lock만으로 안전해야 한다."""
    with pg_schema() as (url, schema):
        st, ager = PostgresQuestionStore(url, schema=schema), PostgresQuestionStore(url, schema=schema)
        svc = AuthoringService(st, require_two_person=False)
        procs = [subprocess.Popen([sys.executable, "-c", _PURGER, schema, str(ROUNDS * 2)],
                                  env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)),   # 설치 여부와 무관하게 같은 코드를 읽는다
                                 
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        made = []
        k = 0
        try:
            # 프로세스가 끝날 때까지(= purge가 정해진 횟수를 다 돌 때까지) 업로드한다. 고정 시간을 기다리지 않는다
            while any(p.poll() is None for p in procs) or k < 5:
                # 나이 올리기는 업로드 사이에만 한다: 이 스레드 하나가 번갈아 하므로 업로드 틈과 겹치지 않는다. purge는 별도 프로세스라 자유롭게 겹친다
                ager._write(lambda c: c.execute("UPDATE blobs SET created_at=%s", (_old(),)))
                out = svc.ingest_material(AUTHOR, "qs-proc", "form.txt", _material(k), accept_phi_warning=True)
                made.append((out["draft_id"], st.load_draft(out["draft_id"])["source"]["source_hash"]))
                k += 1
                assert k < 5000, "purge 프로세스가 끝나지 않는다"
        finally:
            results = [(p.wait(HANG_GUARD_SECONDS), p.stderr.read().decode(errors="replace")[-300:]) for p in procs]
        assert all(rc == 0 for rc, _ in results), results
        _check_drafts_have_sources(ager, made)
        st.close(); ager.close()


def test_pg_confirm_of_stale_draft_vs_purge_keeps_provenance(validators):
    """R5: 보존 기간을 넘겨 방치된 초안을 확인하는 순간 purge가 겹친다. 버전이 만들어졌다면 그 버전이 가리키는 원본은 있어야 한다."""
    with pg_schema() as (url, schema):
        sp, se = PostgresQuestionStore(url, schema=schema), PostgresQuestionStore(url, schema=schema)
        svc = AuthoringService(se, require_two_person=False)
        outcomes = {"confirmed": 0, "refused": 0}
        for r in range(ROUNDS * 2):
            out = svc.ingest_material(AUTHOR, f"qs-c{r}", "form.txt", example_text())
            did = out["draft_id"]
            d = se.load_draft(did)
            h = d["source"]["source_hash"]
            ph = svc.get_preview(did)["preview_hash"]
            d["updated_at"] = _old()
            se.save_draft(d)
            se._write(lambda c: c.execute("UPDATE blobs SET created_at=%s", (_old(),)))
            barrier = threading.Barrier(2)
            res = {}

            def confirm():
                try:
                    barrier.wait(HANG_GUARD_SECONDS)
                    res["ref"] = svc.confirm(AUTHOR, did, ph)
                except Exception as e:   # noqa: BLE001 - 지워진 초안이면 NOT_FOUND 등으로 거절되는 것은 허용
                    res["err"] = getattr(e, "code", type(e).__name__)

            def purge():
                try:
                    barrier.wait(HANG_GUARD_SECONDS)
                    purge_once(sp)
                except BaseException as e:   # noqa: BLE001
                    res["perr"] = repr(e)[:300]
            ts = [threading.Thread(target=confirm), threading.Thread(target=purge)]
            [t.start() for t in ts]
            [t.join(HANG_GUARD_SECONDS) for t in ts]
            assert "perr" not in res, res
            if "ref" in res:
                outcomes["confirmed"] += 1
                src = se._version_source_hashes()
                assert h in src and se.get_source(h) is not None, f"라운드 {r}: 확정된 버전의 원본이 사라졌다"
            else:
                outcomes["refused"] += 1
                assert res.get("err") in {"NOT_FOUND", "FAILED_PRECONDITION"}, res
        assert sum(outcomes.values()) == ROUNDS * 2
        sp.close(); se.close()


def test_pg_confirm_interleaved_with_purge_at_the_worst_point(validators):
    """R5를 결정적으로: confirm이 초안을 읽은 직후(최신 버전을 보는 시점)에 purge가 전부 실행된 것처럼 끼워 넣는다(고정 sleep 없음).
    방치된 초안이 확인되는 중이면 purge가 초안과 원본을 지워도, 확정된 버전이 가리키는 원본은 남아 있어야 한다."""
    with pg_schema() as (url, schema):
        sp, se = PostgresQuestionStore(url, schema=schema), PostgresQuestionStore(url, schema=schema)
        svc = AuthoringService(se, require_two_person=False)
        out = svc.ingest_material(AUTHOR, "qs-w", "form.txt", example_text())
        did = out["draft_id"]
        d = se.load_draft(did)
        h = d["source"]["source_hash"]
        ph = svc.get_preview(did)["preview_hash"]
        d["updated_at"] = _old()
        se.save_draft(d)
        se._write(lambda c: c.execute("UPDATE blobs SET created_at=%s", (_old(),)))
        real, fired = svc._latest_ref, []

        def hooked(qsid):
            if not fired:
                fired.append(purge_once(sp))
            return real(qsid)
        svc._latest_ref = hooked
        try:
            ref = svc.confirm(AUTHOR, did, ph)
        except Exception as e:   # noqa: BLE001 - 거절은 허용(아래에서 거절 이유를 본다)
            assert getattr(e, "code", "") in {"NOT_FOUND", "FAILED_PRECONDITION"}, repr(e)
            ref = None
        assert fired, "끼워 넣기가 실행되지 않았다"
        assert ref is None and fired[0]["sources_removed"] == 1, fired   # 원본이 정리된 뒤의 확인은 거절된다(출처 없는 버전을 만들지 않는다)
        assert se._version_source_hashes() == set()
        se.put_source(h, "form.txt", example_text())                      # 같은 자료를 다시 올리면 같은 해시라 예약된 확인이 이어진다
        ref = svc.confirm(AUTHOR, did, ph)
        assert h in se._version_source_hashes() and se.get_source(h) is not None
        assert purge_once(sp)["sources_removed"] == 0                    # 버전이 참조하면 오래돼도 지우지 않는다
        assert se.get_source(h) is not None
        sp.close(); se.close()
