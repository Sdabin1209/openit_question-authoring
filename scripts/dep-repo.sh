# 의존 리포의 고정 태그를 꺼낼 리포 위치(pip 의 git+ssh…@태그 설치와 같은 원칙: 판은 태그 하나, 위치는 정해진 순서로 찾는다).
#   1. 환경 변수 OPENIT_<이름>_REPO  2. 옆 폴더(../<이름>)에 그 태그가 있으면 옆 폴더  3. 원격에서 그 태그만 git 제외 캐시(build/deps/<이름>.git-<태그>)로 받는다
# 사용: dep_repo <이름> <태그> -> 표준 출력: 리포 경로. 받지 못하면 종료 코드 1.
dep_repo() {
  local name=$1 tag=$2 here; here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  local envn="OPENIT_$(echo "$name" | tr 'a-z-' 'A-Z_')_REPO"
  if [ -n "${!envn:-}" ]; then echo "${!envn}"; return 0; fi
  if git -C "$here/../$name" rev-parse --verify --quiet "$tag^{commit}" >/dev/null 2>&1; then echo "$here/../$name"; return 0; fi
  local cache="$here/build/deps/$name.git-$tag"
  if ! git -C "$cache" rev-parse --verify --quiet "$tag^{commit}" >/dev/null 2>&1; then
    mkdir -p "$here/build/deps"; local tmp; tmp="$(mktemp -d "$here/build/deps/$name.tmp-XXXX")"
    git clone -q --bare --depth 1 --branch "$tag" "git@github.com:Sdabin1209/openit_$name.git" "$tmp/r.git" || { rm -rf "$tmp"; return 1; }
    git -C "$tmp/r.git" tag -f "$tag" HEAD >/dev/null
    mv -T "$tmp/r.git" "$cache" 2>/dev/null || true   # 동시에 받은 다른 실행이 먼저 놓았으면 그것을 쓴다(원자적 이름 바꾸기)
    rm -rf "$tmp"
  fi
  git -C "$cache" rev-parse --verify --quiet "$tag^{commit}" >/dev/null 2>&1 || return 1
  echo "$cache"
}
