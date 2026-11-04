#!/usr/bin/env bash
# 스모크 시험: 이미지를 빌드하고, 컨테이너를 띄워 /healthz, /readyz, 등록-확인 흐름을 확인한다.
# SMOKE_DB=postgres면 임시 docker 네트워크에 postgres:16을 띄워 PostgreSQL 경로로 시험한다(기본은 SQLite 경로).
# 실행 파일 이름은 DOCKER 환경 변수로만 바꿀 수 있다(기본 docker).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
POSTGRES_IMAGE="$(. "$here/images.env"; printf %s "$POSTGRES_IMAGE")"   # digest 고정(images.env)
DOCKER="${DOCKER:-docker}"
"$DOCKER" info >/dev/null 2>&1 || { echo "오류: $DOCKER 데몬에 연결할 수 없다. 데몬이 실행 중이고 이 사용자가 접근할 수 있는지 확인하라." >&2; exit 1; }
IMG=question-authoring:smoke-$$   # 왜 $$: 스모크가 동시에 돌면 고정 태그를 서로 덮어쓰고 정리가 남의 이미지를 지웠다(병렬 재현). 실행마다 고유 태그, 정리는 자기 태그만
NAME=question-authoring-smoke-$$
TOKEN=smoke-token
# 빌드는 scripts/build-local-image.sh(영역 진입점 build-image.sh, 계약 BuildImageRequest)로 한다. 버전은 잠금 파일의 핀이다.
BUILDTMP="$(mktemp -d)"
bash "$here/scripts/build-local-image.sh" "$IMG" "$BUILDTMP/result.json"
SECDIR="$(mktemp -d)"
SMOKE_DB="${SMOKE_DB:-sqlite}"
NET=question-authoring-smoke-net-$$; PGNAME=question-authoring-smoke-pg-$$; CURLC=question-authoring-smoke-curl-$$
# 왜 호스트 포트를 거치지 않나: Docker Desktop 의 호스트 포트 전달은 부하 직후 연결을 거부·지연시켜 스모크가 간헐 실패했다(전체 시험 바로 뒤 재현).
# 표준 해법은 Docker 의 컨테이너 네트워크 모드이다: 앱 컨테이너와 네트워크 이름공간을 공유하는 짧은 확인 컨테이너(공식 curl 이미지)에서 루프백으로 부른다.
CURL_IMAGE=curlimages/curl:8.10.1
cu() { "$DOCKER" exec "$CURLC" curl "$@"; }           # 확인 컨테이너 안의 curl
cud() { "$DOCKER" exec -i "$CURLC" curl "$@"; }        # 표준 입력으로 본문을 주는 호출(--data-binary @-)
BASE="http://127.0.0.1:8080"      # 확인 컨테이너는 앱 컨테이너의 네트워크 이름공간에 붙는다(--network container:<이름>): 평문은 루프백만(RUNTIME R1.1), 호스트 포트도 거치지 않는다
# 왜 정리 전에 출력하나: 병렬 실행 중 한 번 실패했을 때 컨테이너가 이미 지워져 원인을 볼 수 없었다. 어느 단계에서든 실패(종료 코드 0이 아님)하면 컨테이너를 지우기 전에
# 로그 마지막 100줄과 상태(ExitCode, OOMKilled 등)를 낸다. inspect는 State만 낸다(전체를 내면 환경 변수가 섞인다).
on_exit() {
  rc=$?
  if [ "$rc" -ne 0 ]; then
    for c in "$NAME" "$PGNAME" "$CURLC"; do
      if "$DOCKER" inspect "$c" >/dev/null 2>&1; then
        echo "=== 실패(종료 코드 $rc): 컨테이너 $c 진단 ===" >&2
        "$DOCKER" inspect -f 'status={{.State.Status}} ExitCode={{.State.ExitCode}} OOMKilled={{.State.OOMKilled}} Error={{.State.Error}} StartedAt={{.State.StartedAt}} FinishedAt={{.State.FinishedAt}}' "$c" >&2 2>&1 || true
        "$DOCKER" logs --tail 100 "$c" >&2 2>&1 || true
      fi
    done
  fi
  "$DOCKER" rm -fv "$NAME" "$PGNAME" "$CURLC" >/dev/null 2>&1 || true; "$DOCKER" network rm "$NET" >/dev/null 2>&1 || true; "$DOCKER" rmi "$IMG" >/dev/null 2>&1 || true; rm -rf "$SECDIR" "$BUILDTMP"
  exit "$rc"
}
trap on_exit EXIT
EXTRA=()
# 비밀은 파일로 주입한다(D84): NAME_FILE. 환경 변수에는 경로만 있다.
REVTOKEN=smoke-reviewer-token
printf '%s' "$TOKEN:smoke-author:question-author,$REVTOKEN:smoke-reviewer:clinical-reviewer" > "$SECDIR/author_tokens"
printf '%s' "smoke=read-token" > "$SECDIR/read_tokens"
"$DOCKER" network create "$NET" >/dev/null
if [ "$SMOKE_DB" = postgres ]; then
  PGPW=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')
  printf '%s' "$PGPW" > "$SECDIR/pg_password"; chmod 644 "$SECDIR/pg_password"
  "$DOCKER" run -d --name "$PGNAME" --network "$NET" -e POSTGRES_USER=qa -e POSTGRES_DB=qa -e POSTGRES_PASSWORD_FILE=/run/secrets/pg_password -v "$SECDIR/pg_password:/run/secrets/pg_password:ro" "$POSTGRES_IMAGE" >/dev/null
  for i in $(seq 1 60); do "$DOCKER" exec "$PGNAME" pg_isready -U qa -d qa >/dev/null 2>&1 && break; sleep 1; done
  sleep 2
  printf '%s' "postgresql://qa:$PGPW@$PGNAME:5432/qa" > "$SECDIR/database_url"
  EXTRA=(-e QA_DATABASE_URL_FILE=/run/secrets/database_url)
