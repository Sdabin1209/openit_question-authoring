"""시험이 쓰는 의존 리포의 고정 태그 소스 트리(ops-feedback tests/deps.py 와 같은 방식, 영역마다 따로 둔다: 영역끼리 코드를 가져오지 않는다).
옆 폴더(WORKSPACE_DIR, 기본: 이 리포의 상위)에 그 태그가 있으면 git archive, 없으면 GitHub 에서 그 태그만 얕게 받는다. 둘 다 안 되면 실패한다(건너뛰지 않는다)."""
from helpers import min_env
import io, os, subprocess, tarfile, tempfile
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WS = Path(os.environ.get("WORKSPACE_DIR") or ROOT.parent)


@lru_cache(maxsize=None)
def pinned_tree(repo: str, tag: str, path: str = "") -> Path:
    dest = Path(tempfile.mkdtemp(prefix=f"deps-{repo}-"))
    local = WS / repo
    r = subprocess.run(["git", "-C", str(local), "archive", tag] + ([path] if path else []), capture_output=True, env=min_env()) if local.is_dir() else None
    if r is not None and r.returncode == 0:
        tarfile.open(fileobj=io.BytesIO(r.stdout)).extractall(dest, filter="data")
        return dest / path if path else dest
    url = f"git@github.com:Sdabin1209/openit_{repo}.git"
    c = subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", tag, url, str(dest / "clone")], capture_output=True, text=True, env=min_env("SSH_AUTH_SOCK", "GIT_CONFIG_GLOBAL"))
    if c.returncode != 0:
        raise RuntimeError(f"{repo} {tag} 를 옆 폴더({local})에서도 {url} 에서도 꺼내지 못했다: {c.stderr.strip()[-200:]}")
    return dest / "clone" / path if path else dest / "clone"
