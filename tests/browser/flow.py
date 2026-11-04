"""저작 화면 브라우저 시나리오(Playwright, 공식 이미지 안에서 돈다). 인자: 기준 주소. 종료 코드 0 = 모든 단언 통과, 1 = 단언 실패(메시지는 표준 출력).
화면 결과(DOM 텍스트)를 단언한다: 로그인 표시, 새 초안, 자료 올리기, 확정, 버전·판정, 검수 결정, 신호."""
import sys

from playwright.sync_api import expect, sync_playwright

base = sys.argv[1]
expect.set_options(timeout=20000)


def wait_reachable(url):
    """이 하네스의 도커 엔진(호스트 포트 전달)은 새로 열린 포트가 컨테이너에서 닿기까지 지연이 있고 부하에서 빈 응답이 난다. 조건(응답이 온다)으로 기다린 뒤 시작한다(제품 동작이 아니라 시험 환경의 준비)."""
    import time
    import urllib.error
    import urllib.request
    end = time.monotonic() + 60
    ok = 0
    while time.monotonic() < end:
        try:
            urllib.request.urlopen(url, timeout=5)
        except urllib.error.HTTPError:
            ok += 1                       # 응답이 왔다(로그인 전이라 401)
        except OSError:
            ok = 0
        else:
            ok += 1
        if ok >= 5:                       # 연속 다섯 번 응답: 간헐적 빈 응답 구간을 지난다
            return
        time.sleep(0.2)
    raise RuntimeError("대역에 닿지 못한다")


def step(name):
    print("STEP", name, flush=True)


trace = []
wait_reachable(base + "/oauth2/userinfo")
with sync_playwright() as p:
    b = p.chromium.launch()
    try:
        kim = b.new_context()
        kim.add_cookies([{"name": "who", "value": "kim", "url": base}])
        page = kim.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("requestfailed", lambda r: trace.append(f"REQFAIL {r.url} {r.failure}"))        # 실패 때 원인을 보이려는 기록(단언에는 쓰지 않는다)
        page.on("request", lambda r: trace.append(f"REQ {r.url}"))
        page.on("console", lambda m: trace.append(f"CONSOLE {m.type} {m.text[:120]}"))
        page.goto(base + "/ui/")
        step("login shown")
        expect(page.locator("#who")).to_contain_text("dr-kim")
        expect(page.locator("#who")).to_contain_text("author")

        step("new draft")
        page.fill("#new-draft [name=question_set_id]", "qs-ui1")
        page.click("#new-draft button[type=submit]")
        expect(page.locator("#msg")).to_contain_text("를 만들었다")
        expect(page.locator("#detail-title")).to_contain_text("qs-ui1")

        step("upload material")
        page.click("[data-tab=upload]")
        page.fill("#upload-form [name=question_set_id]", "qs-ui2")
        page.set_input_files("#upload-form [name=file]", "/t/form.txt")
        page.click("#upload-form button[type=submit]")
        expect(page.locator("#msg")).to_contain_text("올렸다")
        expect(page.locator("#preview")).to_contain_text("qs-ui2")

        step("confirm (submit for review)")
        expect(page.locator("#confirm")).to_be_enabled()
        page.click("#confirm")
        expect(page.locator("#msg")).to_contain_text("확정했다: qs-ui2@1")

        step("versions list and gate decision")
        page.click("[data-tab=versions]")
        row = page.locator("#versions-table tbody tr", has_text="qs-ui2")
        expect(row).to_contain_text("예시 문진표")
        expect(row).to_contain_text("아직")                    # 저자 확정만으로는 검수 승인이 아니다(2인 승인)
        expect(row).to_contain_text("반려")                    # release-gate 판정(읽기만)

        step("review decision as the clinical reviewer")
        park = b.new_context()
        park.add_cookies([{"name": "who", "value": "park", "url": base}])
        pg2 = park.new_page()
        pg2.goto(base + "/ui/")
        expect(pg2.locator("#who")).to_contain_text("clinical-reviewer")
        pg2.click("[data-tab=review]")
        expect(pg2.locator("#review-form [name=ref] option")).to_have_count(1)
        pg2.select_option("#review-form [name=decision]", "APPROVED")
        pg2.check("#review-form [name=clinical_basis_approved]")
        pg2.click("#review-form button[type=submit]")
        expect(pg2.locator("#msg")).to_contain_text("검수 결정을 남겼다")

        step("versions show approval")
        pg2.click("[data-tab=versions]")
        expect(pg2.locator("#versions-table tbody tr", has_text="qs-ui2")).to_contain_text("승인됨")

        step("signals")
        pg2.click("[data-tab=signals]")
        pg2.click("#signals-form button[type=submit]")
        expect(pg2.locator("#signals-out")).to_contain_text("DISABLED")

        step("author cannot approve (server decides, screen shows the error)")
        page.click("[data-tab=review]")
        page.select_option("#review-form [name=decision]", "APPROVED")
        page.check("#review-form [name=clinical_basis_approved]")
        page.click("#review-form button[type=submit]")
        expect(page.locator("#msg")).to_contain_text("PERMISSION_DENIED")
        assert not errors, errors
        print("DONE", flush=True)
    except Exception as e:           # noqa: BLE001
        print("FAIL", type(e).__name__, str(e)[:400], flush=True)
        print("TRACE", " | ".join(globals().get("trace", [])[-12:]), flush=True)
        sys.exit(1)
    finally:
        b.close()
