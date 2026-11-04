#!/usr/bin/env bash
# 개발용 이미지 빌드 한 곳(스모크·브라우저 시험이 같이 쓴다): scripts/build-image.sh(계약 BuildImageRequest)를 부른다.
# 사용: build-local-image.sh <이미지 태그> [결과 JSON 파일]. 요청의 버전은 잠금 파일의 핀이고, 라이브러리 소스 트리는 그 태그에서 꺼낸다
# (배포에서는 deploy가 하는 일을 개발용으로 대신한다. 이 한 곳만 이웃 리포를 읽는다). 의존 설치용 GIT_CONFIG_GLOBAL 은 호출자가 정한다.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
DOCKER="${DOCKER:-docker}"
IMG="$1"; OUT="${2:-/dev/null}"
PY="${PYTHON:-$here/.venv/bin/python}"
BCV="$(sed -n 's/^boundary-contracts @ .*@\(v[0-9.]*\)$/\1/p' "$here/requirements.lock")"
SST="$(sed -n 's/^openit-survey-schema @ .*@\(survey-schema-v[0-9.]*\)#.*$/\1/p' "$here/requirements.lock")"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir "$T/bc" "$T/ss"
git -C "$here/../boundary-contracts" archive "$BCV" | tar -x -C "$T/bc"
git -C "$here/../survey-service" archive "$SST" schema | tar -x -C "$T/ss"
cat > "$T/request.json" <<EOF
{"source_dir": "$here", "revision": "$(git -C "$here" rev-parse --short HEAD)",
 "versions": {"boundary": "$BCV", "survey_schema": "${SST#survey-schema-}"},
 "dependency_sources": {"boundary": "$T/bc", "survey_schema": "$T/ss/schema"}, "image_tag": "$IMG"}
EOF
DOCKER="$DOCKER" PYTHON="$PY" bash "$here/scripts/build-image.sh" "$T/request.json" | tail -1 | tee "$OUT"
