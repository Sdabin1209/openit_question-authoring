"""워크스페이스 규칙: 이웃 영역 리포의 파일 구조(스크립트·설정 경로)에 기대지 않는다. 읽기가 허용된 것은 계약(boundary-contracts)·문진 스키마(survey-service/schema)·areas.json 뿐이다.
이 리포의 안내·스크립트·시험이 `../deploy/…` 같은 이웃 경로를 실행·참조하지 않는지 본다(역사 기록인 CHANGELOG·audit 은 뺀다)."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED = ("../boundary-contracts", "../survey-service", "../head/shared/gitconfig-local-mirrors", "../head/shared/areas.json")
SKIP_DIRS = {".venv", "build", ".gen", ".git", "__pycache__", ".dev-postgres", "node_modules"}
SKIP_FILES = {"CHANGELOG.md"}


def files():
    for p in ROOT.rglob("*"):
        if p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS) and "audit" not in p.parts and "egg-info" not in str(p) \
                and p.suffix in {".md", ".sh", ".py", ".yaml", ".yml", ".json", ".txt", ".toml"} and p.name not in SKIP_FILES and p.name != Path(__file__).name:
            yield p


def test_no_path_into_a_neighbour_other_than_the_allowed_reads():
    bad = []
    for p in files():
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for m in re.finditer(r"\.\./[A-Za-z][\w-]*[^\s`'\"),;]*", line):
                first = m.group(0).split("/")[1]
                if (ROOT.parent / first).is_dir() and first != ROOT.name and not m.group(0).startswith(ALLOWED):      # 워크스페이스의 이웃 폴더만(리포 안 상대 경로·URL 조각은 아니다)
                    bad.append(f"{p.relative_to(ROOT)}:{n}: {m.group(0)}")
    assert bad == [], bad


def test_dev_postgres_is_this_repos_own_script_and_compose_file():
    sh = (ROOT / "scripts" / "dev-postgres.sh").read_text(encoding="utf-8")
    comp = (ROOT / "compose.dev.yaml").read_text(encoding="utf-8")
    assert "postgres:16" in comp and '"127.0.0.1::5432"' in comp                  # Docker 가 호스트 포트를 배정한다
    assert "question-authoring" in sh and "DEV_PG_SUFFIX" in sh                   # 이름에 리포 이름
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts" / "dev-postgres.sh")], capture_output=True).returncode == 0
    assert ".dev-postgres/" in (ROOT / ".gitignore").read_text(encoding="utf-8")
