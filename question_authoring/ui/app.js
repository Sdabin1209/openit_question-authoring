// 문진표 저작 화면. 저작 API(AuthoringRequests·AuthoringResponses·SignalsResponse)만 부른다. 같은 출처의 상대 경로만 쓴다(호스트를 코드에 넣지 않는다).
// 권한 판단은 platform 공개 입구와 서비스가 한다. 이 화면은 보여 주고 요청을 보낼 뿐이다.
// 서버 값은 textContent 로만 넣는다(마크업 삽입 API 금지: 자료에 든 문장이 마크업으로 해석되지 않게).
"use strict";
const AUTH = {login: "/oauth2/start", logout: "/oauth2/sign_out", userinfo: "/oauth2/userinfo",
              idp_logout: "/auth/realms/openit/protocol/openid-connect/logout", client_id: "openit-gateway"};   // HumanAuthEndpoints 의 값 그대로
const $ = (s, r = document) => r.querySelector(s);
let current = null;      // 열린 초안 {draft_id, ...}

function say(text, kind) { const m = $("#msg"); m.textContent = text; m.className = kind || ""; }

async function api(method, path, body, raw) {
  const opt = {method, headers: {"Accept": "application/json"}, credentials: "same-origin", redirect: "manual"};
  if (body !== undefined) { opt.body = raw ? body : JSON.stringify(body); if (!raw) opt.headers["Content-Type"] = "application/json"; }
  let r;
  try { r = await fetch(path, opt); } catch (e) { throw {code: "NETWORK", message: "서버에 닿지 못했다. 잠시 뒤 다시 시도한다.", retryable: true}; }
  if (r.type === "opaqueredirect" || r.status === 302) { location.href = AUTH.login + "?rd=" + encodeURIComponent(location.pathname); throw {code: "LOGIN", message: "로그인이 필요하다."}; }
  let data = null; try { data = await r.json(); } catch (e) { /* 본문 없음 */ }
  if (r.status === 401) { location.href = AUTH.login + "?rd=" + encodeURIComponent(location.pathname); throw {code: "LOGIN", message: "로그인이 필요하다."}; }
  if (!r.ok) {
    const d = data || {};
    const retry = r.headers.get("Retry-After");
    throw {code: d.code || ("HTTP_" + r.status), message: d.message || "요청을 처리하지 못했다", retryable: !!d.retryable || r.status === 503, retryAfter: retry, status: r.status, details: d.details};
  }
  return data;
}
function fail(e) {
  if (e && e.code === "LOGIN") return;
  const extra = e && e.retryable ? (e.retryAfter ? ` (${e.retryAfter}초 뒤 다시 시도)` : " (다시 시도할 수 있다)") : "";
  say(`${(e && e.code) || "ERROR"}: ${(e && e.message) || e}${extra}`, "err");
}
function el(tag, text, cls) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; }

