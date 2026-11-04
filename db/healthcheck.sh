#!/bin/sh
# question-authoring-db HEALTHCHECK(root 로 실행된다). 건강 = (1) 선언된 계정마다 역할이 있고 LOGIN 이 되며 선언된 데이터베이스에 CONNECT 할 수 있다, (2) 서버가 TLS 로 접속을 받는다.
# 왜 계정 확인은 소켓에서 슈퍼유저로: 계정 암호는 이 컨테이너 밖 비밀이라 점검이 들고 다니지 않는다(D84). 소켓 peer 규칙(pg_hba.conf)이 이미 postgres 에게만 열려 있다.
# 왜 TLS 확인은 암호 없이 sslmode=require: TLS 핸드셰이크가 끝난 뒤에야 서버가 암호를 묻는다. 그래서 "no password supplied"(암호 요구)는 TLS 가 된다는 뜻이고,
#   "does not support SSL"/"no encryption" 이면 TLS 가 꺼진 것이다. 다른 실패(연결 거부 등)도 unhealthy 로 본다. 읽는 것은 이미지 안 고정 경로의 선언(/etc/openit/database.json)뿐이다(구조 규칙 1).
set -eu
DECL=/etc/openit/database.json   # 고정 경로(이미지에 구워진 선언 = 라벨 io.openit.database). 덮어쓰는 환경 변수는 두지 않는다: AreaDatabaseEnv 에 없다
PORT="$(jq -r '.port' "$DECL")"
SQL="$(jq -r --arg q "'" '[.accounts[] | "SELECT count(*) = 1 FROM pg_roles r WHERE r.rolname=" + $q + .name + $q + " AND r.rolcanlogin AND EXISTS (SELECT 1 FROM pg_database d WHERE d.datname=" + $q + .database + $q + " AND has_database_privilege(r.oid, d.oid, " + $q + "CONNECT" + $q + "))"] | join(" UNION ALL ")' "$DECL")"
res="$(timeout 4 gosu postgres psql -X -At -d postgres -c "$SQL" 2>/dev/null)" || { echo "unhealthy: 소켓 접속 실패" >&2; exit 1; }
[ -n "$res" ] && ! printf '%s\n' "$res" | grep -qv '^t$' || { echo "unhealthy: 선언한 계정·데이터베이스가 준비되지 않았다" >&2; exit 1; }
acct="$(jq -r '.accounts[0] | .name' "$DECL")"; db="$(jq -r '.accounts[0] | .database' "$DECL")"
err="$(timeout 4 env PGSSLMODE=require PGCONNECT_TIMEOUT=3 psql -X -w -h 127.0.0.1 -p "$PORT" -U "$acct" -d "$db" -c 'SELECT 1' 2>&1 >/dev/null)" && rc=0 || rc=$?
case "$err" in
  *"no password supplied"*) exit 0 ;;
esac
echo "unhealthy: TLS 접속 확인 실패" >&2; exit 1
