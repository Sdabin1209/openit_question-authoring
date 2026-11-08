# ADR 0032: 영역 DB 이미지는 비 root(postgres 999:999)로 실행한다

## 상태
채택(2026-11-08, 헤드 대장 #8). ADR 0029 의 "2026-11-06 배포 권한 회귀 수정" 절(gosu, 비밀 사본, 배포 capability)을 대체한다(Supersedes). 0029 의 나머지(엔진·버전·접속·백업·HEALTHCHECK)는 그대로 유효하다.

## 맥락
`db/Dockerfile` 에 `USER` 가 없어 이미지가 root 로 시작했다. 진입점이 root 로 비밀 사본(`/run/question-authoring-db-secrets`)과 인증서 사본을 만들고 `gosu postgres` 로 내려갔고, 이를 위해 배포가 CHOWN·DAC_OVERRIDE·FOWNER·SETUID·SETGID capability 를 줘야 했다. 공통 규칙은 "root 가 아닌 사용자로 실행"이고, 계약 ContainerInputs(D134)는 이미지 선언의 process uid:gid 가 실제 실행 사용자와 같아야 배치가 파일·쓰기 권한을 맞게 준다고 정한다(2026-11-06 DB 비밀 읽기 실패의 원인). 헤드 점검 `check-image-autonomy.py` 가 이를 잡았다.

## 결정
- `db/Dockerfile` 에 `USER postgres`(공식 `postgres:16` 이미지의 postgres, uid:gid 999:999)를 둔다. 데이터 폴더는 공식 이미지가 postgres 소유로 만들어 두고, initdb 훅(`10-accounts.sh`)은 공식 진입점이 같은 사용자로 돌린다.
- `db/inputs.json`(라벨 `io.openit.inputs`)의 process 와 모든 `written_by` 를 999:999 로, capabilities 를 빈 목록으로 한다.
- 진입점은 root 전제를 버린다: 인증서 사본은 소유자 지정 없이 만든다(이미 postgres), 비밀 사본 단계를 없애고 배치가 준 비밀 파일을 그대로 읽는다(없거나 첫 줄이 비면 시작하지 않는 검사는 유지), 인증서 갱신 신호는 `gosu` 없이 `kill -HUP`, HEALTHCHECK 도 `gosu` 없이 실행한다.

## 왜
- 표준: 비 root 실행(공통 규칙), 최소 권한(capability 0), ContainerInputs 의 "선언 = 실제 실행 사용자". 같은 방식이 `platform/identity/Dockerfile.db`(`USER postgres`, 선언 999:999, capability 없음)에 이미 있다.
- 비밀 사본이 필요했던 이유는 root 가 gosu 로 내려가며 보조 그룹이 초기화되어 0640 비밀을 못 읽는 것이었다. 처음부터 postgres 로 실행하면 배치가 선언의 uid:gid 로 준 파일을 직접 읽으므로 사본도, 권한 완화도 필요 없다.
- 측정한 것은 없다. 근거는 표준과 위 결함 사례다.

## 검토한 대안과 버리는 이유
- root 유지 + gosu: 공통 규칙 위반이고 capability 5개가 필요하다. 버린다.
- 임의 uid(예: 10001) 새로 만들기: 공식 이미지의 데이터 폴더·initdb 가 postgres 를 가정한다. 공식 이미지와 달라질 이유가 없고 identity-db 와도 어긋난다. 버린다.
- 선언만 0:0 으로 두기: 규칙 위반을 그대로 두는 것이다. 버린다.

## 결과
- 얻는 것: root 없음, capability 없음, 비밀 사본 없음, 선언과 실제 사용자가 빌드 때 대조된다(`scripts/build-image.sh` 의 inputs_check).
- 시험(변이로 지킨다): `tests/test_database_image.py` 는 실제 빌드 위에서 (1) USER 줄을 뺀 Dockerfile 이 빌드 단계의 선언 대조에서 거부되고 이미지가 지워지는 것, (2) 선언을 0:0 으로 바꾼 것이 거부되는 것, (3) 원본은 통과하는 것, (4) 컨테이너 안 `id` 가 선언과 같은 것을 본다. 시험 컨테이너는 capability 없이, `/run` 을 선언한 uid:gid 소유 tmpfs 로 띄운다.
- 감수하는 것 / 전제: **deploy compose 를 같이 바꿔야 기동한다.** 이 DB 서비스의 `cap_add` 를 제거하고, `/run` tmpfs 를 `uid=999,gid=999` 로 준다(`/tmp` 도 선언대로 쓸 수 있어야 한다). 비밀·인증서 파일은 999:999 가 읽을 수 있어야 한다. compose 는 deploy(헤드) 소유라 이 영역은 고치지 않는다. 바뀌기 전의 compose 로는 `/run` 에 쓰지 못해 인증서 복사에서 멈춘다. 이 영역에서는 deploy 의 실제 compose 로 기동해 보지 않았다.
- 기존 데이터 볼륨: 고정 digest 의 `postgres:16` 이미지의 `/usr/local/bin/docker-entrypoint.sh`(`docker_create_db_directories`)를 읽었다. root 로 시작하면 PGDATA(`/var/lib/postgresql/data`)와 `/var/run/postgresql` 의 postgres 소유가 아닌 항목을 `chown postgres` 한다. 옛 진입점은 root 로 시작해 이 경로를 탔으므로 기존 볼륨은 이미 postgres(999) 소유일 가능성이 크다(읽은 것은 진입점과 이미지 기본 소유자 postgres 뿐이다). 운영 볼륨의 실제 소유자는 확인하지 않았다. 이 영역에는 운영 데이터가 없다는 전제이며 이것도 확인하지 않았다.

## 되돌리는 조건
공식 postgres 이미지가 postgres 이외의 사용자나 root 시작을 전제하도록 바뀌거나, 배포가 999:999 로 파일을 줄 수 없는 환경이면 다시 정한다. 그때는 새 ADR 이 이 결정을 대체한다.
