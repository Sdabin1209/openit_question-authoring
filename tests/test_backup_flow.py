"""RUNTIME R10.1: 백업은 영역이 자기 데이터를 꺼내 자기 영역 게이트웨이 출구(R1.8)로 platform 백업 저장소에 보낸다.
저장소가 응답하지 않으면 0 이 아닌 종료 코드(R9.2)이고 서비스 출력은 바뀌지 않는다. 비밀은 argv·출력에 없다(R6.3)."""
import io
import json
import re
import subprocess
from pathlib import Path

import pytest
from boundary_contracts.errors import BoundaryError

from question_authoring import __main__ as qa_main
from question_authoring.wiring import backup_flow

ROOT = Path(__file__).resolve().parent.parent


def secret(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def env_of(tmp_path, **over):
    e = {"OPENIT_BACKUP_URL": "https://backup-store:8443/question-authoring",
         "OPENIT_BACKUP_CREDENTIALS_FILE": secret(tmp_path, "cred", "backup-user:S3CRET-pass:word"),
         "OPENIT_BACKUP_REPO_PASSWORD_FILE": secret(tmp_path, "repo-pw", "repo-pass"),
         "QA_DATABASE_URL_FILE": secret(tmp_path, "dburl", "postgresql://u:DBSECRET@db/q"),
         "OPENIT_EGRESS_PROXY": "http://127.0.0.1:3128", "SSL_CERT_FILE": "/etc/root.pem", "PATH": "/usr/bin"}
    e.update(over)
    return e


class Runner:
    def __init__(self, fail_on=None):
        self.calls, self.fail_on = [], fail_on

    def __call__(self, argv, stdin=None, env=None, **kw):
        self.calls.append((list(argv), dict(env or {}), stdin.read() if stdin else None))
        rc = 1 if (self.fail_on and self.fail_on in argv) else 0
        if argv[1:2] == ["cat"]:
            rc = 0
        return subprocess.CompletedProcess(argv, rc, stdout=b'{"message_type":"status"}\n{"message_type":"summary","snapshot_id":"abc123def"}\n')


@pytest.fixture
def fake_dump(monkeypatch):
    def d(url, out):
        out.write(b"COPY versions FROM STDIN;\nrow\n\\.\n")
        return 31
    monkeypatch.setattr(backup_flow, "dump", d)


def test_backup_goes_out_through_the_own_gateway_with_secrets_only_in_the_child_env(tmp_path, fake_dump):
    r = Runner()
    out = backup_flow.run(env_of(tmp_path), proxy="http://127.0.0.1:3128", runner=r)
    assert out == {"snapshot_id": "abc123def", "bytes": 31}
    argv, child, stdin = r.calls[-1]
    assert argv[:2] == ["restic", "backup"] and stdin == b"COPY versions FROM STDIN;\nrow\n\\.\n"
    assert child["HTTPS_PROXY"] == "http://127.0.0.1:3128"                         # R1.8: 자기 영역 게이트웨이 출구
    assert child["RESTIC_REPOSITORY"] == "rest:https://backup-store:8443/question-authoring"
    assert child["RESTIC_REST_USERNAME"] == "backup-user" and child["RESTIC_REST_PASSWORD"] == "S3CRET-pass:word"
    assert set(child) <= {"PATH", "HOME", "RESTIC_CACHE_DIR", "RESTIC_REPOSITORY", "RESTIC_PASSWORD_FILE", "RESTIC_REST_USERNAME",
                          "RESTIC_REST_PASSWORD", "HTTPS_PROXY", "SSL_CERT_FILE"}  # R9.1: 상속 환경을 통째로 넘기지 않는다
    joined = " ".join(" ".join(c[0]) for c in r.calls)
    assert "S3CRET" not in joined and "DBSECRET" not in joined and "repo-pass" not in joined      # R6.3: argv 에 비밀 없음


def run_main(tmp_path, monkeypatch, capsys, run):
    for k, v in env_of(tmp_path).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("QA_DB_PATH", ":memory:")
    monkeypatch.setattr(backup_flow, "run", lambda env, proxy=None: run(env))
    rc = qa_main.main(["backup"])
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def test_success_prints_a_backup_job_result_last_line_and_exits_0(tmp_path, monkeypatch, capsys):
    from question_authoring.wiring import contract
    rc, out, err = run_main(tmp_path, monkeypatch, capsys, lambda env: {"snapshot_id": "abc", "bytes": 5})
    line = json.loads(out.strip().splitlines()[-1])
    assert rc == 0 and line["status"] == "ok" and line["snapshot_id"] == "abc" and line["error"] is None
    assert contract.problems("BackupJobResult", None, line) == []


@pytest.mark.parametrize("code,rc_want", [("UNAVAILABLE", 75), ("INVALID_RECORD", 1)])
def test_failure_prints_failed_result_and_exit_code_75_or_1(tmp_path, monkeypatch, capsys, code, rc_want):
    from question_authoring.wiring import contract
    def refuse(env):
        raise BoundaryError(code, "백업 저장소로 보내지 못했다")
    rc, out, err = run_main(tmp_path, monkeypatch, capsys, refuse)
    line = json.loads(out.strip().splitlines()[-1])
    assert rc == rc_want and line["status"] == "failed" and line["snapshot_id"] is None and line["error"]["code"] == code
    assert contract.problems("BackupJobResult", None, line) == [] and "S3CRET" not in out + err and "DBSECRET" not in out + err


@pytest.mark.parametrize("fail", ["init", "backup"])
def test_restic_failures_are_unavailable(tmp_path, fake_dump, fail):
    class R(Runner):
        def __call__(self, argv, stdin=None, env=None, **kw):
            r = super().__call__(argv, stdin, env, **kw)
            if argv[1] == "cat":
                return subprocess.CompletedProcess(argv, 1)          # 저장소 없음 → init
            return r
    with pytest.raises(BoundaryError) as e:
        backup_flow.run(env_of(tmp_path), runner=R(fail_on=fail))
    assert e.value.code == "UNAVAILABLE"


def test_missing_binary_is_unavailable_kind_only(tmp_path, fake_dump):
    def boom(*a, **k):
        raise FileNotFoundError("restic SECRET-path")
    with pytest.raises(BoundaryError) as e:
        backup_flow.run(env_of(tmp_path), runner=boom)
    assert e.value.code == "UNAVAILABLE" and "SECRET" not in e.value.message


@pytest.mark.parametrize("bad", [dict(OPENIT_BACKUP_URL="http://backup-store/q"), dict(OPENIT_BACKUP_URL=""), dict(QA_DATABASE_URL_FILE="")])
def test_config_violations_do_not_start(tmp_path, fake_dump, bad):
    e = env_of(tmp_path)
    e.update(bad)
    e = {k: v for k, v in e.items() if v != ""}
    with pytest.raises((BoundaryError, ValueError)):
        backup_flow.run(e, runner=Runner())


def test_dumped_tables_are_the_migration_tables():
    sql = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "question_authoring" / "migrations" / "postgres").glob("*.py"))
    assert set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql)) == set(backup_flow.TABLES)


