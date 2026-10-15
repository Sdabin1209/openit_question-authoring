FROM python:3.12-slim
ARG COMMIT=unknown
ARG BOUNDARY_TAG=unknown
ARG SURVEY_SCHEMA_TAG=unknown
LABEL io.openit.service=question-authoring \
      io.openit.revision=$COMMIT \
      io.openit.contracts.boundary=$BOUNDARY_TAG \
      io.openit.contracts.survey-schema=$SURVEY_SCHEMA_TAG \
      io.openit.turn-engine=none \
      io.openit.model-runtime=none
WORKDIR /app
COPY requirements.lock .
RUN grep -v -e '^boundary-contracts' -e '^openit-survey-schema' -e '^#' requirements.lock > /tmp/req.txt && pip install --no-cache-dir -r /tmp/req.txt
COPY build/wheels /tmp/wheels
RUN pip install --no-cache-dir --no-deps /tmp/wheels/*.whl && rm -rf /tmp/wheels
COPY pyproject.toml ./
COPY question_authoring ./question_authoring
RUN pip install --no-cache-dir --no-deps .
RUN useradd --uid 10001 --create-home qa && mkdir /data && chown qa /data
USER 10001
ENV QA_DB_PATH=/data/questions.sqlite3 \
    PYTHONUNBUFFERED=1
EXPOSE 8080
VOLUME /data
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status==200 else 1)"
CMD ["python", "-m", "question_authoring"]
