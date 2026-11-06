#!/usr/bin/env bash
# boundary-contracts와 openit-survey-schema를 태그 시점 소스(git archive)로 꺼내 wheel을 만든다. 산출물은 build/(커밋하지 않음).
# docker 안에서는 ssh가 어려우므로 호스트에서 만들어 이미지에 설치한다. 태그는 requirements.lock과 같아야 한다.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
. "$here/scripts/dep-repo.sh"   # 의존 리포 위치(환경 변수 → 옆 폴더 → 원격 태그 캐시)
BC_TAG="${CONTRACTS_TAG:-v3.6.3}"
SS_TAG="${SURVEY_SCHEMA_TAG:-survey-schema-v4.1.1}"
PY="${PYTHON:-python3}"
rm -rf "$here/build"; mkdir -p "$here/build/bc-src" "$here/build/ss-src" "$here/build/wheels"
BC_REPO="$(dep_repo boundary-contracts "$BC_TAG")"
git -C "$BC_REPO" archive "$BC_TAG" | tar -x -C "$here/build/bc-src"
git -C "$BC_REPO" rev-parse "$BC_TAG^{commit}" > "$here/build/contracts.rev"
SS_REPO="$(dep_repo survey-service "$SS_TAG")"
git -C "$SS_REPO" archive "$SS_TAG" schema | tar -x -C "$here/build/ss-src"
git -C "$SS_REPO" rev-parse "$SS_TAG^{commit}" > "$here/build/survey-schema.rev"
"$PY" -m pip wheel -q --no-deps -w "$here/build/wheels" "$here/build/bc-src"
"$PY" -m pip wheel -q --no-deps -w "$here/build/wheels" "$here/build/ss-src/schema"
echo "$BC_TAG" > "$here/build/contracts.tag"; echo "$SS_TAG" > "$here/build/survey-schema.tag"
ls "$here/build/wheels"
