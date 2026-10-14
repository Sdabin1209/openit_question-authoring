"""텍스트 양식 추출기('문항 양식 텍스트', .txt/.md). 줄 단위 문법이라 규칙 기반이고 결정적이다.

문법은 docs/design.md 7절과 examples/question_form.txt에 있다. 줄의 첫 낱말이 종류를 정하고, 해석하지 못한 줄은
버리지 않고 UNPARSED로 알린다. 수치·범위·환산·위험 기준은 정해진 모양에 정확히 맞을 때만 값으로 읽는다.
"""
from __future__ import annotations

import re
from typing import Optional

from .base import (MISSING, UNPARSED, UNSURE, ExtractedFlag, ExtractionFailure, ExtractionResult)

NUM = r"-?\d+(?:\.\d+)?"
_num_re = re.compile(rf"^{NUM}$")
_Q_HEAD = re.compile(r"^문항\s+(\S+?)\s*:\s*(.+)$")
_S_HEAD = re.compile(r"^슬롯\s+(\S+?)\s*:\s*(필수|선택)\s*$")
_KV = re.compile(r"^([^:：]+?)\s*[:：]\s*(.*)$")


def _split(s: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,，]", s) if x.strip()]


def _n(s: str):
    f = float(s)
    return int(f) if re.fullmatch(r"-?\d+", s) else f


