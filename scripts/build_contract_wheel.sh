#!/usr/bin/env bash
# boundary-contracts 태그(기본 v1.0.0) 시점의 소스를 git archive로 꺼내 wheel을 만든다. 산출물은 build/(커밋하지 않음).
# docker 안에서는 ssh가 어려우므로 호스트에서 만들어 이미지에 설치한다.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
TAG="${CONTRACTS_TAG:-v1.0.0}"
repo="$here/../boundary-contracts"
rm -rf "$here/build/contracts-src" "$here/build/wheels"
mkdir -p "$here/build/contracts-src" "$here/build/wheels"
git -C "$repo" archive "$TAG" | tar -x -C "$here/build/contracts-src"
git -C "$repo" rev-parse "$TAG^{commit}" > "$here/build/contracts.rev"
PY="${PYTHON:-python3}"
"$PY" -m pip wheel -q --no-deps -w "$here/build/wheels" "$here/build/contracts-src"
mkdir -p "$here/build/survey-schema"
cp -r "$here/../survey-service/schema/schemas" "$here/../survey-service/schema/CONTRACT_VERSION" "$here/build/survey-schema/"
ls "$here/build/wheels"