def test_dump_copies_every_table_from_a_real_postgres_in_one_readonly_transaction(validators):
    from conftest import pg_schema
    from question_authoring.wiring.stores import PostgresQuestionStore
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema)
        try:
            import psycopg.conninfo
            d = psycopg.conninfo.conninfo_to_dict(url)
            d["options"] = f"-c search_path={schema}"
            dsn = psycopg.conninfo.make_conninfo(**d)
            buf = io.BytesIO()
            n = backup_flow.dump(dsn, buf)
            text = buf.getvalue().decode()
            assert n == len(buf.getvalue()) and all(f"COPY {t} FROM STDIN;" in text for t in backup_flow.TABLES)
        finally:
            s.close()


def test_bad_egress_proxy_refuses_to_start(tmp_path, monkeypatch):
    """R1.8·CommonEnv: OPENIT_EGRESS_PROXY 가 loopback http 가 아니면 기동 때 거부한다(잡도 같다). 값은 메시지에 쓰지 않는다."""
    for k, v in env_of(tmp_path, OPENIT_EGRESS_PROXY="http://gateway.example:3128").items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("QA_DB_PATH", ":memory:")
    monkeypatch.setenv("QA_DEV_MODE", "1")
    with pytest.raises(SystemExit) as e:
        qa_main.main(["backup"])
    assert "CommonEnv" in str(e.value) and "gateway.example" not in str(e.value)