class TextFormExtractor:
    name = "text-form"
    version = "1"
    extensions = (".txt", ".md")

    def can_handle(self, filename: str, data: bytes) -> bool:
        return filename.lower().endswith(self.extensions)

    # ---- 진입 ----
    def extract(self, data: bytes, filename: str) -> ExtractionResult:
        return TextFormExtractor()._run(data)  # 호출마다 새 상태(스레드 안전)

    def _run(self, data: bytes) -> ExtractionResult:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise ExtractionFailure("UTF-8 텍스트가 아니다") from e
        self.flags: list[ExtractedFlag] = []
        self.bank: dict = {"questions": []}
        self.ss: Optional[dict] = None
        self.q: Optional[dict] = None
        self.qi = -1
        self.s: Optional[dict] = None
        self.si = -1
        self.ctx = "top"
        for line in text.splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            self._line(raw)
        if not self.bank["questions"]:
            raise ExtractionFailure("'문항 <ID>: <이름>' 줄이 하나도 없다")
        self._close_slot()
        self._close_question()
        self._close_session()
        if self.ss is not None:
            self.bank["session_settings"] = self.ss
        else:
            self._flag("/session_settings", MISSING, "세션 설정 구역이 없다")
        return ExtractionResult(self.bank, self.flags)

    # ---- 도우미 ----
    def _missing(self, path, message):
        """이미 같은 자리에 UNSURE 플래그가 있으면(읽었으나 확신 못 함) 빠졌다는 플래그를 또 달지 않는다."""
        if not any(f.path == path for f in self.flags):
            self._flag(path, MISSING, message)

    def _flag(self, path, kind, message, raw=None):
        self.flags.append(ExtractedFlag(path, kind, message, raw))

    def _qp(self, *tail):
        return "/questions/%d%s" % (self.qi, "".join("/" + str(t) for t in tail))

    def _sp(self, *tail):
        return self._qp("slots", self.si, *tail)

    def _unparsed(self, raw):
        if self.ctx == "slot":
            path = self._sp()
        elif self.ctx == "question":
            path = self._qp()
        elif self.ctx == "session":
            path = "/session_settings"
        else:
            path = ""
        self._flag(path, UNPARSED, "해석하지 못한 줄이다(자료에서 빠졌다)", raw)

    # ---- 구역 종료: 빠진 필수 항목을 플래그로 ----
    def _close_slot(self):
        s = self.s
        if s is None:
            return
        for key, label in (("reask_sentence", "다시 묻는 문장"), ("required", "필수 여부"),
                           ("yes_no_map", "예아니오 대응(없으면 '예아니오: 없음')"),
                           ("risk_rule", "위험 기준(없으면 '위험 기준: 없음')")):
            if key not in s:
                self._missing(self._sp(key), f"{label}가 자료에 없다")
        self.s = None

    def _close_question(self):
        q = self.q
        if q is None:
            return
        self._close_slot()
        for key, label in (("order", "순서"), ("prerequisite", "선행 조건(없으면 '선행 조건: 없음')"),
                           ("question_sentence", "질문 문장"), ("refusal_explanation", "거절 설명"),
                           ("policy", "정책")):
            if key not in q:
                self._missing(self._qp(key), f"{label}가 자료에 없다")
        if not q["slots"]:
            self._flag(self._qp("slots"), MISSING, "슬롯이 하나도 없다")
        self.q = None

    def _close_session(self):
        ss = self.ss
        if ss is None:
            return
        for key, label in (("risk_phrases", "위험 문구"), ("confirm_limit", "확인 한도"),
                           ("unrecognized_streak_limit", "인식 실패 연속 한도"), ("ask_limit", "질문 횟수 한도")):
            if key not in ss:
                self._missing("/session_settings/" + key, f"{label}가 자료에 없다")

    # ---- 줄 처리 ----
    def _line(self, raw: str):
        if raw == "세션 설정":
            self._close_slot(); self._close_question(); self.q = None
            self.ss = {"fixed_sentences": [], "guide_list": []}
            self.ctx = "session"
            return
        m = _Q_HEAD.match(raw)
        if m:
            self._close_slot(); self._close_question()
            self.q = {"question_id": m.group(1), "name": m.group(2).strip(), "guide_sentences": [],
                      "affected_followups": [], "repeat_keys": [], "slots": []}
            self.bank["questions"].append(self.q)
            self.qi += 1
            self.ctx = "question"
            return
        m = _S_HEAD.match(raw)
        if m and self.q is not None:
            self._close_slot()
            self.s = {"slot_id": m.group(1), "units": [], "choices": [], "required": m.group(2) == "필수"}
            self.q["slots"].append(self.s)
            self.si += 1
            self.ctx = "slot"
            return
        if raw.startswith("슬롯") and self.q is not None:
            self._close_slot()
            self.si = len(self.q["slots"])
            self.q["slots"].append({"slot_id": "", "units": [], "choices": []})
            self.s = None
            self._flag(self._qp("slots", self.si), UNPARSED, "슬롯 줄 형식이 맞지 않는다('슬롯 <ID>: 필수|선택')", raw)
            self.ctx = "question"
            return
        if self.ctx == "session":
            return self._session_line(raw)
        if self.ctx == "slot":
            if self._slot_line(raw):
                return
            # 슬롯 줄이 아니면 문항 줄일 수 있다(들여쓰기에 의존하지 않는다)
            if self._question_line(raw, from_slot=True):
                return
            return self._unparsed(raw)
        if self.ctx == "question":
            if self._question_line(raw):
                return
        self._unparsed(raw)

    def _kv(self, raw):
        m = _KV.match(raw)
        return (m.group(1).strip(), m.group(2).strip()) if m else (None, None)

    def _session_line(self, raw):
        ss = self.ss
        k, v = self._kv(raw)
        if k is None:
            return self._unparsed(raw)
        base = "/session_settings"
        if k == "위험 문구":
            ss["risk_phrases"] = [] if v == "없음" else _split(v)
        elif k in ("확인 한도", "인식 실패 연속 한도", "질문 횟수 한도"):
            key = {"확인 한도": "confirm_limit", "인식 실패 연속 한도": "unrecognized_streak_limit",
                   "질문 횟수 한도": "ask_limit"}[k]
            if _num_re.match(v) and "." not in v:
                ss[key] = _n(v)
            else:
                self._flag(f"{base}/{key}", UNSURE, "정수로 읽지 못했다", raw)
        elif k.startswith("고정 문장 "):
            ss["fixed_sentences"].append({"key": k[len("고정 문장 "):].strip(), "sentence": v})
        elif k.startswith("안내 "):
            parts = [p.strip() for p in v.split("|")]
            gid = k[len("안내 "):].strip()
            idx = len(ss["guide_list"])
            if len(parts) == 4 and parts[1] in ("문항별", "전체") and parts[3] in ("없음", "REMAINING_COUNT"):
                ss["guide_list"].append({"guide_id": gid, "selection_criterion": parts[0], "per_question": parts[1] == "문항별",
                                         "sentence": None if parts[2] == "없음" else parts[2],
                                         "fill_value": None if parts[3] == "없음" else parts[3]})
            else:
                self._flag(f"{base}/guide_list", UNPARSED, "안내 형식이 맞지 않는다('안내 <ID>: 기준 | 문항별/전체 | 문장/없음 | 채움값/없음')", raw)
        else:
            self._unparsed(raw)

    def _question_line(self, raw, from_slot=False) -> bool:
        q = self.q
        if q is None:
            return False
        k, v = self._kv(raw)
        if k is None:
            return False
        if k == "순서":
            if _num_re.match(v) and "." not in v and not v.startswith("-"):
                q["order"] = _n(v)
            else:
                self._flag(self._qp("order"), UNSURE, "0 이상의 정수로 읽지 못했다", raw)
        elif k == "선행 조건":
            if v == "없음":
                q["prerequisite"] = None
            else:
                m = re.match(r"^(\S+?)\.(\S+?)\s*=\s*(\S+)$", v)
                if m:
                    q["prerequisite"] = {"slot": {"question_id": m.group(1), "repeat_key": "", "slot_id": m.group(2)},
                                         "value": m.group(3)}
                else:
                    self._flag(self._qp("prerequisite"), UNSURE, "'<문항>.<슬롯> = <값>' 형식으로 읽지 못했다", raw)
        elif k == "질문":
            q["question_sentence"] = v
        elif k == "거절 설명":
            q["refusal_explanation"] = v
        elif k.startswith("안내 문장 "):
            q["guide_sentences"].append({"guide_id": k[len("안내 문장 "):].strip(), "sentence": v})
        elif k == "후속 문항":
            q["affected_followups"] = _split(v)
        elif k == "반복 키":
            q["repeat_keys"] = _split(v)
        elif k == "정책":
            m = re.match(r"^질문 한도\s*(\d+)\s*[,，]\s*모름\s*(\d+)\s*[,，]\s*거절\s*(\d+)$", v)
            if m:
                q["policy"] = {"question_limit": int(m.group(1)), "unknown_accept_after": int(m.group(2)),
                               "refusal_accept_after": int(m.group(3))}
            else:
                self._flag(self._qp("policy"), UNSURE, "'질문 한도 N, 모름 N, 거절 N' 형식으로 읽지 못했다", raw)
        else:
            return False
        if from_slot:
            self.ctx = "question"
        return True

    def _slot_line(self, raw) -> bool:
        s = self.s
        k, v = self._kv(raw)
        if s is None or k is None:
            return False
        if k == "다시 묻기":
            s["reask_sentence"] = v
        elif k == "단위":
            self._unit(v, raw)
        elif k == "선택지":
            s["choices"] = _split(v)
        elif k.startswith("선택지 코드 "):
            code = k[len("선택지 코드 "):].strip()
            parts = [p.strip() for p in v.split("|")]
            syn: list[str] = []
            for p in parts[1:]:
                if p.startswith("같은 말:"):
                    syn = _split(p[len("같은 말:"):])
            s.setdefault("choice_catalog", []).append({"code": code, "label": parts[0], "synonyms": syn})
        elif k == "예아니오":
            if v == "없음":
                s["yes_no_map"] = None
            else:
                m = re.match(r"^예\s*=\s*(\S+)\s*[,，]\s*아니오\s*=\s*(\S+)$", v)
                if m:
                    s["yes_no_map"] = {"yes": m.group(1), "no": m.group(2)}
                else:
                    self._flag(self._sp("yes_no_map"), UNSURE, "'예=<값>, 아니오=<값>' 형식으로 읽지 못했다", raw)
        elif k == "위험 기준":
            self._risk(v, raw)
        elif k == "필수":
            if v in ("예", "아니오"):
                s["required"] = v == "예"
            else:
                self._flag(self._sp("required"), UNSURE, "'예'/'아니오'로 읽지 못했다", raw)
        else:
            return False
        return True

    def _unit(self, v: str, raw: str):
        s = self.s
        parts = [p.strip() for p in v.split("|")]
        name = parts[0]
        ui = len(s["units"])
        up = self._sp("units", ui)
        unit = {"name": name, "synonyms": [], "range": None, "convert_to": None}
        s["units"].append(unit)
        if not name:
            self._flag(up, UNSURE, "단위 이름이 비어 있다", raw)
            unit["name"] = ""
        for p in parts[1:]:
            if p.startswith("같은 말:"):
                unit["synonyms"] = _split(p[len("같은 말:"):])
            elif p.startswith("범위"):
                m = re.match(rf"^범위\s*({NUM})\s*~\s*({NUM})$", p)
                if m and float(m.group(1)) <= float(m.group(2)):
                    unit["range"] = [_n(m.group(1)), _n(m.group(2))]
                else:
                    self._flag(up + "/range", UNSURE, "범위를 '범위 최소 ~ 최대' 형식으로 읽지 못했다. 값을 비워 두었다", p)
            elif p.startswith("환산"):
                body = p[len("환산"):].lstrip(": ").strip()
                m = re.match(rf"^({NUM})\s+(\S+)$", body)
                if m and float(m.group(1)) > 0:
                    unit["convert_to"] = {"unit": m.group(2), "factor": _n(m.group(1))}
                elif body == "없음":
                    pass
                else:
                    self._flag(up + "/convert_to", UNSURE, "환산을 '환산: <배수> <대상 단위>'로 읽지 못했다. 값을 비워 두었다", p)
            else:
                self._flag(up, UNSURE, "해석하지 못한 단위 항목이다", p)

    def _risk(self, v: str, raw: str):
        s = self.s
        rp = self._sp("risk_rule")
        if v == "없음":
            s["risk_rule"] = None
            return
        v2 = v.replace("≥", ">=")
        m = re.match(r"^(>=|=)\s*(.+?)\s+(즉시|결과 시)$", v2)
        if not m:
            self._flag(rp, UNSURE, "위험 기준을 '>= 값 단위 즉시' 또는 '= 값 결과 시' 형식으로 읽지 못했다. 값을 비워 두었다", raw)
            return
        op, val, when = m.groups()
        response = "IMMEDIATE" if when == "즉시" else "ON_RESULT"
        mm = re.match(rf"^({NUM})\s+(\S+)$", val)
        if mm:
            unit_names = {u["name"] for u in s["units"]}
            value = {"amount": _n(mm.group(1)), "unit": mm.group(2)}
            s["risk_rule"] = {"operator": op, "value": value, "response": response}
            if mm.group(2) not in unit_names:
                self._flag(rp + "/value/unit", UNSURE, f"위험 기준의 단위 '{mm.group(2)}'가 이 슬롯의 단위 목록에 없다", raw)
        elif re.match(rf"^{NUM}$", val):
            self._flag(rp, UNSURE, "위험 기준에 단위가 없다. 값을 비워 두었다", raw)
        else:
            s["risk_rule"] = {"operator": op, "value": val, "response": response}
            known = set(s.get("choices", [])) | {c["code"] for c in s.get("choice_catalog", [])} \
                | set((s.get("yes_no_map") or {}).values())
            if val not in known:
                self._flag(rp + "/value", UNSURE, f"위험 기준의 값 '{val}'이 이 슬롯의 선택지에 없다", raw)
