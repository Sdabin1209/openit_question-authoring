FROM python:3.12-slim
ARG COMMIT=unknown
ARG CONTRACT_VERSION=unknown
ARG BOUNDARY_CONTRACTS_REV=unknown
LABEL org.opencontainers.image.revision=$COMMIT \
      openit.contract.survey-schema=$CONTRACT_VERSION \
      openit.contract.boundary-contracts=$BOUNDARY_CONTRACTS_REV \
      openit.lib.turn-engine=none \
      openit.lib.model-runtime=none
WORKDIR /app
COPY requirements.lock .
RUN grep -v '^boundary-contracts' requirements.lock > /tmp/req.txt && pip install --no-cache-dir -r /tmp/req.txt
COPY build/wheels /tmp/wheels
RUN pip install --no-cache-dir --no-deps /tmp/wheels/boundary_contracts-*.whl && rm -rf /tmp/wheels
COPY build/survey-schema /opt/survey-schema
COPY pyproject.toml ./
COPY question_authoring ./question_authoring
RUN pip install --no-cache-dir --no-deps .
RUN useradd --uid 10001 --create-home qa && mkdir /data && chown qa /data
USER 10001
ENV QA_DB_PATH=/data/questions.sqlite3 \
    QA_SURVEY_SCHEMA=/opt/survey-schema \
    PYTHONUNBUFFERED=1
EXPOSE 8080
VOLUME /data
CMD ["python", "-m", "question_authoring"]
