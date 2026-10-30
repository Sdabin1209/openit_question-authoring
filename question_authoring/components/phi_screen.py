"""환자 정보 경고: 올린 자료에 환자 정보로 보이는 패턴이 있는지 살핀다(경고 전용, 판단은 저자). 입력 PhiScreenInput -> 출력 PhiScreenResult."""
from __future__ import annotations

import base64
import re
import unicodedata

from boundary_contracts.errors import BoundaryError

INPUT_SCHEMA = "PhiScreenInput"
OUTPUT_SCHEMA = "PhiScreenResult"
NON_UTF8_WARNING = "UTF-8 텍스트가 아니라 환자 정보를 검사하지 못했다"
# 환자 정보로 보이는 패턴(경고 전용, 판단은 저자가 한다). 놓치는 것보다 거짓 경고가 낫다(D88 U5). 모두 길이를 묶거나 숫자 경계만 써서 선형 시간이다.
# 근거: 이전 패턴이 한글에 붙은 번호, 구분자 없는 번호, 공백으로 나눈 전화, +82, 유선을 놓쳤다(전수 점검 P5). 병록번호·이름·주소는 패턴이 없어 탐지하지 못한다(한계).
_SEP = r"[-\s.\u2010-\u2015\u2212\uff0d]?"     # 하이픈 종류(전각 포함), 공백, 점 중 하나 또는 없음
# 앞뒤가 숫자가 아닌 날짜만 지운다: YYYY-MM-DD, YYYY.MM.DD, YYYY/MM/DD, 8자리 YYYYMMDD. 13자리 번호의 앞부분(예 2001011234567)은 뒤에 숫자가 이어져 지워지지 않는다
# 숫자(또는 숫자+구분자 하나) 바로 뒤의 날짜꼴은 지우지 않는다: 010-20120101, +82 10 20120101, 02-20120101 같은 전화번호의 뒷부분이다(라운드3 M2). 두 lookbehind는 고정 폭이라 선형 시간이다
_DATE = re.compile(r"(?<!\d)(?<!\d\))(?<!\d[-\s.\u2010-\u2015\u2212\uff0d])(?:(?:19|20)\d{2}[-./](?:0?[1-9]|1[0-2])[-./](?:0?[1-9]|[12]\d|3[01])|(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01]))(?!\d)")
_PHI = [("주민등록번호 형태", re.compile(rf"(?<!\d)\d{{2}}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01]){_SEP}[1-8]\d{{6}}(?!\d)")),
        ("전화번호 형태", re.compile(rf"(?<!\d)(?:\+?82{_SEP}0?{_SEP}1[016789]|01[016789])\)?{_SEP}\d{{3,4}}{_SEP}\d{{4}}(?!\d)")),
        ("유선전화 형태", re.compile(rf"(?<!\d)(?:0(?:2|[3-6]\d)|\+?82{_SEP}0?{_SEP}(?:2|[3-6]\d))\)?{_SEP}\d{{3,4}}{_SEP}\d{{4}}(?!\d)")),
        ("이메일 형태", re.compile(r"[\w.+-]{1,64}@[\w-]{1,63}\.[\w.]{1,63}"))]



def run(inp: dict) -> dict:
    res = _screen(inp)
    if res["warnings"] and not inp["accept_warning"]:
        raise BoundaryError("FAILED_PRECONDITION", "환자 정보로 보이는 내용이 있다. 확인 후 다시 올려야 한다", {"warnings": res["warnings"]})
    return res


def _screen(inp: dict) -> dict:
    data = base64.b64decode(inp["data_base64"])
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # UTF-8이 아니면 정규식으로 살필 수 없다. 조용히 통과시키지 않고 경고한다(UTF-16 등은 바이트가 달라 패턴이 맞지 않는다)
        return {"warnings": [NON_UTF8_WARNING]}
    text = unicodedata.normalize("NFKC", text)   # 전각 숫자·하이픈을 반각으로
    text = _DATE.sub("날짜", text)              # 날짜 바로 뒤 숫자를 전화번호로 오인하지 않게(경고 피로 줄이기, 라운드2 점검)
    return {"warnings": [name for name, rx in _PHI if rx.search(text)]}
