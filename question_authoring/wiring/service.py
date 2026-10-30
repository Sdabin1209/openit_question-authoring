"""저작 서비스: 컴포넌트를 이어 저작 흐름(초안 -> 미리보기 -> 저자 확인 -> 불변 버전)을 만든다. 업무 규칙은 컴포넌트에 있고 여기에는 두지 않는다.

여기가 하는 일: 저장소 읽기·쓰기 순서, 초안별 락, 컴포넌트 출력을 다음 컴포넌트 입력으로 넘기기. 문항 저장소에 쓰는 길은 이 서비스뿐이다.
모든 실패는 BoundaryError다.
"""
from __future__ import annotations

import base64
import copy
import tempfile
import threading
from pathlib import Path
from typing import Callable, Iterable, Optional

import boundary_contracts
from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError

from ..components import (access_policy, bank_validate, change_diff, change_record, confirm_finalize, confirm_plan,
                          draft_discard, draft_edit, draft_ingest, draft_open, extract_textform, flag_acknowledge,
                          phi_screen, preview_build, preview_hash, review_followup, signals_build)
from ..components.link_authoring_server import Actor
from ..components.link_failures_read import FailureReader
from ..components.review_followup import REVIEWER_ROLE
from . import schemas
from .ids import new_id_default, now_default, ts

PIPELINE_SCHEMA_VERSION = boundary_contracts.PIPELINE_SCHEMA_VERSION
EXT_KEY = "question-authoring"
NON_UTF8_WARNING = phi_screen.NON_UTF8_WARNING
__all__ = ["Actor", "AuthoringService", "REVIEWER_ROLE", "PIPELINE_SCHEMA_VERSION", "NON_UTF8_WARNING"]

# 추출기 레지스트리: 형식마다 추출기 컴포넌트 하나(D33). 이름 -> 컴포넌트 모듈. 모듈은 NAME, VERSION, can_handle, run을 낸다
DEFAULT_EXTRACTORS = (extract_textform,)


def _d(actor: Actor) -> dict:
    return {"id": actor.id, "role": actor.role}


