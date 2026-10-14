#!/usr/bin/env bash
# 스모크 시험: 이미지를 빌드하고, 컨테이너를 띄워 /healthz, /readyz, 등록-확인 흐름을 확인한다.
# WSL에서 docker가 없으면 DOCKER=docker.exe 로 실행한다.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
DOCKER="${DOCKER:-docker}"
IMG=question-authoring:smoke
NAME=question-authoring-smoke-$$
TOKEN=smoke-token
"$here/scripts/build_contract_wheel.sh"
"$DOCKER" build -q -t "$IMG" \
  --build-arg COMMIT="$(git -C "$here" rev-parse --short HEAD)" \
  --build-arg CONTRACT_VERSION="$(cat "$here/build/survey-schema/CONTRACT_VERSION")" \
  --build-arg BOUNDARY_CONTRACTS_REV="$(cat "$here/build/contracts.rev")" "$here" >/dev/null
trap '"$DOCKER" rm -fv "$NAME" >/dev/null 2>&1 || true' EXIT
"$DOCKER" run -d --name "$NAME" -p 127.0.0.1::8080 -e QA_AUTHOR_TOKENS="$TOKEN:smoke-author:question-author" "$IMG" >/dev/null
PORT=$("$DOCKER" port "$NAME" 8080/tcp | head -1 | sed 's/.*://' | tr -d '\r')
echo "호스트 포트(Docker 배정): $PORT"
for i in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://127.0.0.1:$PORT/healthz"; echo
curl -fsS "http://127.0.0.1:$PORT/readyz"; echo
[ "$("$DOCKER" exec "$NAME" id -u)" != "0" ] || { echo "root로 실행 중"; exit 1; }
AUTH="Authorization: Bearer $TOKEN"
did=$(curl -fsS -X POST -H "$AUTH" --data-binary @"$here/examples/question_form.txt" \
  "http://127.0.0.1:$PORT/v1/materials?question_set_id=qs-smoke&filename=form.txt" | python3 -c 'import sys,json;print(json.load(sys.stdin)["draft_id"])')
ph=$(curl -fsS -H "$AUTH" "http://127.0.0.1:$PORT/v1/drafts/$did/preview" | python3 -c 'import sys,json;d=json.load(sys.stdin);assert d["ready_to_confirm"];print(d["preview_hash"])')
curl -fsS -X POST -H "$AUTH" -d "{\"preview_hash\":\"$ph\"}" "http://127.0.0.1:$PORT/v1/drafts/$did/confirm"; echo
code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/v1/drafts")
[ "$code" = "403" ] || { echo "인증 없는 요청이 거부되지 않음: $code"; exit 1; }
"$DOCKER" image inspect "$IMG" --format '{{json .Config.Labels}}'
echo "SMOKE OK"
