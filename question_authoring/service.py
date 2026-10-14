"""저작 API의 핵심 로직. HTTP 계층(http_api.py)은 이것을 얇게 감싼다.

초안(가변) -> 미리보기 -> 저자 확인 -> 불변 버전. 문항 저장소에 쓰는 길은 이 서비스뿐이다.
모든 실패는 BoundaryError다.
"""
from __future__ import annotations

import copy
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError

from . import classify, preview as pv
from .extractors import ExtractionFailure, ExtractorRegistry, default_registry
from .store.base import AuthoringStore, bank_hash
from .validation import PIPELINE_SCHEMA_VERSION, Validators, semantic_errors

EXT_KEY = "question-authoring"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
# 환자 정보가 섞였을 가능성을 알리는 단순 패턴(경고 전용. 판단은 저자가 한다)
_PHI = [("주민등록번호 형태", re.compile(r"\b\d{6}-[1-4]\d{6}\b")),
        ("전화번호 형태", re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b")),
        ("이메일 형태", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"))]


@dataclass(frozen=True)
class Actor:
    id: str
    role: str


def _now_default() -> datetime:
    return datetime.now(timezone.utc)


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id_default(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def _unescape(p: str) -> str:
    return p.replace("~1", "/").replace("~0", "~")


def _walk(root: Any, tokens: list[str], create: bool):
    cur = root
    for t in tokens:
        if isinstance(cur, list):
            try:
                cur = cur[int(t)]
            except (ValueError, IndexError):
                raise BoundaryError("INVALID_RECORD", f"경로를 찾을 수 없다: {t}")
        elif isinstance(cur, dict):
            if t not in cur:
                if create:
                    cur[t] = {}
                else:
                    raise BoundaryError("INVALID_RECORD", f"경로를 찾을 수 없다: {t}")
            cur = cur[t]
        else:
            raise BoundaryError("INVALID_RECORD", f"경로를 찾을 수 없다: {t}")
    return cur


def apply_edit(bank: dict, edit: dict) -> None:
    """edit = {op: 'set'|'remove'|'append', path: '/questions/0/...', value?}. 경로는 JSON 포인터."""
    op, path = edit.get("op"), edit.get("path")
    if op not in ("set", "remove", "append") or not isinstance(path, str) or not path.startswith("/"):
        raise BoundaryError("INVALID_RECORD", "편집 항목 형식이 아니다")
    tokens = [_unescape(t) for t in path[1:].split("/")]
    parent = _walk(bank, tokens[:-1], create=(op == "set"))
    last = tokens[-1]
    if op == "append":
        target = parent.get(last) if isinstance(parent, dict) else None
        if not isinstance(target, list):
            raise BoundaryError("INVALID_RECORD", f"목록이 아니다: {path}")
        target.append(copy.deepcopy(edit.get("value")))
    elif op == "set":
        if isinstance(parent, list):
            try:
                parent[int(last)] = copy.deepcopy(edit.get("value"))
            except (ValueError, IndexError):
                raise BoundaryError("INVALID_RECORD", f"경로를 찾을 수 없다: {path}")
        elif isinstance(parent, dict):
            parent[last] = copy.deepcopy(edit.get("value"))
        else:
            raise BoundaryError("INVALID_RECORD", f"경로를 찾을 수 없다: {path}")
    else:
        try:
            if isinstance(parent, list):
                del parent[int(last)]
            else:
                del parent[last]
        except (KeyError, ValueError, IndexError, TypeError):
            raise BoundaryError("INVALID_RECORD", f"경로를 찾을 수 없다: {path}")


class AuthoringService:
    def __init__(self, store: AuthoringStore, validators: Validators, *,
                 extractors: Optional[ExtractorRegistry] = None,
                 author_roles: Iterable[str] = ("question-author",),
                 clock: Callable[[], datetime] = _now_default,
                 new_id: Callable[[str], str] = _new_id_default):
        self.store = store
        self.v = validators
        self.extractors = extractors or default_registry()
        self.author_roles = frozenset(author_roles)
        self._clock = clock
        self._new_id = new_id

    # ---------- 공통 ----------
    def _auth(self, actor: Optional[Actor]) -> Actor:
        if actor is None or actor.role not in self.author_roles:
            raise BoundaryError("PERMISSION_DENIED", "저작 권한이 없다")
        if not _ID.match(actor.id or ""):
            raise BoundaryError("INVALID_RECORD", "actor.id 형식이 아니다")
        return actor

    @staticmethod
    def _check_id(name: str, value: str) -> None:
        if not isinstance(value, str) or not _ID.match(value):
            raise BoundaryError("INVALID_RECORD", f"{name} 형식이 아니다: {value!r}")

    def _draft(self, draft_id: str) -> dict:
        d = self.store.load_draft(draft_id)
        if d is None:
            raise BoundaryError("NOT_FOUND", f"초안이 없다: {draft_id}")
        return d

    def _open_draft(self, draft_id: str) -> dict:
        d = self._draft(draft_id)
        if d["status"] != "OPEN":
            raise BoundaryError("FAILED_PRECONDITION", f"이미 {d['status']} 상태의 초안이다")
        return d

    def _touch(self, d: dict) -> None:
        d["updated_at"] = _ts(self._clock())
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

    @staticmethod
    def _strip_version(bank: dict) -> dict:
        b = copy.deepcopy(bank)
        b.pop("version", None)
        return b

    # ---------- 초안 ----------
    def create_draft(self, actor: Actor, question_set_id: str, *, base_version: Optional[str] = "latest") -> dict:
        self._auth(actor)
        self._check_id("question_set_id", question_set_id)
        ref, bank = self._base(question_set_id, base_version)
        d = {
            "draft_id": self._new_id("dr"), "question_set_id": question_set_id, "status": "OPEN",
            "base": ref, "bank": self._strip_version(bank) if bank else {"questions": []},
            "flags": [], "source": None, "fallback": None, "warnings": [], "created_by": actor.id,
            "created_at": _ts(self._clock()), "updated_at": _ts(self._clock()),
        }
        self.store.save_draft(d)
        return d

    def get_draft(self, draft_id: str) -> dict:
        return self._draft(draft_id)

    def list_drafts(self, question_set_id: Optional[str] = None) -> list[dict]:
        return self.store.list_drafts(question_set_id)

    def apply_edits(self, actor: Actor, draft_id: str, edits: list[dict]) -> dict:
        """화면 입력(템플릿 양식)의 저장 형태. 경로를 고치면 그 아래 플래그는 저자가 직접 값을 정한 것으로 닫힌다."""
        self._auth(actor)
        d = self._open_draft(draft_id)
        new_bank = copy.deepcopy(d["bank"])
        for e in edits:
            apply_edit(new_bank, e)
        for e in edits:
            for f in d["flags"]:
                if not f["resolved"] and (f["path"] == e["path"] or f["path"].startswith(e["path"] + "/")):
                    f["resolved"], f["resolved_by"], f["resolution"] = True, actor.id, "EDITED"
        d["bank"] = new_bank
        self._touch(d)
        return d

    def acknowledge_flags(self, actor: Actor, draft_id: str, flag_ids: list[str]) -> dict:
        """추출된 값이 맞다고 저자가 직접 확인한다."""
        self._auth(actor)
        d = self._open_draft(draft_id)
        by_id = {f["flag_id"]: f for f in d["flags"]}
        for fid in flag_ids:
            if fid not in by_id:
                raise BoundaryError("NOT_FOUND", f"플래그가 없다: {fid}")
        for fid in flag_ids:
            f = by_id[fid]
            if not f["resolved"]:
                f["resolved"], f["resolved_by"], f["resolution"] = True, actor.id, "ACKNOWLEDGED"
        self._touch(d)
        return d

    def discard_draft(self, actor: Actor, draft_id: str, reason: str) -> dict:
        """확인하지 않고 버린다. 버전이 아니므로 ReviewRecord는 만들지 않는다."""
        self._auth(actor)
        d = self._open_draft(draft_id)
        d["status"], d["discard_reason"] = "DISCARDED", reason
        self._touch(d)
        return d

    # ---------- 문항 자료 등록 ----------
    def ingest_material(self, actor: Actor, question_set_id: str, filename: str, data: bytes, *,
                        base_version: Optional[str] = "latest", extractor: Optional[str] = None,
                        accept_phi_warning: bool = False) -> dict:
        """원본을 추출기로 읽어 초안을 만든다. 읽지 못하면 PARSED가 아니라 FALLBACK 초안(화면 입력으로 이어 가기)을 돌려준다."""
        self._auth(actor)
        self._check_id("question_set_id", question_set_id)
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise BoundaryError("INVALID_RECORD", "자료가 비어 있다")
        warnings = self._phi_warnings(bytes(data))
        if warnings and not accept_phi_warning:
            raise BoundaryError("FAILED_PRECONDITION", "환자 정보로 보이는 내용이 있다. 확인 후 다시 올려야 한다",
                                {"warnings": warnings})
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "material"
            p.write_bytes(data)
            source_hash = canonical.file_hash(p)
        self.store.put_source(source_hash, filename, bytes(data))
        ref, base_bank = self._base(question_set_id, base_version)
        d = self.create_draft(actor, question_set_id, base_version=base_version)
        d["warnings"] = warnings
        d["source"] = {"kind": "material", "source_hash": source_hash, "filename": filename, "size": len(data)}
        try:
            ex = self.extractors.select(filename, bytes(data), extractor)
            d["source"].update(extractor=ex.name, extractor_version=ex.version)
            res = ex.extract(bytes(data), filename)
        except ExtractionFailure as e:
            d["source"].update(extractor=d["source"].get("extractor"))
            d["fallback"] = {"reason": str(e),
                             "guidance": "자료를 읽지 못했다. 화면 입력으로 이어서 작성할 수 있다. 원본 해시는 출처로 남는다."}
            self._touch(d)
            return {"draft_id": d["draft_id"], "status": "FALLBACK", "reason": str(e), "warnings": warnings}
        except Exception as e:  # 추출기 버그도 등록을 막지 않는다
            d["fallback"] = {"reason": f"추출기 오류: {type(e).__name__}", "guidance": "화면 입력으로 이어서 작성할 수 있다."}
            self._touch(d)
            return {"draft_id": d["draft_id"], "status": "FALLBACK", "reason": d["fallback"]["reason"], "warnings": warnings}
        d["bank"] = res.bank
        flags = []
        seen: dict[str, int] = {}
        for f in res.flags:
            base_id = "fl-" + canonical.sha256_hash([f.path, f.kind, f.message, f.raw])[7:19]
            n = seen.get(base_id, 0)
            seen[base_id] = n + 1
            flags.append({"flag_id": base_id if n == 0 else f"{base_id}-{n}", "path": f.path, "kind": f.kind,
                          "message": f.message, "raw": f.raw, "resolved": False})
        d["flags"] = flags
        self._touch(d)
        return {"draft_id": d["draft_id"], "status": "PARSED", "open_flags": len(flags), "warnings": warnings}

    @staticmethod
    def _phi_warnings(data: bytes) -> list[str]:
        text = data.decode("utf-8", errors="ignore")
        return [name for name, rx in _PHI if rx.search(text)]

    # ---------- 미리보기 ----------
    def _validation_errors(self, d: dict) -> list[str]:
        candidate = copy.deepcopy(d["bank"])
        candidate["version"] = 0   # 번호는 확인 때 정해진다. 나머지 구조만 검사한다.
        return self.v.errors("QuestionBank", candidate) + semantic_errors(candidate)

    def get_preview(self, draft_id: str) -> dict:
        d = self._draft(draft_id)
        base_bank = None
        if d["base"]:
            base_bank = self.store.get_question_bank(d["base"])
        errors = self._validation_errors(d)
        open_flags = [f for f in d["flags"] if not f["resolved"]]
        change = None
        if base_bank is not None and not errors:
            change = pv.change_summary(base_bank, d["bank"])
        return {
            "draft_id": d["draft_id"], "status": d["status"], "preview_hash": pv.preview_hash(d),
            "text": pv.render_text(d), "open_flags": open_flags, "validation_errors": errors,
            "change": change, "warnings": d["warnings"], "fallback": d["fallback"],
            "ready_to_confirm": d["status"] == "OPEN" and not open_flags and not errors
                                and not (change is not None and not change["change_kinds"]),
        }

    # ---------- 확인(저자 확인 = ReviewRecord) ----------
    def confirm(self, actor: Actor, draft_id: str, preview_hash: str) -> dict:
        self._auth(actor)
        d = self._draft(draft_id)
        if d["status"] == "CONFIRMED":
            if d["confirmed"]["preview_hash"] == preview_hash:
                return d["confirmed"]["ref"]            # 멱등
            raise BoundaryError("CONFLICT", "이미 다른 미리보기로 확인된 초안이다")
        if d["status"] != "OPEN":
            raise BoundaryError("FAILED_PRECONDITION", f"{d['status']} 상태의 초안은 확인할 수 없다")
        if pv.preview_hash(d) != preview_hash:
            raise BoundaryError("FAILED_PRECONDITION", "미리보기 이후 초안이 바뀌었다. 미리보기를 다시 확인해야 한다")
        open_flags = [f for f in d["flags"] if not f["resolved"]]
        if open_flags:
            raise BoundaryError("FAILED_PRECONDITION", f"확인하지 않은 항목이 {len(open_flags)}건 있다",
                                {"flag_ids": [f["flag_id"] for f in open_flags]})
        qsid = d["question_set_id"]
        latest = self._latest_ref(qsid)
        if not d.get("confirming") and latest != d["base"]:
            raise BoundaryError("FAILED_PRECONDITION", "초안을 만든 뒤 새 버전이 생겼다. 최신 버전에서 다시 시작해야 한다")
        # 번호와 시각은 처음 시도에서 예약해 재시도해도 같은 레코드가 나오게 한다
        if not d.get("confirming"):
            d["confirming"] = {"version": (int(latest["version"]) + 1) if latest else 1, "at": _ts(self._clock())}
            self.store.save_draft(d)
        n, at = d["confirming"]["version"], d["confirming"]["at"]
        bank = copy.deepcopy(d["bank"])
        bank["version"] = n
        errs = self.v.errors("QuestionBank", bank) + semantic_errors(bank)
        if errs:
            raise BoundaryError("INVALID_RECORD", f"QuestionBank 검증 실패: {errs[0]}", {"errors": errs[:20]})
        ref = {"id": qsid, "version": str(n), "content_hash": bank_hash(bank)}
        base_bank = self.store.get_question_bank(d["base"]) if d["base"] else None
        if base_bank is not None and not classify.diff_banks(base_bank, bank)[0]:
            d.pop("confirming", None)
            self.store.save_draft(d)
            raise BoundaryError("FAILED_PRECONDITION", "이전 버전과 내용이 같다. 새 버전을 만들지 않는다")
        change = classify.classify_change(base_bank, bank, to_ref=ref, from_ref=d["base"], created_at=at,
                                          pipeline_schema_version=PIPELINE_SCHEMA_VERSION)
        if change is None:
            pass
        else:
            change = {k: v for k, v in change.items() if v is not None}
            change["ext"] = {EXT_KEY: {"draft_id": draft_id}}
            self.v.check("ChangeClassification", change)
        source = d["source"] or {"kind": "screen"}
        review = {
            "pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "review_id": "rv-" + draft_id,
            "question_set": ref, "reviewer_role": actor.role, "reviewer_id": actor.id,
            "decision": "APPROVED", "clinical_basis_approved": True, "reviewed_at": at,
            "ext": {EXT_KEY: {
                "kind": "AUTHOR_CONFIRMATION", "draft_id": draft_id, "preview_hash": preview_hash,
                "source": source,
                "acknowledged_flags": [f["flag_id"] for f in d["flags"] if f.get("resolution") == "ACKNOWLEDGED"],
                "edited_flags": [f["flag_id"] for f in d["flags"] if f.get("resolution") == "EDITED"],
            }},
        }
        self._check_id("review_id", review["review_id"])
        self.v.check("ReviewRecord", review)
        self.store.commit_version(bank, ref, review, change, source)
        d["status"], d["confirmed"] = "CONFIRMED", {"ref": ref, "preview_hash": preview_hash, "review_id": review["review_id"]}
        self._touch(d)
        return ref

    # ---------- 이후 검수 ----------
    def add_review(self, actor: Actor, ref: dict, *, decision: str, clinical_basis_approved: bool,
                   reason: Optional[str] = None, review_id: Optional[str] = None) -> dict:
        """만들어진 버전에 검수를 덧붙인다(두 번째 검수자 또는 승인 철회). 이전 검수는 지우지 않는다."""
        self._auth(actor)
        self.store.get_question_bank(ref)  # 존재와 무결성
        rec = {
            "pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "review_id": review_id or self._new_id("rv"),
            "question_set": ref, "reviewer_role": actor.role, "reviewer_id": actor.id, "decision": decision,
            "clinical_basis_approved": bool(clinical_basis_approved), "reviewed_at": _ts(self._clock()),
            "ext": {EXT_KEY: {"kind": "FOLLOW_UP_REVIEW"}},
        }
        if reason:
            rec["reason"] = reason
        self.v.check("ReviewRecord", rec)
        if review_id:  # 재시도 멱등: 같은 review_id에 같은 판단이면 기존 것을 돌려준다
            for old in self.store.get_reviews(ref):
                if old["review_id"] == review_id:
                    keys = ("reviewer_role", "reviewer_id", "decision", "clinical_basis_approved", "reason")
                    if all(old.get(k) == rec.get(k) for k in keys):
                        return old
                    raise BoundaryError("CONFLICT", f"같은 review_id에 다른 내용: {review_id}")
        self.store.add_review(rec)
        return rec