async function whoami() {
  try {
    const r = await fetch(AUTH.userinfo, {credentials: "same-origin", redirect: "manual"});
    if (!r.ok) { location.href = AUTH.login + "?rd=" + encodeURIComponent(location.pathname); return; }
    const u = await r.json();
    const groups = (u.groups || []).map(g => g.replace(/^\//, ""));
    $("#who").textContent = `${u.preferredUsername || u.user || ""} (${groups.join(", ") || "역할 없음"})`;
    const lo = $("#logout");
    lo.hidden = false;
    lo.href = AUTH.logout + "?rd=" + encodeURIComponent(AUTH.idp_logout + "?client_id=" + AUTH.client_id + "&post_logout_redirect_uri=" + encodeURIComponent(location.origin + "/ui/"));
    if (!groups.includes("author") && !groups.includes("clinical-reviewer")) say("이 화면을 쓸 역할(author, clinical-reviewer)이 없다. 가입 승인을 기다리거나 관리자에게 알린다.", "err");
  } catch (e) { $("#who").textContent = "로그인 상태를 확인하지 못했다"; }
}

function tab(name) {
  document.querySelectorAll("#tabs button").forEach(b => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  ["drafts", "upload", "review", "signals"].forEach(t => { $("#tab-" + t).hidden = t !== name; });
  if (name === "review") loadReviewChoices();
}

async function loadDrafts() {
  const tb = $("#draft-table tbody"); tb.replaceChildren();
  const out = await api("GET", "/v1/drafts");
  (out.drafts || []).slice().reverse().forEach(d => {
    const tr = document.createElement("tr");
    const ref = d.confirmed && d.confirmed.ref;
    [d.draft_id, d.question_set_id, d.status, ref ? `${ref.id}@${ref.version}` : "", "", d.updated_at].forEach(v => tr.appendChild(el("td", v)));
    tr.addEventListener("click", () => openDraft(d.draft_id));
    tb.appendChild(tr);
  });
}

async function openDraft(id) {
  const [d, pv] = await Promise.all([api("GET", "/v1/drafts/" + encodeURIComponent(id)), api("GET", "/v1/drafts/" + encodeURIComponent(id) + "/preview")]);
  current = {draft: d, preview: pv};
  $("#detail").hidden = false;
  $("#detail-title").textContent = `초안 ${d.draft_id} (${d.question_set_id}, ${d.status})`;
  $("#preview").textContent = pv.text;
  const fl = $("#flags"); fl.replaceChildren();
  pv.open_flags.forEach(f => {
    const row = el("div", "", "flag");
    row.appendChild(el("span", `[${f.kind}] ${f.path}: ${f.message}${f.raw ? " 원문: " + f.raw : ""} `));
    const b = el("button", "맞다고 확인"); b.type = "button";
    b.addEventListener("click", () => act(async () => { await api("POST", `/v1/drafts/${encodeURIComponent(id)}/acknowledge`, {flag_ids: [f.flag_id]}); await openDraft(id); }));
    row.appendChild(b); fl.appendChild(row);
  });
  const v = $("#validation"); v.replaceChildren();
  pv.validation_errors.forEach(e => v.appendChild(el("div", "검증 오류: " + e, "flag")));
  if (pv.change) v.appendChild(el("div", "변경: " + pv.change.text));
  $("#confirm").disabled = !(pv.ready_to_confirm);
  const open = d.status === "OPEN";
  $("#edit-form").hidden = !open; $("#discard").disabled = !open;
}
async function act(fn) { try { await fn(); } catch (e) { fail(e); } }

async function loadReviewChoices() {
  const sel = $("#review-form [name=ref]"); sel.replaceChildren();
  try {
    const out = await api("GET", "/v1/drafts");
    (out.drafts || []).filter(d => d.confirmed && d.confirmed.ref).forEach(d => {
      const r = d.confirmed.ref; const o = el("option", `${r.id}@${r.version} (초안 ${d.draft_id})`); o.value = JSON.stringify(r); sel.appendChild(o);
    });
    if (!sel.options.length) say("확정된 버전이 없다. 초안을 확정하면 여기에 나온다.");
  } catch (e) { fail(e); }
}

function renderSignals(s) {
  const box = $("#signals-out"); box.replaceChildren();
  if (s.status !== "OK") { box.appendChild(el("p", `신호를 만들 수 없다(${s.status}): ${s.reason || ""}`)); return; }
  box.appendChild(el("p", `읽은 사례 ${s.cases_read}건${s.truncated ? " (상한에서 잘림)" : ""}, 생성 ${s.generated_at}. ${s.note || ""}`));
  s.question_sets.forEach(q => {
    box.appendChild(el("h3", `${q.question_set_id}: 사례 ${q.case_count}건, 확정 ${q.confirmed_count}건`));
    const t = document.createElement("table");
    const h = document.createElement("tr"); ["문항", "사례", "확정", "실패 유형"].forEach(x => h.appendChild(el("th", x))); t.appendChild(h);
    q.questions.forEach(x => { const r = document.createElement("tr");
      [x.question_id, x.case_count, x.confirmed_count, Object.entries(x.failure_types).map(([k, n]) => `${k} ${n}`).join(", ")].forEach(v => r.appendChild(el("td", String(v)))); t.appendChild(r); });
    box.appendChild(t);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  whoami();
  document.querySelectorAll("#tabs button").forEach(b => b.addEventListener("click", () => tab(b.dataset.tab)));
  $("#reload").addEventListener("click", () => act(loadDrafts));
  $("#new-draft").addEventListener("submit", ev => { ev.preventDefault(); act(async () => {
    const f = new FormData(ev.target); const body = {question_set_id: f.get("question_set_id")};
    if (f.get("base_version")) body.base_version = f.get("base_version");
    const d = await api("POST", "/v1/drafts", body); say(`초안 ${d.draft_id} 를 만들었다.`, "ok"); await loadDrafts(); await openDraft(d.draft_id); }); });
  $("#upload-form").addEventListener("submit", ev => { ev.preventDefault(); act(async () => {
    const f = new FormData(ev.target); const file = f.get("file");
    const q = new URLSearchParams({question_set_id: f.get("question_set_id"), filename: file.name});
    if (f.get("base_version")) q.set("base_version", f.get("base_version"));
    if (f.get("accept_phi_warning")) q.set("accept_phi_warning", "true");
    const out = await api("POST", "/v1/materials?" + q, await file.arrayBuffer(), true);
    say(out.status === "PARSED" ? `올렸다. 확인이 필요한 항목 ${out.open_flags}개.` : `읽지 못했다: ${out.reason}. 초안에서 직접 채울 수 있다.`, out.status === "PARSED" ? "ok" : "err");
    tab("drafts"); await loadDrafts(); await openDraft(out.draft_id); }); });
  $("#edit-form").addEventListener("submit", ev => { ev.preventDefault(); act(async () => {
    const f = new FormData(ev.target); const e = {op: f.get("op"), path: f.get("path")};
    if (f.get("op") !== "remove") { try { e.value = JSON.parse(f.get("value")); } catch (x) { throw {code: "INVALID_RECORD", message: "값은 JSON 이어야 한다(문장은 큰따옴표로 감싼다)."}; } }
    await api("POST", `/v1/drafts/${encodeURIComponent(current.draft.draft_id)}/edits`, {edits: [e]}); say("적용했다.", "ok"); await openDraft(current.draft.draft_id); }); });
  $("#confirm").addEventListener("click", () => act(async () => {
    const ref = await api("POST", `/v1/drafts/${encodeURIComponent(current.draft.draft_id)}/confirm`, {preview_hash: current.preview.preview_hash});
    say(`확정했다: ${ref.id}@${ref.version}`, "ok"); await loadDrafts(); await openDraft(current.draft.draft_id); }));
  $("#discard").addEventListener("click", () => act(async () => {
    const reason = prompt("버리는 사유"); if (!reason) return;
    await api("POST", `/v1/drafts/${encodeURIComponent(current.draft.draft_id)}/discard`, {reason}); say("버렸다.", "ok"); await loadDrafts(); $("#detail").hidden = true; }));
  $("#review-form").addEventListener("submit", ev => { ev.preventDefault(); act(async () => {
    const f = new FormData(ev.target); const body = {question_set: JSON.parse(f.get("ref")), decision: f.get("decision"), clinical_basis_approved: !!f.get("clinical_basis_approved")};
    if (f.get("reason")) body.reason = f.get("reason");
    await api("POST", "/v1/reviews", body); say("검수 결정을 남겼다.", "ok"); }); });
  $("#signals-form").addEventListener("submit", ev => { ev.preventDefault(); act(async () => {
    const f = new FormData(ev.target); const q = new URLSearchParams();
    if (f.get("question_set_id")) q.set("question_set_id", f.get("question_set_id"));
    if (f.get("max_cases")) q.set("max_cases", f.get("max_cases"));
    renderSignals(await api("GET", "/v1/signals?" + q)); }); });
  act(loadDrafts);
});