else
  EXTRA=(-e QA_DEV_MODE=1)   # SQLite는 개발·시험 구성이다. 운영 구성(PostgreSQL URL과 토큰)이 아니면 서비스가 시작을 거부한다
fi
chmod 755 "$SECDIR"; chmod 644 "$SECDIR"/*
"$DOCKER" run -d --name "$NAME" --network "$NET" "${EXTRA[@]}" -v "$SECDIR:/run/secrets:ro" -e QA_REQUIRE_TWO_PERSON=true -e QA_AUTHOR_TOKENS_FILE=/run/secrets/author_tokens -e QA_READ_TOKENS_FILE=/run/secrets/read_tokens "$IMG" >/dev/null
"$DOCKER" run -d --name "$CURLC" --network "container:$NAME" --entrypoint sleep "$CURL_IMAGE" 3600 >/dev/null      # 확인 컨테이너(이 스크립트가 만든 이름만 정리한다)
echo "접속: $BASE (확인 컨테이너가 $NAME 의 네트워크를 공유, 호스트 포트 없음)"
# 왜 30초: 단독 기동 시간 측정(2026-11-03, 이 PC, 3회): 컨테이너 시작부터 /healthz 응답까지 각 1초(정수 초 측정, 실제 1~2초 미만). 여러 영역 스모크를 한 서버에서 병렬로 돌리면 느려질 수 있어 단독 측정값의 여러 배 여유를 둔다(표준 값이 아니라 측정에서 거꾸로 정한 값).
START=$SECONDS
for i in $(seq 1 30); do
  cu -fsS "$BASE/healthz" >/dev/null 2>&1 && break
  sleep 1
done
echo "/healthz 응답까지 약 $((SECONDS - START))초(정수 초)"
cu -fsS "$BASE/healthz"; echo
ready=$(cu -fsS "$BASE/readyz"); echo "$ready"
echo "$ready" | grep -q "\"db\": \"$SMOKE_DB\"" || { echo "/readyz의 db 종류가 $SMOKE_DB가 아님"; exit 1; }
# 왜 변수에 담나: `curl | grep -q` 는 grep 이 먼저 끝나면 curl 이 쓰기 오류(23)로 끝나 pipefail 에서 간헐적으로 실패한다(PostgreSQL 스모크에서 한 번 드러났다)
ui=$(cu -fsS "$BASE/ui/") && grep -q "문진표 저작" <<<"$ui" || { echo "저작 화면이 열리지 않음"; exit 1; }
mt=$(cu -fsS -H "Authorization: Bearer wrong-token" "$BASE/metrics") && grep -q "^qa_up 1" <<<"$mt" || { echo "/metrics 가 인증 없이 200 이 아님(R10.11)"; exit 1; }
"$DOCKER" exec "$NAME" restic version >/dev/null || { echo "백업 도구(restic)가 이미지에 없음(R10.1)"; exit 1; }
[ "$("$DOCKER" exec "$NAME" id -u)" != "0" ] || { echo "root로 실행 중"; exit 1; }
if "$DOCKER" inspect "$NAME" | grep -q -e "$TOKEN" -e read-token || "$DOCKER" exec "$NAME" env | grep -q -e "$TOKEN" -e read-token; then echo "비밀이 env/inspect에 노출됨"; exit 1; fi
echo "비밀 비노출 확인(inspect, exec env)"
if [ "$SMOKE_DB" = postgres ]; then
  if "$DOCKER" inspect "$NAME" | grep -q "$PGPW" || "$DOCKER" exec "$NAME" env | grep -q "$PGPW" || "$DOCKER" logs "$NAME" 2>&1 | grep -q "$PGPW"; then echo "DB 암호가 노출됨"; exit 1; fi
  echo "DB 암호 비노출 확인(inspect, env, logs)"
fi
AUTH="Authorization: Bearer $TOKEN"
did=$(cud -fsS -X POST -H "$AUTH" --data-binary @- <<<"$(sed s/qs-main/qs-smoke/ "$here/examples/question_form.txt")" \
  "$BASE/v1/materials?question_set_id=qs-smoke&filename=form.txt" | python3 -c 'import sys,json;print(json.load(sys.stdin)["draft_id"])')
ph=$(cu -fsS -H "$AUTH" "$BASE/v1/drafts/$did/preview" | python3 -c 'import sys,json;d=json.load(sys.stdin);assert d["ready_to_confirm"];print(d["preview_hash"])')
ref=$(cu -fsS -X POST -H "$AUTH" -d "{\"preview_hash\":\"$ph\"}" "$BASE/v1/drafts/$did/confirm"); echo "$ref"
# 4-eyes(D88): 저자의 확인만으로는 검수 완료가 아니다. 같은 저자가 스스로 임상 승인하면 거부되고, 검수 역할의 다른 주체가 승인해야 한다
code=$(cu -s -o /dev/null -w '%{http_code}' -X POST -H "$AUTH" -d "{\"question_set\":$ref,\"decision\":\"APPROVED\",\"clinical_basis_approved\":true}" "$BASE/v1/reviews")
[ "$code" = "403" ] || { echo "저자의 자기 승인이 거부되지 않음: $code"; exit 1; }
cu -fsS -X POST -H "Authorization: Bearer $REVTOKEN" -d "{\"question_set\":$ref,\"decision\":\"APPROVED\",\"clinical_basis_approved\":true}" "$BASE/v1/reviews" >/dev/null
code=$(cu -s -o /dev/null -w '%{http_code}' "$BASE/v1/drafts")
[ "$code" = "401" ] || { echo "인증 없는 요청이 401로 거부되지 않음(계약 CONVENTIONS 6절): $code"; exit 1; }
cu -fsS -H "$AUTH" "$BASE/v1/signals" | grep -q DISABLED || { echo "신호 비활성 응답 실패"; exit 1; }
RT="Authorization: Bearer read-token"
cu -fsS -H "$RT" "$BASE/v1/question-sets" | grep -q '"qs-smoke"' || { echo "list_ids 실패"; exit 1; }
cu -fsS -H "$RT" "$BASE/v1/question-sets/qs-smoke/latest?reviewed_only=true" | grep -q '"version": "1"' || { echo "latest 실패"; exit 1; }
cu -fsS -H "$RT" "$BASE/v1/question-sets/qs-smoke/versions/1" | python3 -c 'import sys,json;assert json.load(sys.stdin)["version"]==1'
code=$(cu -s -o /dev/null -w '%{http_code}' -H "$RT" "$BASE/v1/drafts")
[ "$code" = "403" ] || { echo "읽기 토큰이 저작 경로에서 거부되지 않음: $code"; exit 1; }
for i in $(seq 1 20); do
  st=$("$DOCKER" inspect -f '{{.State.Health.Status}}' "$NAME"); [ "$st" = healthy ] && break; sleep 2
done
echo "HEALTHCHECK: $st"; [ "$st" = healthy ] || exit 1
"$DOCKER" image inspect "$IMG" --format '{{json .Config.Labels}}' | tee "$SECDIR/labels.json"
# 라벨을 계약 ImageLabels로 검사한다. 계약 패키지가 있는 파이썬(.venv 또는 PYTHON)이 없으면 검사하지 못했다고 밝힌다
LPY="${PYTHON:-$here/.venv/bin/python}"
if "$LPY" -c "import boundary_contracts" >/dev/null 2>&1; then
  "$LPY" -c "import json,sys,boundary_contracts as b; e=list(b.validator('ImageLabels').iter_errors(json.load(open(sys.argv[1])))); print('ImageLabels 검사:', 'OK' if not e else e); sys.exit(1 if e else 0)" "$SECDIR/labels.json"
else
  echo "ImageLabels 검사 못 함: boundary_contracts가 있는 파이썬이 없다(PYTHON 지정)"
fi
if "$LPY" -c "import boundary_contracts" >/dev/null 2>&1; then
  "$LPY" -c "import json,sys,boundary_contracts as b; e=list(b.validator('BuildImageResult').iter_errors(json.load(open(sys.argv[1])))); print('BuildImageResult 검사:', 'OK' if not e else e); sys.exit(1 if e else 0)" "$BUILDTMP/result.json"
fi
if [ "$SMOKE_DB" = postgres ]; then
  # 컨테이너를 다시 띄워도 데이터가 남는다(서버 쪽 저장소)
  "$DOCKER" restart "$NAME" >/dev/null
  "$DOCKER" rm -f "$CURLC" >/dev/null; "$DOCKER" run -d --name "$CURLC" --network "container:$NAME" --entrypoint sleep "$CURL_IMAGE" 3600 >/dev/null      # 재시작하면 네트워크 이름공간이 새로 생기므로 확인 컨테이너를 다시 붙인다
  for i in $(seq 1 30); do cu -fsS "$BASE/readyz" >/dev/null 2>&1 && break; sleep 1; done
  cu -fsS -H "$RT" "$BASE/v1/question-sets" | grep -q '"qs-smoke"' || { echo "재시작 뒤 데이터가 없음"; exit 1; }
  echo "재시작 뒤 데이터 유지 확인"
fi
echo "SMOKE OK ($SMOKE_DB)"
