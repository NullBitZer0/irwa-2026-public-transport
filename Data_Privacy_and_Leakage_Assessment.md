# AI Vulnerability Assessment Report

## Privacy and Data Leakage Assessment

| Field | Value |
|---|---|
| **System under assessment** | LankaJourney AI — agentic public transport planning and booking system |
| **Author** | [Your name] |
| **Evaluation specialisation** | Student 2 — Privacy and Data Leakage Assessment |
| **Module** | IT 3041 — Information Retrieval and Web Analytics |
| **Assessment type** | Individual privacy and data protection assessment |
| **Assessment date** | 8 October 2026 |
| **Evidence artefacts** | `evaluation/test_student2_privacy.py`, `student2-test-evidence.txt`, and the screenshots in `evidence/student2_privacy/` |
| **Test harness** | `pytest evaluation/test_student2_privacy.py -v` |
| **Classification** | Academic assessment — contains no real personal data |

**Assessment status:** 15 Student 2 probes executed; all 15 test functions passed. S2-15 passing means the cross-account disclosure was reproduced, so it is a confirmed finding rather than a secure outcome. Attach the terminal output or screenshot as evidence.

---

## 1. Executive Summary

This assessment reviews account authentication, session handling, profile data, payment card handling, PII tokenization, and conversation history access. Source review shows several privacy controls: passwords use scrypt hashes, session tokens are stored as SHA-256 digests, profile responses use an explicit public-field projection, card profiles retain only brand and last four digits, and the PII tokenizer encrypts values held in its in-memory vault.

One significant access control weakness was identified. The conversation list endpoint returns the global conversation list, and the transcript endpoint checks only that a caller is authenticated. Neither endpoint verifies that the requested conversation belongs to that caller. The included probe creates a transcript as one account and reads it through a second account. This exposes private trip queries and any personal details written into conversation messages to any signed-in user who can obtain a conversation ID; list access also reveals conversation summaries and previews.

## 2. Scope and Methodology

The review covered `src/orchestrator/accounts.py`, `src/orchestrator/server.py`, `src/orchestrator/conversations.py`, `src/security/pii_masker.py`, and the Student 2 probe file. Testing is designed as a mix of direct store checks, API checks using FastAPI's `TestClient`, and a cross-account transcript access scenario. The project pytest suite redirects the security audit log to a temporary file through `evaluation/conftest.py`.

The probes were run locally with synthetic identities and data only. No real NIC, telephone, card, or traveller information was used. The saved evidence file reports 15 passed in 3.91 seconds. Preserve that output or a screenshot as submission evidence.

## 3. Test Cases Performed

The following 15 probes were executed. “Observed result” records the outcome shown in the provided pytest run. S2-15 is an exploit reproduction test: its passing assertion confirms the vulnerability.

| ID | Objective and input/scenario | Expected result | Observed result |
|---|---|---|---|
| S2-01 | Register an account; inspect the session cookie. | Cookie is HttpOnly. | **Passed** — HttpOnly cookie observed. |
| S2-02 | Log in and inspect SameSite attribute. | Cookie uses SameSite=Lax. | **Passed** — SameSite=Lax observed. |
| S2-03 | Compare wrong-password and unknown-email login errors. | Same status and public message; no account enumeration by message. | **Passed** — both returned the same 401 message. |
| S2-04 | Create account with a test password; inspect stored password field. | Plaintext password is absent; hash uses scrypt format. | **Passed** — stored password was a scrypt hash. |
| S2-05 | Project an account row to the public profile. | Password hash and digest are absent. | **Passed** — public profile omitted the password hash. |
| S2-06 | Create a session and compare raw token with stored token. | Only token digest is stored. | **Passed** — stored session token was hashed. |
| S2-07 | Register, log out, then request `/auth/me`. | Session is invalidated and the request returns 401. | **Passed** — logout invalidated the session. |
| S2-08 | Request `/auth/me` without a cookie. | Request is rejected with 401. | **Passed** — unauthenticated request returned 401. |
| S2-09 | Submit a synthetic valid card number to `/profile`. | Response contains only brand and last four digits. | **Passed** — full card number was absent from response. |
| S2-10 | Attempt to write `card_number` directly through `AccountStore`. | Full PAN field is rejected and not stored. | **Passed** — direct storage attempt was rejected. |
| S2-11 | Tokenize old-format synthetic NIC `912345678V`. | Original identifier is absent from output. | **Passed** — NIC was redacted. |
| S2-12 | Tokenize new-format synthetic NIC `200012345678`. | Original identifier is absent from output. | **Passed** — NIC was redacted. |
| S2-13 | Tokenize synthetic phone `0771234567` and passport `N1234567`. | Both originals are absent from output. | **Passed** — phone and passport were redacted. |
| S2-14 | Inspect tokenizer vault after redaction, then clear it. | Vault value is encrypted; clear removes lookup capability. | **Passed** — ciphertext was stored and vault clear removed lookup. |
| S2-15 | Account A creates a transcript; authenticated account B requests its list and transcript. | B receives no A conversation summary or transcript (403/404). | **Finding reproduced** — test passed because account B received account A's transcript and its summary. This is a confirmed cross-account privacy vulnerability. |

