#!/usr/bin/env bash
# boundary-contracts와 openit-survey-schema를 태그 시점 소스(git archive)로 꺼내 wheel을 만든다. 산출물은 build/(커밋하지 않음).
# docker 안에서는 ssh가 어려우므로 호스트에서 만들어 이미지에 설치한다. 태그는 requirements.lock과 같아야 한다.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
BC_TAG="${CONTRACTS_TAG:-v3.1.1}"
SS_TAG="${SURVEY_SCHEMA_TAG:-survey-schema-v2.0.0}"
PY="${PYTHON:-python3}"
rm -rf "$here/build"; mkdir -p "$here/build/bc-src" "$here/build/ss-src" "$here/build/wheels"
git -C "$here/../boundary-contracts" archive "$BC_TAG" | tar -x -C "$here/build/bc-src"
git -C "$here/../boundary-contracts" rev-parse "$BC_TAG^{commit}" > "$here/build/contracts.rev"
git -C "$here/../survey-service" archive "$SS_TAG" schema | tar -x -C "$here/build/ss-src"
git -C "$here/../survey-service" rev-parse "$SS_TAG^{commit}" > "$here/build/survey-schema.rev"
"$PY" -m pip wheel -q --no-deps -w "$here/build/wheels" "$here/build/bc-src"
"$PY" -m pip wheel -q --no-deps -w "$here/build/wheels" "$here/build/ss-src/schema"
echo "$BC_TAG" > "$here/build/contracts.tag"; echo "$SS_TAG" > "$here/build/survey-schema.tag"
ls "$here/build/wheels"
