FROM python:3.12-slim
ARG COMMIT=n/a
ARG BOUNDARY_TAG=n/a
ARG SURVEY_SCHEMA_TAG=n/a
# 왜 model-runtime=n/a: 이 서비스는 model-runtime을 쓰지 않는다. turn-engine 라벨은 계약 3.0.0에서 없어졌다(ImageLabels가 거부한다).
# 라벨 키는 D50이 통일했고 계약 ImageLabels는 두 키를 필수로 두며 값 없음은 `n/a` 하나만 허용한다(`none`은 거부, deploy CR-D28). ARG로 받게 하면 넘겨받을 값이 없다.
LABEL io.openit.service=question-authoring \
      io.openit.revision=$COMMIT \
      io.openit.contracts.boundary=$BOUNDARY_TAG \
      io.openit.contracts.survey-schema=$SURVEY_SCHEMA_TAG \
      io.openit.model-runtime=n/a
WORKDIR /app
COPY requirements.lock .
RUN grep -v -e '^boundary-contracts' -e '^openit-survey-schema' -e '^openit-link' -e '^#' requirements.lock > /tmp/req.txt && pip install --no-cache-dir -r /tmp/req.txt
COPY build/wheels /tmp/wheels
RUN pip install --no-cache-dir --no-deps /tmp/wheels/*.whl && rm -rf /tmp/wheels
COPY pyproject.toml ./
COPY question_authoring ./question_authoring
COPY schemas ./schemas
RUN pip install --no-cache-dir --no-deps .
RUN useradd --uid 10001 --create-home qa && mkdir /data && chown qa /data
USER 10001
ENV QA_DB_PATH=/data/questions.sqlite3 \
    QA_SCHEMA_DIR=/app/schemas \
    PYTHONUNBUFFERED=1
EXPOSE 8080
VOLUME /data
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status==200 else 1)"
CMD ["python", "-m", "question_authoring"]
