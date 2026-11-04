#!/bin/sh
# question-authoring-db 진입점(root 로 시작해 공식 postgres 이미지 진입점으로 넘긴다).
# 하는 일: 서버 TLS 필수(DB_TLS_DIR 의 tls.crt·tls.key 가 없으면 시작하지 않는다), 접속 규칙·암호 방식·연결 상한을 서버 인자로 고정한다.
# 왜 인증서를 복사하나: PostgreSQL 은 개인 키 파일이 서버 사용자 소유이고 0600 이 아니면 거부한다. 마운트된 비밀 파일의 권한은 우리가 정하지 못한다.
set -eu
if [ "${1:-}" = "postgres" ] && [ "$(id -u)" = "0" ]; then
  TLS="${DB_TLS_DIR:-/certs}"
  [ -s "$TLS/tls.crt" ] && [ -s "$TLS/tls.key" ] || { echo "question-authoring-db: $TLS/tls.crt·tls.key 가 없다. 서버 TLS 는 필수다(D126)" >&2; exit 1; }
  install -d -o postgres -g postgres -m 0700 /var/lib/postgresql-tls
  install -o postgres -g postgres -m 0644 "$TLS/tls.crt" /var/lib/postgresql-tls/tls.crt
  install -o postgres -g postgres -m 0600 "$TLS/tls.key" /var/lib/postgresql-tls/tls.key
  # max_connections 50: 서비스 동시 요청 기본 32(QA_MAX_CONCURRENT_REQUESTS) + 정리·백업 잡 + 관리용 여유. 측정하지 않은 시작값(ADR 0029)
  shift
  set -- postgres \
    -c ssl=on -c ssl_cert_file=/var/lib/postgresql-tls/tls.crt -c ssl_key_file=/var/lib/postgresql-tls/tls.key -c ssl_min_protocol_version=TLSv1.2 \
    -c hba_file=/etc/openit/pg_hba.conf -c password_encryption=scram-sha-256 -c max_connections=50 -c listen_addresses='*' "$@"
fi
exec docker-entrypoint.sh "$@"
