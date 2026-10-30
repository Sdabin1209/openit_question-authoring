# CR-0008: openit_link v0.1.0에 필요한 것 (헤드 소유 라이브러리)

question-authoring이 link_authoring_server를 openit_link.App/Route 위에 옮기면서 만난 부족한 점이다. 영역에서 우회 구현하지 않고 아래대로 요청한다.

1. **`request_schema`·`response_schema`가 계약 파일 전체만 가리킨다.** 이 영역의 API 계약은 `AuthoringRequests.schema.json#/$defs/<이름>`, `AuthoringResponses.schema.json#/$defs/<이름>`처럼 파일 하나에 $defs 여럿이다. 요청: `"AuthoringRequests#Edit"` 같은 `파일#$defs이름` 지정을 받아 달라. 지금은 `raw_body=True`로 받고 영역 코드가 `$defs`로 검증한다(규칙을 영역에서 다시 구현하는 것이므로 임시).
2. **쿼리 검증이 없다.** `MaterialQuery`, `SignalsQuery`는 쿼리 문자열을 스키마로 검증한다. 요청: `Route.query_schema`.
3. **토큰 비교가 상수 시간이 아니다.** v0.1.0은 `dict.get`으로 토큰을 찾는다. 이 영역은 이전에 모든 후보를 `hmac.compare_digest`로 끝까지 비교했다(ADR 0010/0013, 표준성 점검 #12). 요청: 라이브러리가 상수 시간 비교를 한다. 그 전까지 타이밍 보호가 약해졌다.
4. **토큰이 가리키는 호출자가 역할 문자열 하나뿐이다.** 이 영역은 토큰마다 행위자 ID가 다르다(`QA_AUTHOR_TOKENS` 토큰:ID:역할). 지금은 역할 문자열에 ID를 실어 풀어 쓴다(임시). 요청: 토큰 → (역할, 행위자 ID) 또는 처리 함수가 호출자를 고르는 훅.
5. **알려진 경로에 다른 메서드는 405**, 이 영역은 이전에 인증 뒤 404였다. 계약 규약에 맞는 쪽으로 시험을 고쳤다(문제 없음, 알림).
6. **BuildImageRequest에 통신 라이브러리 소스 자리가 없다.** `dependency_sources`/`versions`에 `link` 키가 없어 build-image.sh가 requirements.lock의 `openit-link @ git+ssh://...@태그` 줄로 호스트에서 wheel을 만든다(ssh로 GitHub에 닿아야 한다). 요청: `dependency_sources.link`, `versions.link`를 계약에 추가해 다른 라이브러리처럼 deploy가 태그에서 소스를 꺼내 주게 한다.
