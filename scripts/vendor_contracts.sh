#!/usr/bin/env bash
# 이미지 빌드용으로 읽기 전용 계약을 vendor-contracts/ 에 복사한다(커밋하지 않는다).
# 배포 때는 boundary-contracts를 Git 태그로 설치하도록 바꾼다(STATUS.md 참고: 아직 태그가 없다).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
rm -rf "$here/vendor-contracts"; mkdir -p "$here/vendor-contracts"
src_bc="$here/../boundary-contracts"; src_sv="$here/../survey-service/schema"
mkdir -p "$here/vendor-contracts/boundary-contracts" "$here/vendor-contracts/survey-schema"
cp -r "$src_bc/boundary_contracts" "$src_bc/schemas" "$src_bc/pyproject.toml" "$here/vendor-contracts/boundary-contracts/"
find "$here/vendor-contracts" -name __pycache__ -prune -exec rm -rf {} +
cp -r "$src_sv/schemas" "$src_sv/CONTRACT_VERSION" "$here/vendor-contracts/survey-schema/"
(cd "$src_bc" && git rev-parse HEAD 2>/dev/null || echo unknown) > "$here/vendor-contracts/boundary-contracts.rev"
echo "vendor-contracts 준비됨"
