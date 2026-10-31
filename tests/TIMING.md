# 시험 안의 시간 (U422)

원칙: 시험 중 실제 시간을 기다리거나 재서 판정하는 곳은 없다. 합격·불합격은 결과(오류 종류·상태·재시도 여부·넘긴 값)로 정한다.
처리 구분: **1** 우리 코드의 시간 → 시계 주입(ADR 0024), 시험이 가짜 시계를 흘림 / **2** 라이브러리 안의 시간 → (a) 넘긴 값 확인 (b) 가짜 의존이 즉시 예외 / **3** 속도 측정 → 시간 단언 삭제, 결과만 / **지움** / **매달림 감시** → `tests/helpers.py`의 `HANG_GUARD_SECONDS`(300) 하나.

## 바뀐 시험

| 파일 | 시험(이전) | 목적 | 처리 | 바뀐 시험 |
|---|---|---|---|---|
| test_extractor_scale.py | test_long_whitespace_line_is_linear, test_many_slot_lines, test_many_slots_within_limit_are_fast, test_many_question_lines, test_8mib_inputs_finish_fast | 큰 입력에서 추출기가 제곱 시간으로 느려지지 않고, 한도를 넘으면 알린다 | 3 | 같은 이름. `took < N` 삭제, 결과(플래그·ExtractionFailure·개수)만 단언. 끝나는지는 `run_bounded`(매달림 감시) |
| test_extractor_scale.py | test_time_budget_is_enforced | 추출 시간 예산을 넘으면 포기한다 | 1 | test_time_budget_is_enforced_with_an_injected_clock, test_time_budget_value_is_the_documented_one (추출기에 시계 주입) |
| test_extractor_scale.py | test_healthz_answers_while_a_large_upload_is_extracted | 큰 업로드를 처리하는 동안 서버가 다른 요청에 답한다 | 2 (waitress 스레드 모델) | test_large_upload_is_extracted_inside_a_worker_thread_not_the_server_loop (처리 스레드 수·본문 상한 값 확인) |
| test_two_person.py | test_phi_check_scales_linearly_with_phone_and_date_text | 환자정보 정규식이 제곱 시간이 아니다 | 3 | test_phi_check_finishes_on_phone_and_date_text (`run_bounded`, 시간 단언 없음) |
| test_fault_paths.py | test_phi_check_is_linear_on_long_lines_and_still_detects | 위와 같음(긴 한 줄) | 3 | test_phi_check_finishes_on_long_lines_and_still_detects |
| test_fault_paths.py | test_stalled_upload_is_cut_and_nothing_is_saved | 멈춘 업로드를 서버가 끊는다 | 2 (waitress) | 지움. 값 확인은 test_server_properties.py::test_make_server_passes_the_limits_to_waitress |
| test_fault_paths.py | test_client_disconnect_mid_upload_saves_nothing | 끊긴 업로드는 아무것도 저장하지 않는다 | 2 (waitress가 모자란 본문을 앱에 안 넘김) | test_upload_is_atomic_when_extraction_fails (처리가 실패하면 반쯤 처리된 초안이 없다) |
| test_fault_paths.py | test_d5_slow_upstream_is_bounded_by_call_timeout | 상류가 느리면 호출 상한이 끊는다 | 2 (urllib 소켓 시한) | 지움. test_d5_upstream_timeout_is_503_retryable_and_authoring_is_unaffected (즉시 UNAVAILABLE을 던지는 대역), test_entry_point_passes_signal_timeouts_to_the_api (넘긴 값) |
| test_fault_paths.py | test_d5_total_deadline_bounds_many_slow_pages | 개선 신호 읽기의 총 마감이 끝없는 쪽을 끊는다 | 1 | test_d5_total_deadline_stops_endless_pages_with_an_injected_clock, test_d5_deadline_value_reaches_the_reader |
| test_ops_hardening.py | test_sigterm_stops_the_server_promptly, test_sigterm_stops_the_server_even_when_signals_were_inherited_as_ignored | SIGTERM에 서버가 끝난다 | 2 (신호·waitress) | 지움. test_sigterm_handler_is_installed_before_the_server_starts_listening, test_server_properties.py::test_close_all_ends_the_server_loop_without_any_signal, test_drain_* (종료 절차의 우리 몫, 시계 주입) |
| test_server_properties.py | test_stalled_client_does_not_block_other_requests, test_requests_over_the_limit_wait_instead_of_failing | 느린 연결이 다른 요청을 막지 않는다 / 한도를 넘는 요청은 기다린다 | 2 (waitress) | 지움. test_make_server_passes_the_limits_to_waitress, test_make_server_defaults, test_every_route_has_the_body_limit, test_entry_point_passes_env_limits_to_the_server |
| test_server_properties.py | test_sigterm_finishes_in_flight_request, test_sigterm_gives_up_after_grace | SIGTERM에 처리 중 요청을 마치고 유예 뒤 포기한다 | 1 (drain의 유예) + 2 (신호) | test_drain_stops_accepting_and_reports_requests_unfinished_after_the_grace_period, test_drain_returns_immediately_when_nothing_is_in_flight, test_drain_returns_as_soon_as_in_flight_requests_finish, test_drain_waits_for_pending_response_bytes_until_the_grace_period_ends (시험 시계) |
| test_postgres_specific.py | test_slow_statement_is_cut_by_server_timeout, test_lock_wait_in_write_is_bounded | PostgreSQL이 문장·락 대기를 시한에 자른다 | 2 | 지움. test_server_side_statement_timeout_is_set(값), test_server_side_timeout_is_unavailable_and_not_retried (즉시 QueryCanceled/LockNotAvailable) |
| test_postgres_specific.py | test_readyz_503_within_limit_when_unreachable_then_recovers, test_connect_is_bounded_when_unreachable | 도달 불가 서버에 /readyz·접속이 시한 안에 실패한다 | 2 (psycopg 접속 시한) | 지움. test_limits_are_passed_to_the_driver (접속·확인 연결 인자), test_connect_failure_is_unavailable_without_secrets (즉시 접속 시간 초과 예외, 비밀 비노출) |
| test_postgres_specific.py | test_frozen_server_readyz_503_and_request_path_bounded_then_recovers | docker pause로 얼린 서버에서 요청 경로·/readyz가 시한 안에 끝난다 | 1 (우리 감시 타이머) + 2 (docker pause) | 지움(docker pause). test_frozen_server_is_cut_by_the_clients_own_watchdog (타이머 주입, 넘긴 시한 3.0/8.0), test_readyz_gives_up_when_the_probe_does_not_answer (기다림 주입) |
| test_postgres_specific.py | test_large_path_has_its_own_statement_limit | 큰 자료 경로만 문장 상한이 크다 | 2(a) | test_large_write_uses_the_large_statement_limit_and_plain_write_does_not (삽입 순간의 상한 값을 트리거로 기록) |
| test_postgres_specific.py | test_max_body_sized_upload_fits_default_limit | 8MiB 자료가 큰 자료 경로로 저장·조회된다 | 3 | test_max_body_sized_upload_round_trips (속도 단언 삭제) |
| test_postgres_specific.py | test_readyz_probe_does_not_wait_for_main_connection_or_lock | /readyz 확인이 주 연결·락을 기다리지 않는다 | 우리 코드, 순서 | 같은 이름. `sleep(0.5)` 대신 `wait_until(쓰기가 락 대기 중)`, `<1.0` 대신 `th.is_alive()` |

