# source 해서 쓴다(docker 를 부르는 스크립트의 맨 위). 사용자 전역 ~/.docker 설정(자격 증명 도우미, 플러그인 경로, 현재 컨텍스트)에 기대지 않게 한다.
# 왜: 같은 명령이 사용자마다 다르게 동작하면(자격 증명 도우미가 없어 pull 이 실패하는 등) 재현이 안 된다. 표준: Docker 의 DOCKER_CONFIG(설정 폴더) 와 DOCKER_HOST(엔진 주소).
#  - DOCKER_CONFIG 가 주어지면 그대로 둔다(호출자가 정한 것).
#  - 없으면 리포 build/docker-config 의 빈 설정({})을 쓴다. 사용자 ~/.docker 는 읽지도 고치지도 않는다.
#  - 현재 컨텍스트의 엔진이 기본 소켓이 아니면 docker CLI 가 알려 주는 주소를 DOCKER_HOST 로 옮긴다(설정 폴더를 바꾸면 컨텍스트가 사라지므로). 파일을 직접 읽지 않는다.
_qa_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -z "${DOCKER_CONFIG:-}" ]; then
  if [ -z "${DOCKER_HOST:-}" ]; then
    _qa_ep="$("${DOCKER:-docker}" context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null || true)"
    case "$_qa_ep" in ""|unix:///var/run/docker.sock) ;; *) export DOCKER_HOST="$_qa_ep" ;; esac
  fi
  mkdir -p "$_qa_root/build/docker-config"
  [ -f "$_qa_root/build/docker-config/config.json" ] || printf '{}\n' > "$_qa_root/build/docker-config/config.json"
  export DOCKER_CONFIG="$_qa_root/build/docker-config"
fi
unset _qa_root _qa_ep
