#!/bin/sh
# question-authoring-db 진입점(root 로 시작해 공식 postgres 이미지 진입점으로 넘긴다).
# 하는 일: 서버 TLS 필수(DB_TLS_DIR 의 tls.crt·tls.key 가 없으면 시작하지 않는다), 접속 규칙·암호 방식·연결 상한을 서버 인자로 고정한다.
set -eu
# 인증서 복사: PostgreSQL 은 개인 키 파일이 서버 사용자 소유이고 0600 이 아니면 거부한다. 마운트된 비밀 파일의 권한은 우리가 정하지 못한다.
install_tls() {
  TLS="${DB_TLS_DIR:-/certs}"
  [ -s "$TLS/tls.crt" ] && [ -s "$TLS/tls.key" ] || return 1
  install -d -o postgres -g postgres -m 0700 /var/lib/postgresql-tls
  install -o postgres -g postgres -m 0644 "$TLS/tls.crt" /var/lib/postgresql-tls/tls.crt.new
  install -o postgres -g postgres -m 0600 "$TLS/tls.key" /var/lib/postgresql-tls/tls.key.new
  mv /var/lib/postgresql-tls/tls.crt.new /var/lib/postgresql-tls/tls.crt       # 같은 폴더에서 rename: 서버가 반쯤 쓴 파일을 읽지 않는다
  mv /var/lib/postgresql-tls/tls.key.new /var/lib/postgresql-tls/tls.key
}
fingerprint() { cat "${DB_TLS_DIR:-/certs}/tls.crt" "${DB_TLS_DIR:-/certs}/tls.key" 2>/dev/null | sha256sum | cut -d' ' -f1; }

if [ "${1:-}" = "postgres" ] && [ "$(id -u)" = "0" ]; then
  install_tls || { echo "question-authoring-db: ${DB_TLS_DIR:-/certs}/tls.crt·tls.key 가 없다. 서버 TLS 는 필수다(D126)" >&2; exit 1; }
  # 갱신된 인증서를 스스로 다시 읽는다(step-ca 가 인증서를 갱신해 같은 파일을 바꾼다): 60초마다 지문을 보고 바뀌면 다시 복사한 뒤 서버에 SIGHUP(ssl_cert_file·ssl_key_file 은 reload 로 적용된다, PostgreSQL 문서).
  # 60초는 갱신이 만료보다 훨씬 앞서 일어난다는 전제의 시작값이다(측정하지 않음, ADR 0029). 복사나 신호가 실패해도 다음 주기에 다시 한다.
  (
    last="$(fingerprint)"
    while sleep 60; do
      now="$(fingerprint)"
      if [ -n "$now" ] && [ "$now" != "$last" ] && install_tls; then
        pid="$(head -n1 /var/lib/postgresql/data/postmaster.pid 2>/dev/null || true)"
        if [ -n "$pid" ] && kill -HUP "$pid" 2>/dev/null; then last="$now"; echo "question-authoring-db: 갱신된 서버 인증서를 다시 읽었다" >&2; fi
      fi
    done
  ) &
  # max_connections 50: 서비스 동시 요청 기본 32(QA_MAX_CONCURRENT_REQUESTS) + 정리·백업 잡 + 관리용 여유. 측정하지 않은 시작값(ADR 0029)
  shift
  set -- postgres \
    -c ssl=on -c ssl_cert_file=/var/lib/postgresql-tls/tls.crt -c ssl_key_file=/var/lib/postgresql-tls/tls.key -c ssl_min_protocol_version=TLSv1.2 \
    -c hba_file=/etc/openit/pg_hba.conf -c password_encryption=scram-sha-256 -c max_connections=50 -c listen_addresses='*' "$@"
fi
exec docker-entrypoint.sh "$@"