class AuthoringService:
    def __init__(self, store, *, extractors: Iterable = DEFAULT_EXTRACTORS,
                 author_roles: Iterable[str] = ("question-author",),
                 require_two_person: bool = True,
                 clock: Callable = now_default,
                 new_id: Callable[[str], str] = new_id_default):
        self.store = store
        self.extractors = list(extractors)
        self.author_roles = frozenset(author_roles)
        # D88 U9: 임상 근거 승인은 저자와 다른 주체(검수 역할)만 한다. False는 이전 동작(저자 확인이 곧 승인)이고 /readyz에 경고가 뜬다
        self.require_two_person = bool(require_two_person)
        self._draft_locks: dict[str, threading.RLock] = {}
        self._draft_locks_guard = threading.Lock()
        self._clock = clock
        self._new_id = new_id
        b = schemas.bind
        self._access, self._phi, self._open, self._ingest = b(access_policy), b(phi_screen), b(draft_open), b(draft_ingest)
        self._edit, self._ack, self._discard = b(draft_edit), b(flag_acknowledge), b(draft_discard)
        self._bank_validate, self._diff, self._record = b(bank_validate), b(change_diff), b(change_record)
        self._phash, self._preview = b(preview_hash), b(preview_build)
        self._plan, self._finalize, self._followup = b(confirm_plan), b(confirm_finalize), b(review_followup)
        self._signals = b(signals_build)

    # ---------- 공통 ----------
    def now(self) -> str:
        return ts(self._clock())

    def check(self, name: str, value) -> None:
        """계약 레코드와 문진 스키마 QuestionBank 검사(읽기 면의 응답 자기 검사에 쓴다)."""
        if name == "QuestionBank":
            errs = bank_validate.question_bank_errors(value)
            if errs:
                raise BoundaryError("INVALID_RECORD", f"{name} 스키마 위반: {errs[0]}", {"errors": errs[:20]})
        else:
            schemas.check(name, value)

    def _authorize(self, actor: Optional[Actor], *, reviewer_ok: bool = False, owner: Optional[str] = None) -> Actor:
        self._access({"actor": _d(actor) if actor is not None else None, "allowed_roles": sorted(self.author_roles),
                      "reviewer_role": REVIEWER_ROLE, "reviewer_ok": reviewer_ok,
                      "require_two_person": self.require_two_person, "owner_id": owner})
        return actor

    def require_author(self, actor: Optional[Actor]) -> Actor:
        return self._authorize(actor)

    def require_reader(self, actor: Optional[Actor]) -> Actor:
        """초안 조회·개선 신호 읽기: 저자와 (4-eyes 모드의) 검수자."""
        return self._authorize(actor, reviewer_ok=True)

    def _dlock(self, draft_id: str) -> threading.RLock:
        with self._draft_locks_guard:
            return self._draft_locks.setdefault(draft_id, threading.RLock())

    def _draft(self, draft_id: str) -> dict:
        d = self.store.load_draft(draft_id)
        if d is None:
            raise BoundaryError("NOT_FOUND", f"초안이 없다: {draft_id}")
        return d

    def _save(self, d: dict) -> None:
        self.store.save_draft(d)

    def _touch(self, d: dict) -> None:
        """저장 시각을 갱신해 저장한다(서비스의 모든 편집이 하는 저장). 경쟁 시험이 편집을 흉내 낼 때 쓴다."""
        d["updated_at"] = self.now()
        self.store.save_draft(d)

    def _latest_ref(self, qsid: str) -> Optional[dict]:
        try:
            return self.store.latest(qsid)
        except BoundaryError as e:
            if e.code == "NOT_FOUND":
                return None
            raise

    def _base(self, qsid: str, base_version: Optional[str]) -> tuple[Optional[dict], Optional[dict]]:
        """(기반 ref, 기반 bank). base_version이 None이면 새 문항 세트(기반 없음)로, 'latest'면 최신으로 시작한다."""
        if base_version is None:
            return None, None
        if base_version == "latest":
            ref = self._latest_ref(qsid)
            if ref is None:
                return None, None
        else:
            rows = dict((r["version"], r) for r in self.store.list_versions(qsid, limit=1000)[0])
            if base_version not in rows:
                raise BoundaryError("NOT_FOUND", f"기반 버전이 없다: {qsid}@{base_version}")
            ref = rows[base_version]
        return ref, self.store.get_question_bank(ref)

    # ---------- 초안 ----------
    def create_draft(self, actor: Actor, question_set_id: str, *, base_version: Optional[str] = "latest") -> dict:
        self._authorize(actor)
        schemas.check("QuestionSetId", question_set_id)
        ref, bank = self._base(question_set_id, base_version)
        d = self._open({"draft_id": self._new_id("dr"), "question_set_id": question_set_id, "actor_id": actor.id,
                        "now": self.now(), "base": ref, "base_bank": bank})
        self._save(d)
        return d

    def get_draft(self, draft_id: str) -> dict:
        return self._draft(draft_id)

    def list_drafts(self, question_set_id: Optional[str] = None) -> list[dict]:
        return self.store.list_drafts(question_set_id)

    def apply_edits(self, actor: Actor, draft_id: str, edits: list[dict]) -> dict:
        """화면 입력(템플릿 양식)의 저장 형태."""
        self._authorize(actor)
        schemas.check("EditList", edits)
        with self._dlock(draft_id):
            d = self._draft(draft_id)
            self._authorize(actor, owner=d["created_by"])
            d = self._edit({"draft": d, "edits": edits, "actor_id": actor.id, "now": self.now()})
            self._save(d)
            return d

    def acknowledge_flags(self, actor: Actor, draft_id: str, flag_ids: list[str]) -> dict:
        """추출된 값이 맞다고 저자가 직접 확인한다."""
        self._authorize(actor)
        schemas.check("FlagIdList", flag_ids)
        with self._dlock(draft_id):
            d = self._draft(draft_id)
            self._authorize(actor, owner=d["created_by"])
            d = self._ack({"draft": d, "flag_ids": flag_ids, "actor_id": actor.id, "now": self.now()})
            self._save(d)
            return d

    def discard_draft(self, actor: Actor, draft_id: str, reason: str) -> dict:
        self._authorize(actor)
        with self._dlock(draft_id):
            schemas.check("DiscardReason", reason)
            d = self._draft(draft_id)
            self._authorize(actor, owner=d["created_by"])
            d = self._discard({"draft": d, "reason": reason, "actor_id": actor.id, "now": self.now()})
            self._save(d)
            return d

    # ---------- 문항 자료 등록 ----------
    def _select(self, filename: str, data: bytes, name: Optional[str]):
        """(추출기 모듈 | None, 사유 | None). 이름이 있으면 그것, 없으면 형식(확장자)이 맞는 첫 추출기."""
        if name is not None:
            for e in self.extractors:
                if e.NAME == name:
                    return e, None
            return None, f"알 수 없는 추출기: {name}"
        for e in self.extractors:
            if e.can_handle(filename):
                return e, None
        return None, f"지원하지 않는 형식이다: {filename}"

    def ingest_material(self, actor: Actor, question_set_id: str, filename: str, data: bytes, *,
                        base_version: Optional[str] = "latest", extractor: Optional[str] = None,
                        accept_phi_warning: bool = False) -> dict:
        """원본을 추출기로 읽어 초안을 만든다. 읽지 못하면 PARSED가 아니라 FALLBACK 초안(화면 입력으로 이어 가기)을 돌려준다."""
        self._authorize(actor)
        schemas.check("MaterialUpload", {"question_set_id": question_set_id, "filename": filename,
                                         "size": len(data) if isinstance(data, (bytes, bytearray)) else -1})
        warnings = self._phi({"data_base64": base64.b64encode(bytes(data)).decode("ascii"),
                              "accept_warning": bool(accept_phi_warning)})["warnings"]
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "material"
            p.write_bytes(bytes(data))
            source_hash = canonical.file_hash(p)
        self.store.put_source(source_hash, filename, bytes(data))
        d = self.create_draft(actor, question_set_id, base_version=base_version)
        ex, why = self._select(filename, bytes(data), extractor)
        if ex is None:
            extraction = {"status": "FAILED", "reason": why, "flags": []}
        else:
            try:
                extraction = schemas.bind(ex)({"filename": filename, "data_base64": base64.b64encode(bytes(data)).decode("ascii")})
            except Exception as e:  # 추출기 버그도 등록을 막지 않는다
                extraction = {"status": "FAILED", "reason": f"추출기 오류: {type(e).__name__}", "flags": []}
        res = self._ingest({
            "draft": d, "warnings": warnings, "now": self.now(),
            "source": {"source_hash": source_hash, "filename": filename, "size": len(data)},
            "extractor": ({"name": ex.NAME, "version": ex.VERSION} if ex is not None else None),
            "extraction": extraction,
            "guidance": ("화면 입력으로 이어서 작성할 수 있다." if extraction.get("reason", "").startswith("추출기 오류") else None)})
        self._save(res["draft"])
        out = {"draft_id": d["draft_id"], "status": res["status"], "warnings": warnings}
        if res["status"] == "PARSED":
            out["open_flags"] = res["open_flags"]
        else:
            out["reason"] = res["reason"]
        return out

    # ---------- 미리보기 ----------
    def _validation_errors(self, d: dict, version: Optional[int]) -> list[str]:
        return self._bank_validate({"bank": d["bank"], "version": version})["errors"]

    def _hash(self, d: dict) -> str:
        return self._phash({"question_set_id": d["question_set_id"], "base": d.get("base"), "bank": d["bank"],
                            "flags": d["flags"]})["preview_hash"]

    def get_preview(self, draft_id: str) -> dict:
        d = self._draft(draft_id)
        base_bank = self.store.get_question_bank(d["base"]) if d["base"] else None
        errors = self._validation_errors(d, 0)   # 번호는 확인 때 정해진다. 나머지 구조만 검사한다
        change = None
        if base_bank is not None and not errors:
            change = self._diff({"old": base_bank, "new": d["bank"]})
        return self._preview({"draft": d, "preview_hash": self._hash(d), "validation_errors": errors, "change": change})

    # ---------- 확인(저자 확인 = ReviewRecord) ----------
    def confirm(self, actor: Actor, draft_id: str, preview_hash: str) -> dict:
        self._authorize(actor)
        schemas.check("PreviewHashValue", preview_hash)
        with self._dlock(draft_id):
            return self._confirm(actor, draft_id, preview_hash)

    def _confirm(self, actor: Actor, draft_id: str, preview_hash: str) -> dict:
        d = self._draft(draft_id)
        self._authorize(actor, owner=d["created_by"])
        qsid = d["question_set_id"]
        plan = self._plan({"draft": d, "preview_hash": preview_hash, "current_preview_hash": self._hash(d),
                           "latest": self._latest_ref(qsid), "now": self.now()})
        if plan["action"] == "REPLAY":
            return plan["ref"]
        n, at = plan["reservation"]["version"], plan["reservation"]["at"]
        if plan["reserved_now"]:
            # 번호와 시각은 처음 시도에서 예약해 재시도해도 같은 레코드가 나오게 한다
            d["confirming"] = plan["reservation"]
            self.store.save_draft(d)
        bank = copy.deepcopy(d["bank"])
        bank["version"] = n
        errs = self._validation_errors({"bank": bank}, None)
        ref = {"id": qsid, "version": str(n), "content_hash": canonical.sha256_hash(bank)}
        base_bank = self.store.get_question_bank(d["base"]) if d["base"] else None
        diff = self._diff({"old": base_bank, "new": bank}) if base_bank is not None and not errs else None
        change = None
        if diff is not None and diff["change_kinds"]:
            change = self._record({"diff": diff, "to": ref, "from": d["base"], "created_at": at, "draft_id": draft_id,
                                   "pipeline_schema_version": PIPELINE_SCHEMA_VERSION})
        try:
            fin = self._finalize({"draft": d, "actor": _d(actor), "preview_hash": preview_hash, "validation_errors": errs,
                                  "ref": ref, "diff": diff, "at": at, "require_two_person": self.require_two_person,
                                  "pipeline_schema_version": PIPELINE_SCHEMA_VERSION})
        except BoundaryError as e:
            if e.code == "FAILED_PRECONDITION":     # 내용이 같다: 예약을 풀어 이 초안이 영영 막히지 않게 한다
                d.pop("confirming", None)
                self.store.save_draft(d)
            raise
        review = fin["review"]
        try:
            self.store.commit_version(bank, ref, review, change, d["source"] or {"kind": "screen"})
        except BoundaryError as e:
            if e.code == "CONFLICT" and d.get("confirming"):
                # 다른 초안이 같은 번호를 먼저 썼다. 예약을 풀어야 이 초안이 영영 막히지 않는다(전수 점검 A7)
                d.pop("confirming", None)
                self.store.save_draft(d)
                raise BoundaryError("FAILED_PRECONDITION", "다른 초안이 먼저 확정되어 번호 예약을 풀었다. 최신 버전에서 다시 시작해야 한다") from None
            raise
        d["status"], d["confirmed"] = "CONFIRMED", {"ref": ref, "preview_hash": preview_hash, "review_id": review["review_id"]}
        d["updated_at"] = self.now()
        self._save(d)
        return ref

    # ---------- 이후 검수 ----------
    def add_review(self, actor: Actor, ref: dict, *, decision: str, clinical_basis_approved: bool,
                   reason: Optional[str] = None, review_id: Optional[str] = None) -> dict:
        """만들어진 버전에 검수를 덧붙인다(두 번째 검수자 또는 승인 철회). 이전 검수는 지우지 않는다."""
        self._authorize(actor, reviewer_ok=True)
        self.store.get_question_bank(ref)  # 존재와 무결성
        res = self._followup({
            "actor": _d(actor), "require_two_person": self.require_two_person, "ref": ref, "decision": decision,
            "clinical_basis_approved": clinical_basis_approved, "reason": reason, "review_id": review_id,
            "new_review_id": self._new_id("rv"), "existing_reviews": self.store.get_reviews(ref),
            "now": self.now(), "pipeline_schema_version": PIPELINE_SCHEMA_VERSION})
        if res["action"] == "STORE":
            self.store.add_review(res["review"])
        return res["review"]

    # ---------- 개선 신호(읽기 전용) ----------
    def build_signals(self, source, *, question_set_id: Optional[str] = None, review_status: Optional[str] = None,
                      max_cases: int = signals_build.DEFAULT_MAX_CASES, deadline_seconds: Optional[float] = None,
                      confusion_min: int = signals_build.CONFUSION_MIN) -> dict:
        bundle = schemas.bind(FailureReader(source))({
            "max_cases": max_cases, "review_status": review_status, "question_set_id": question_set_id,
            "deadline_seconds": deadline_seconds})
        return self._signals({"bundle": bundle, "question_set_id": question_set_id, "confusion_min": confusion_min,
                              "now": self.now()})
