#!/usr/bin/env bash
# 스모크 시험: 이미지를 빌드하고, 컨테이너를 띄워 /healthz, /readyz, 등록-확인 흐름을 확인한다.
# 실행 파일 이름은 DOCKER 환경 변수로만 바꿀 수 있다(기본 docker).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
DOCKER="${DOCKER:-docker}"
"$DOCKER" info >/dev/null 2>&1 || { echo "오류: $DOCKER 데몬에 연결할 수 없다. 데몬이 실행 중이고 이 사용자가 접근할 수 있는지 확인하라." >&2; exit 1; }
IMG=question-authoring:smoke
NAME=question-authoring-smoke-$$
TOKEN=smoke-token
"$here/scripts/build_wheels.sh"
"$DOCKER" build -q -t "$IMG" \
  --build-arg COMMIT="$(git -C "$here" rev-parse --short HEAD)" \
  --build-arg BOUNDARY_TAG="$(cat "$here/build/contracts.tag")" \
  --build-arg SURVEY_SCHEMA_TAG="$(cat "$here/build/survey-schema.tag")" "$here" >/dev/null
SECDIR="$(mktemp -d)"
trap '"$DOCKER" rm -fv "$NAME" >/dev/null 2>&1 || true; rm -rf "$SECDIR"' EXIT
# 비밀은 파일로 주입한다(D84): NAME_FILE. 환경 변수에는 경로만 있다.
printf '%s' "$TOKEN:smoke-author:question-author" > "$SECDIR/author_tokens"
printf '%s' "smoke=read-token" > "$SECDIR/read_tokens"
chmod 755 "$SECDIR"; chmod 644 "$SECDIR"/*
"$DOCKER" run -d --name "$NAME" -p 127.0.0.1::8080 -v "$SECDIR:/run/secrets:ro" -e QA_AUTHOR_TOKENS_FILE=/run/secrets/author_tokens -e QA_READ_TOKENS_FILE=/run/secrets/read_tokens "$IMG" >/dev/null
PORT=$("$DOCKER" port "$NAME" 8080/tcp | head -1 | sed 's/.*://')
echo "호스트 포트(Docker 배정): $PORT"
for i in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://127.0.0.1:$PORT/healthz"; echo
curl -fsS "http://127.0.0.1:$PORT/readyz"; echo
[ "$("$DOCKER" exec "$NAME" id -u)" != "0" ] || { echo "root로 실행 중"; exit 1; }
if "$DOCKER" inspect "$NAME" | grep -q -e "$TOKEN" -e read-token || "$DOCKER" exec "$NAME" env | grep -q -e "$TOKEN" -e read-token; then echo "비밀이 env/inspect에 노출됨"; exit 1; fi
echo "비밀 비노출 확인(inspect, exec env)"
AUTH="Authorization: Bearer $TOKEN"
did=$(curl -fsS -X POST -H "$AUTH" --data-binary @"$here/examples/question_form.txt" \
  "http://127.0.0.1:$PORT/v1/materials?question_set_id=qs-smoke&filename=form.txt" | python3 -c 'import sys,json;print(json.load(sys.stdin)["draft_id"])')
ph=$(curl -fsS -H "$AUTH" "http://127.0.0.1:$PORT/v1/drafts/$did/preview" | python3 -c 'import sys,json;d=json.load(sys.stdin);assert d["ready_to_confirm"];print(d["preview_hash"])')
curl -fsS -X POST -H "$AUTH" -d "{\"preview_hash\":\"$ph\"}" "http://127.0.0.1:$PORT/v1/drafts/$did/confirm"; echo
code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/v1/drafts")
[ "$code" = "403" ] || { echo "인증 없는 요청이 거부되지 않음: $code"; exit 1; }
curl -fsS -H "$AUTH" "http://127.0.0.1:$PORT/v1/signals" | grep -q DISABLED || { echo "신호 비활성 응답 실패"; exit 1; }
RT="Authorization: Bearer read-token"
curl -fsS -H "$RT" "http://127.0.0.1:$PORT/v1/question-sets" | grep -q '"qs-smoke"' || { echo "list_ids 실패"; exit 1; }
curl -fsS -H "$RT" "http://127.0.0.1:$PORT/v1/question-sets/qs-smoke/latest?reviewed_only=true" | grep -q '"version": "1"' || { echo "latest 실패"; exit 1; }
curl -fsS -H "$RT" "http://127.0.0.1:$PORT/v1/question-sets/qs-smoke/versions/1" | python3 -c 'import sys,json;assert json.load(sys.stdin)["version"]==1'
code=$(curl -s -o /dev/null -w '%{http_code}' -H "$RT" "http://127.0.0.1:$PORT/v1/drafts")
[ "$code" = "403" ] || { echo "읽기 토큰이 저작 경로에서 거부되지 않음: $code"; exit 1; }
for i in $(seq 1 20); do
  st=$("$DOCKER" inspect -f '{{.State.Health.Status}}' "$NAME"); [ "$st" = healthy ] && break; sleep 2
done
echo "HEALTHCHECK: $st"; [ "$st" = healthy ] || exit 1
"$DOCKER" image inspect "$IMG" --format '{{json .Config.Labels}}'
echo "SMOKE OK"
