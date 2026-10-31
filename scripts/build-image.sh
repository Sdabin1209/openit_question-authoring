#!/usr/bin/env bash
# 영역 빌드 진입점(계약 BuildImageRequest/BuildImageResult, boundary-contracts 2.5.0, U42). 사용: bash scripts/build-image.sh <요청 JSON 파일>
# 표준 출력의 마지막 줄이 BuildImageResult JSON 한 줄이다. 진행 기록은 표준 오류. DOCKER(없으면 docker), PYTHON(없으면 리포의 .venv/bin/python, boundary-contracts>=2.5.0과 jsonschema가 있어야 한다).
# 왜 이 모양인지: docs/adr/0019-영역-빌드-진입점.md
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ $# -eq 1 ] || { echo "사용: bash scripts/build-image.sh <요청 JSON 파일>" >&2; exit 2; }
DOCKER="${DOCKER:-docker}"
# 해석기 기본은 영역 자신의 환경(scripts/setup-env.sh가 만든 리포의 .venv). 왜: 계약 패키지가 거기에 설치된다. PYTHON으로 바꿀 수 있다
if [ -n "${PYTHON:-}" ]; then PY="$PYTHON"; elif [ -x "$here/../.venv/bin/python" ]; then PY="$here/../.venv/bin/python"; else
  echo "build-image: 해석기를 찾지 못했다. bash scripts/setup-env.sh 로 .venv를 만들거나 PYTHON을 지정하라(boundary-contracts 2.5 이상과 jsonschema 필요)" >&2; exit 2; fi
