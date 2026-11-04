FROM postgres:16
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
COPY db/entrypoint.sh /usr/local/bin/qa-db-entrypoint.sh
ENTRYPOINT ["qa-db-entrypoint.sh"]
CMD ["postgres"]
EXPOSE 5432