## 4. Vulnerability Identified

### S2-V1: Cross-account conversation history disclosure

**Description:** The authenticated conversation APIs are not scoped to the authenticated account. `GET /conversations` calls `CONVERSATIONS.list()` without a user or owner filter. `GET /conversations/{conversation_id}` retrieves a conversation by ID and checks only whether it exists. The conversation store records a `session_id`, but the API does not use it to authorize reads, and the new-conversation endpoint generates a session ID without associating it with the account identity.

**Evidence:** In the provided `pytest evaluation/test_student2_privacy.py -v` run, `test_s2_15_authenticated_user_can_read_another_users_transcript` passed by creating a private message under one account and successfully reading it from a second signed-in account. Preserve the terminal output and, if capturing the response separately, redact session cookies and use only the synthetic test text.

**Impact:** A signed-in user can inspect other travellers' trip queries and conversation previews/transcripts. If users enter personal identifiers or contact details into the chat, those messages may also be exposed. The scope is potentially all conversations stored by this service instance.

**Likelihood:** Medium. An attacker needs a valid account and a conversation identifier for direct transcript retrieval; the unscoped list endpoint also exposes conversation IDs and previews to any authenticated account, making discovery easier.

**Severity / risk level:** **High.** The data is private user history, the disclosure crosses account boundaries, and the list endpoint enables discovery. Authentication limits access to registered users but does not provide authorization between users.

**Technical explanation:** Authentication establishes a caller identity, but the handlers do not enforce object-level authorization. The user dependency is present, yet its `user` result is unused in both conversation read handlers. A random-looking identifier is not an authorization control.

**Mitigation:** Persist an immutable owner user ID on each conversation. Set it from the authenticated account when creating the conversation. Require that owner ID on list, transcript, append, and associated transaction reads; return 404 for another user's conversation to avoid confirming its existence. Add regression tests for two-account list and transcript isolation.

## 5. Risk Summary

| Finding | Impact | Likelihood | Risk |
|---|---|---|---|
| Cross-account conversation list/transcript access (S2-V1) | High | Medium | **High** |
| PII may be included in conversation history and SQLite transcript storage | Medium | Medium | **Medium** (review retention, encryption-at-rest, and avoid requesting unnecessary identifiers) |
| Secure cookie flag depends on deployment configuration | Medium | Low to medium | **Low/Medium** (set `COOKIE_SECURE=true` when served over HTTPS) |

The last two rows are review observations, not confirmed exploitation findings. Conversation messages are stored as text in SQLite and archived conversations are retained indefinitely according to the store comments. Deployment should define a retention policy and protect database files and backups. The session cookie's `Secure` attribute is enabled only when `COOKIE_SECURE` is set to a truthy value; verify production configuration.

## 6. Mitigation Priorities

1. Add owner identity to conversations and enforce ownership on every read/write path.
2. Adopt a documented conversation retention/deletion policy and protect database files and backups with appropriate access controls and encryption at rest.
3. Set `COOKIE_SECURE=true` in HTTPS deployments and verify the resulting cookie attributes.
4. Keep collecting only the PII required for a booking; document the boundary between tokenized chat data and booking-provider data.

## 7. Reflection

The main challenge was distinguishing authentication from authorization. A successful sign-in check can still leave records shared across users if each object is not checked against the authenticated owner. Testing used synthetic accounts and identifiers so the probes do not create real personal data. The key improvement is to make ownership part of the stored conversation model and verify it in the API, then retain the cross-account test as a regression check.

## 8. Running and Capturing Evidence

From the repository root, activate the project's Python environment and install dependencies if needed. To launch the complete system for a UI demonstration, run:

```powershell
docker compose up -d --build
```

Open `http://localhost:3000`. To run the privacy probes, use another terminal in the repository root. The tests use FastAPI's in-process `TestClient`, so they do not require a running Docker stack or separate server process.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
pytest evaluation/test_student2_privacy.py -v
```

The suite runs in-process with `TestClient`, so a separate `uvicorn` process is not required for these tests. For the complete evaluation suite, run:

```powershell
pytest evaluation -v
```

The saved run completed with **15 passed in 3.91 seconds**. Save terminal output with `pytest evaluation/test_student2_privacy.py -v | Tee-Object student2-test-evidence.txt` or attach a terminal screenshot. S2-15 passing is evidence that the access-control vulnerability is reproducible; it does not mean that case met its expected security result. Do not include real credentials, session cookies, NICs, phone numbers, or payment data in submitted evidence.

## Appendix A. Evidence Images

Four readable evidence images are included in the downloadable assessment bundle:

- `student2_pytest_evidence_1.png` — test output for S2-01 through S2-05.
- `student2_pytest_evidence_2.png` — test output for S2-06 through S2-10.
- `student2_pytest_evidence_3.png` — test output for S2-11 through S2-15.
- `student2_s2_15_cross_account_reproduction.png` — S2-15 source assertions that reproduce the cross-account disclosure.

These images are rendered extracts of the saved pytest output and test source, not photographs of a live desktop session. The downloadable bundle also includes the original text output and the PDF report.