# 요청 해석 결과(셸 대입문)는 먼저 변수로 받아 종료 코드를 확인한다: eval "$(...)"는 실패를 삼켜 다음 줄에서 엉뚱한 오류로 끝난다
parsed="$("$PY" "$here/_build_request.py" "$1")" || { echo "build-image: 요청을 해석하지 못했다(위 메시지 참고)" >&2; exit 2; }
eval "$parsed"      # SRC REVISION BOUNDARY_VERSION SURVEY_MAJOR IMAGE_TAG BOUNDARY_SRC SURVEY_SRC
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
ctx="$tmp/ctx"; mkdir -p "$ctx/build/wheels"
# 빌드 컨텍스트는 source_dir 안의 이 영역 소스뿐이다(.git, 가상환경, 이전 빌드 산출물 제외). source_dir은 고치지 않는다.
tar -C "$SRC" --exclude=.git --exclude=.venv --exclude=build --exclude=__pycache__ --exclude='*.egg-info' --exclude=.pytest_cache -cf - . | tar -C "$ctx" -xf -
# 같은 요청이 같은 이미지가 되도록 wheel 안의 시각을 소스 커밋 시각으로 고정한다(없으면 0)
epoch="$(git -C "$SRC" show -s --format=%ct "$REVISION" 2>/dev/null || echo 0)"
for lib in "$BOUNDARY_SRC" "$SURVEY_SRC"; do
  w="$tmp/w$(basename "$lib")"; cp -r "$lib" "$w"          # 원본 트리에 빌드 흔적을 남기지 않는다
  mkdir -p "$tmp/out-$(basename "$lib")"
  SOURCE_DATE_EPOCH="$epoch" "$PY" -m pip wheel -q --no-deps -w "$tmp/out-$(basename "$lib")" "$w" >&2
  cp "$tmp/out-$(basename "$lib")"/*.whl "$ctx/build/wheels/"
done
# 통신 라이브러리(openit-link, D118)는 계약 BuildImageRequest에 dependency_sources 키가 아직 없어(CR-0008) requirements.lock의 고정 줄로 호스트에서 wheel을 만든다.
# 왜 호스트: 이미지 안에서는 ssh를 쓸 수 없다. OPENIT_LINK_SPEC은 시험이 가짜 트리를 넘기는 자리다. 이 wheel도 위와 같은 시각 고정을 쓴다
link_spec="${OPENIT_LINK_SPEC:-$(grep '^openit-link @' "$SRC/requirements.lock" 2>/dev/null || true)}"
[ -n "$link_spec" ] || { echo "build-image: requirements.lock에 openit-link 고정 줄이 없다" >&2; exit 2; }
mkdir -p "$tmp/out-link"
SOURCE_DATE_EPOCH="$epoch" "$PY" -m pip wheel -q --no-deps -w "$tmp/out-link" "$link_spec" >&2 || { echo "build-image: openit-link wheel을 만들지 못했다($link_spec)" >&2; exit 2; }
cp "$tmp/out-link"/*.whl "$ctx/build/wheels/"
# 라벨 정직성(U50): 라벨 값은 요청 문자열이 아니라 이미지에 실제로 설치되는 wheel의 버전에서 찍는다.
# 요청 버전, wheel 버전, 이 영역의 고정(requirements.lock)이 하나라도 다르면 라벨을 찍지 않고 실패한다.
wheel_version() { local f; f="$(ls "$ctx/build/wheels/"$1-*.whl 2>/dev/null | head -1)"; [ -n "$f" ] || { echo "build-image: wheel을 찾지 못했다: $1" >&2; exit 2; }; basename "$f" | cut -d- -f2; }
ACTUAL_BOUNDARY="v$(wheel_version boundary_contracts)"
ACTUAL_SURVEY="$(wheel_version openit_survey_schema)"                 # 예: 2.0.0
lock_boundary="$(sed -n 's/^boundary-contracts @ .*@\(v[0-9][0-9.]*\)$/\1/p' "$SRC/requirements.lock" 2>/dev/null || true)"
lock_survey="$(sed -n 's/^openit-survey-schema @ .*@survey-schema-\(v[0-9][0-9.]*\)#.*$/\1/p' "$SRC/requirements.lock" 2>/dev/null || true)"
mismatch() { echo "build-image: $1 버전이 서로 다르다(요청 $2, 설치될 wheel $3, requirements.lock ${4:-없음}). 라벨이 거짓이 되므로 빌드하지 않는다" >&2; exit 2; }
[ "$ACTUAL_BOUNDARY" = "$BOUNDARY_VERSION" ] && [ "$ACTUAL_BOUNDARY" = "$lock_boundary" ] || mismatch "boundary-contracts" "$BOUNDARY_VERSION" "$ACTUAL_BOUNDARY" "$lock_boundary"
[ "v$ACTUAL_SURVEY" = "$SURVEY_VERSION" ] && [ "v$ACTUAL_SURVEY" = "$lock_survey" ] || mismatch "문진 스키마" "$SURVEY_VERSION" "v$ACTUAL_SURVEY" "$lock_survey"
BOUNDARY_VERSION="$ACTUAL_BOUNDARY"; SURVEY_MAJOR="${ACTUAL_SURVEY%%.*}"      # 라벨 값: 계약은 v 접두사 전체, 문진 스키마는 주 버전
# 레이어에 파일 시각이 들어가므로 컨텍스트 전체의 시각도 같은 값으로 맞춘다
find "$ctx" -exec touch -h -d "@$epoch" {} +
# 노출 선언(계약 Exposure)은 리포의 deploy-exposure.json이 정본이고 이미지 라벨 io.openit.exposure에 compact JSON으로 단다(ADR 0021)
[ -f "$ctx/deploy-exposure.json" ] || { echo "build-image: deploy-exposure.json이 없다(노출 선언은 영역이 낸다)" >&2; exit 2; }
exposure="$("$PY" -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1], encoding="utf-8")), ensure_ascii=False, separators=(",", ":")))' "$ctx/deploy-exposure.json")" \
  || { echo "build-image: deploy-exposure.json을 읽지 못했다" >&2; exit 2; }
# 잡 선언(계약 JobsDeclaration)은 리포의 deploy-jobs.json이 정본이고 이미지 라벨 io.openit.jobs에 compact JSON으로 단다. 내보내기 전에 계약으로 검증한다(R7): 어기면 라벨을 달지 않고 실패한다
[ -f "$ctx/deploy-jobs.json" ] || { echo "build-image: deploy-jobs.json이 없다(잡 선언은 영역이 낸다)" >&2; exit 2; }
jobs="$("$PY" -c '
import json, sys
import boundary_contracts
d = json.load(open(sys.argv[1], encoding="utf-8"))
errs = [e.message for e in boundary_contracts.validator("JobsDeclaration").iter_errors(d)]
if errs:
    sys.stderr.write("JobsDeclaration 위반: " + errs[0][:200] + "\n"); sys.exit(1)
print(json.dumps(d, ensure_ascii=False, separators=(",", ":")))' "$ctx/deploy-jobs.json")" \
  || { echo "build-image: deploy-jobs.json이 계약 JobsDeclaration을 어기거나 읽을 수 없다" >&2; exit 2; }
echo "build-image: $IMAGE_TAG 빌드 (revision $REVISION, 계약 $BOUNDARY_VERSION, 문진 스키마 주 버전 $SURVEY_MAJOR)" >&2
"$DOCKER" build -q -t "$IMAGE_TAG" --label "io.openit.exposure=$exposure" --label "io.openit.jobs=$jobs" --build-arg COMMIT="$REVISION" --build-arg BOUNDARY_TAG="$BOUNDARY_VERSION" \
  --build-arg SURVEY_SCHEMA_TAG="$SURVEY_MAJOR" --build-arg SOURCE_DATE_EPOCH="$epoch" --provenance=false --sbom=false "$ctx" >&2
# 라벨은 요청 값이 아니라 이미지에 실제로 붙은 것을 읽어 낸다
id="$("$DOCKER" image inspect --format '{{.Id}}' "$IMAGE_TAG")"
"$DOCKER" image inspect --format '{{json .Config.Labels}}' "$IMAGE_TAG" | "$PY" -c '
import json, sys
labels = {k: v for k, v in json.load(sys.stdin).items() if k.startswith("io.openit.")}
print(json.dumps({"image": sys.argv[1], "image_id": sys.argv[2], "labels": labels}, ensure_ascii=False, separators=(",", ":")))' "$IMAGE_TAG" "$id"
