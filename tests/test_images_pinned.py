"""기반 이미지는 digest 로 고정한다(공통 규칙 '빌드 때 고정'). 값의 정본은 images.env. 정적 점검: 읽어서 아는 것은 실행하지 않고 본다."""
import re
import subprocess
from pathlib import Path

from helpers import image

ROOT = Path(__file__).resolve().parent.parent
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def test_every_image_in_images_env_has_a_digest():
    keys = [l.split("=", 1)[0] for l in (ROOT / "images.env").read_text(encoding="utf-8").splitlines() if re.match(r"[A-Z_]+=", l)]
    assert keys and all(DIGEST.search(image(k)) for k in keys), keys


def test_dockerfile_arg_defaults_equal_images_env():
    for path, arg, key in (("Dockerfile", "PYTHON_IMAGE", "PYTHON_IMAGE"), ("db/Dockerfile", "POSTGRES_IMAGE", "POSTGRES_IMAGE")):
        text = (ROOT / path).read_text(encoding="utf-8")
        m = re.search(rf"^ARG {arg}=(\S+)$", text, re.M)
        assert m and m.group(1) == image(key), path
        assert re.search(rf"^FROM \${{{arg}}}$", text, re.M), path


def test_no_untagged_digestless_image_is_used_elsewhere():
    """compose·스모크·개발 스크립트에 digest 없는 postgres/python/restic 이미지 이름이 남아 있지 않다."""
    for rel in ("compose.dev.yaml", "scripts/smoke.sh", "scripts/dev-postgres.sh", "scripts/build-local-image.sh"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        assert not re.search(r"\b(postgres:\d|python:3|restic/restic:|restic/rest-server:)", code), rel
