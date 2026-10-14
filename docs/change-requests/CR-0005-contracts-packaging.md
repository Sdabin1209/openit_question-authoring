# CR-0005 boundary-contracts 배포 형태(스키마 포함, Git 태그)

- 대상: boundary-contracts 패키지
- 부족한 점: (a) `pyproject.toml`의 패키지에 `schemas/`가 들어 있지 않아 설치본만으로는 스키마 검증이 불가능하다(현재 편집 설치와 상위 폴더 위치에 의존). (b) 공통 규칙 D22는 Git 태그로 설치하라고 하나 태그가 없다.
- 현재 처리: 개발은 `pip install -e ../boundary-contracts`와 기본 스키마 위치(패키지 상위 `schemas/`)를 쓰고, 이미지 빌드는 읽기 전용 복사본(`scripts/vendor_contracts.sh`, 커밋하지 않음)을 쓴다. 스키마 디렉터리는 `Validators` 인자로도 받는다.
- 요청: 스키마를 패키지 데이터로 포함하고 계약 버전에 맞는 Git 태그를 달아 달라. 태그가 생기면 Dockerfile을 태그 설치로 바꾼다.