## 시계를 읽는 시험 코드와 사용 이유(남은 것 전부)

| 위치 | 무엇 | 분류 |
|---|---|---|
| tests/helpers.py `HANG_GUARD_SECONDS` | 매달림 감시 상수(300) | 매달림 감시. 근거는 상수 주석 |
| tests/helpers.py `wait_until` (`time.monotonic`, `time.sleep(0.01)`) | 조건이 참이 될 때까지 기다림. 마감은 HANG_GUARD_SECONDS | 매달림 감시(조건 대기) |
| tests/helpers.py `stop_http`, `run_bounded` | 스레드·서버 루프 종료 대기 `join(HANG_GUARD_SECONDS)` | 매달림 감시 |
| tests/test_postgres_purge_race.py `barrier.wait`, `join`, `p.wait`, `cv.wait_for` | 경쟁 시험의 동기화·종료 대기, 모두 HANG_GUARD_SECONDS | 매달림 감시 |
| tests/test_build_image.py, test_setup_env.py, test_exposure_u47.py, test_auth_401_403.py, test_http_client_conformance.py, test_signals.py, test_fault_paths.py(`raw`, `request_timeout`) | subprocess·소켓·클라이언트의 마감 인자 | 매달림 감시(HANG_GUARD_SECONDS) |
| tests/test_server_properties.py `request_timeout=7, max_concurrent=3` | 서버에 넘긴 값이 waitress 설정에 그대로 닿는지 보려고 일부러 기본값과 다른 숫자를 넘긴다. 기다리지 않는다 | 2(a) 넘긴 값 확인 |
| tests/test_postgres_specific.py `connect_timeout=2, statement_timeout=2/3/0.5, probe_timeout=4, large_statement_timeout=5/7` | 위와 같음: DB 접속 인자·트리거 기록으로 넘긴 값을 확인한다. 기다리지 않는다 | 2(a) 넘긴 값 확인 |
| tests/test_postgres_specific.py `ImmediateTimer`, `wait=lambda …: False`, FakeConn | 시한이 '이미 지났다'고 주입 | 1 주입 |
| tests/test_server_properties.py `FakeClock` | 시험이 쥔 시계(`sleep`을 부를 때만 흐른다) | 1 주입 |
| tests/test_fault_paths.py 개선 신호 마감 시험의 `now` | 시험이 쥔 시계 | 1 주입 |

## 동작 코드의 시계 주입(ADR 0024)

컴포넌트는 시간을 직접 읽거나 기다리지 않는다: `extract_textform.run(inp, clock=)`, `link_failures_read.FailureReader(source, clock=)`, `drain(srv, grace, clock=, sleep=)`, 저장소 백엔드 `now_ts=`·`timer=`·`wait=`, `signals_build.build_signals(now=)`. 실제 시계는 `wiring/service.py`(`monotonic`), `wiring/stores.py`, `wiring/ids.py`, `__main__.py`가 넣는다.

## smoke·E2E에서 봐야 할 것(지운 시험 중, 헤드 판단 요청)

- 실제 SIGTERM에 프로세스가 끝나고 처리 중 요청이 유예 안에 마무리되는가(지운 test_sigterm_* 둘).
- 실제 waitress가 멈춘 업로드를 `request_timeout`에 끊는가, 본문이 모자란 요청을 앱에 넘기지 않는가.
- 실제 PostgreSQL이 얼었을 때(docker pause) 요청 경로·/readyz가 끝나는가, statement_timeout이 문장·락 대기를 자르는가.
