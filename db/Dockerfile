# 기반 이미지는 digest 로 고정한다(값의 정본은 images.env, 같은 값인지 tests/test_images_pinned.py 가 확인한다)
ARG POSTGRES_IMAGE=postgres:16@sha256:1beaa3084eee2676f20a9cd9928f0bf2c5bf674f8e49f81017912183d377c61c
FROM ${POSTGRES_IMAGE}
ARG COMMIT=n/a
ARG BOUNDARY_TAG=n/a
ARG SURVEY_SCHEMA_TAG=n/a
# 라벨 io.openit.database(DatabaseDeclaration)는 빌드 스크립트가 db/database.json 에서 검증해 --label 로 단다. 키 이름과 값 없음 표기는 앱 이미지와 같다(ImageLabels)
LABEL io.openit.service=question-authoring-db \
      io.openit.revision=$COMMIT \
      io.openit.contracts.boundary=$BOUNDARY_TAG \
      io.openit.contracts.survey-schema=$SURVEY_SCHEMA_TAG \
      io.openit.model-runtime=n/a
# 왜 jq: 초기화 스크립트가 선언(JSON)을 읽는다. Debian 표준 패키지
RUN apt-get update && apt-get install -y --no-install-recommends jq && rm -rf /var/lib/apt/lists/*
COPY db/database.json /etc/openit/database.json
COPY db/pg_hba.conf /etc/openit/pg_hba.conf
COPY db/initdb/10-accounts.sh /docker-entrypoint-initdb.d/10-accounts.sh
COPY --chmod=0755 db/healthcheck.sh /usr/local/bin/qa-db-healthcheck.sh
COPY db/entrypoint.sh /usr/local/bin/qa-db-entrypoint.sh
# 왜 HEALTHCHECK: 선언한 계정과 TLS 접속을 이미지가 스스로 확인한다(pg_isready 는 TLS 와 계정을 보지 않는다). Docker 표준 HEALTHCHECK.
# interval 15s 는 앱 이미지와 같다. start-period 60s 는 첫 기동의 초기화(계정·권한 생성) 시간을 덮는 시작값이고 측정하지 않았다(ADR 0029). timeout 5s 는 점검 안의 timeout 4s 보다 크다.
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 CMD ["qa-db-healthcheck.sh"]
ENTRYPOINT ["qa-db-entrypoint.sh"]
CMD ["postgres"]
EXPOSE 5432
