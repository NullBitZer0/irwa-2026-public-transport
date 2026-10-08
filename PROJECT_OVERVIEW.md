# LankaJourney — Project Handover

A complete technical description of LankaJourney: what it is, how it is built,
what each component does, which decisions are deliberate, and — importantly —
where the honest limits are.

**Read this as a single document.** It is written to be pasted into another AI or
engineer and used as the sole source of truth for the system.

---

## 1. What the system is

A conversational public-transport assistant for Sri Lanka. A traveller writes in
English or Singlish, the system finds real intercity services, shows fares and
times, and — with explicit human approval — books seats and issues tickets.

It is a **4-agent multi-agent system** plus a React front end, all in Docker, with
a search backend, a synthetic-but-flagged timetable corpus, and a security posture
that is a graded deliverable rather than an afterthought.

- **Demo account:** `demo@lankajourney.lk` / `demotravel123`
- **UI:** http://localhost:3000
- **Language scope:** English + Singlish (Sinhala is explicitly out of scope)

---

## 2. Architecture

```
Browser (React + nginx)
      │
      ▼  /api/*  →  nginx proxies to orchestrator
Orchestrator (FastAPI, LangGraph)          ← the only agent that faces the user
      │
      ├──► Planner      (FastAPI, port 8001)  — retrieval, NLP, timetables, fares
      ├──► Booking      (FastAPI, port 8002)  — seats, HITL tokens, payment, tickets
      └──► Conditions   (FastAPI, port 8003)  — weather + live transit news
     
Supporting: OpenSearch (9200), one-shot `ingest` profile
```

**Ports (host-side):** frontend `3000`, orchestrator `8100`, planner `8101`,
booking `8102`, conditions `8103`, OpenSearch `9200`.

### 2.1 Orchestrator

`src/orchestrator/` — the only public surface. Everything else is internal
network traffic between agents.

| File | Role |
|---|---|
| `server.py` | HTTP API: auth, chat, payment, conversations, purchases, schedules |
| `main_graph.py` | LangGraph state machine, all agent logic and message rendering |
| `router.py` | Intent classification (LLM with deterministic fallback) |
| `agent_connectors.py` | Typed HTTP bridge to the three sub-agents |
| `session_store.py` | Per-session slot accumulation + offered-service proposals |
| `state.py` | `TransitSessionState` TypedDict |
| `conversations.py` | Conversation persistence, archive-on-payment |
| `accounts.py` | Registration, login, scrypt hashing, card brand detection |

**LangGraph nodes** (`main_graph.py:1419`): `supervisor` → conditional edge to
one of `planning_agent`, `booking_agent`, `hitl_checkpoint`, `faq_node`,
`conditions_node`, `clarify_node`. Every non-supervisor node writes to `messages`
and terminates. There is no loop.

### 2.2 Planner

`src/planner/` — the information-retrieval and NLP worker.

- `hybrid_retriever.py` — sparse (BM25) + dense (k-NN) + RRF fusion
- `connection_planner.py` — one-change itineraries, time/budget ranking
- `journey_search.py` — direct-service search, station keys, major-city set
- `nlp_parser.py` — rule-based English + Singlish entity extraction
- `fares.py` — fitted distance-based fare **estimator** (display only)
- `geo.py` — coordinates, Natural Earth boundary, stop→city mapping
- `generate_*.py`, `ingest_*.py` — corpus ingestion and generation

### 2.3 Booking

`src/booking/` — the execution engine.

- `state_machine.py` — `INITIATED → SEAT_HELD → AWAITING_PAYMENT → CONFIRMED/CANCELLED`
- `hitl_token.py` — HMAC-signed approval capability (R-09)
- `mock_gateway.py` — simulated operator APIs (SLR / SLTB)
- `payment_gateway.py` — simulated card processing
- `store.py` — SQLite persistence (transactions + purchases)
- `providers.py` — operator contact details

### 2.4 Conditions

`src/conditions/` — weather + live transit news.
- OpenWeather primary (key configured), Open-Meteo fallback (no key)
- Public RSS feeds filtered to transit-relevant items
- Background worker: 10-minute scrape, 5-hour TTL cache

### 2.5 Security & Responsible AI

- `src/security/` — `gateway.py` (ingress block + PII vault), `guardrails.py`
  (leetspeak/zero-width normalised denylist), `pii_masker.py`, `encryption.py`
  (AES-256-GCM), `audit_log.py` (JSONL evidence)
- `src/responsible_ai/grounding.py` — provenance citation attached server-side

---

## 3. The retrieval component

`src/planner/hybrid_retriever.py`. This is the graded IR piece.

### 3.1 Hybrid retrieval and RRF

Two ranked lists are produced independently and fused by **Reciprocal Rank
Fusion**:

```
score(d) = Σ 1 / (k + rank_i(d))          k = 60
```

RRF merges on *rank position*, not raw score, so a BM25 score never has to be
reconciled against a cosine distance. A document found by both retrievers wins.

### 3.2 Filtering happens BEFORE search

`retrieve_candidates` (`:223`) applies the mode filter, then the direction filter,
and only then searches:

- **Mode** — only the requested mode is ever scored
- **Direction** — `_serves_direction` prevents "Jaffna to Colombo" being answered
  with a Colombo→Jaffna service. If nothing runs that direction, it returns `[]`
  — an honest empty result, reported upstream.

### 3.3 Two backends, one interface

| Backend | Sparse | Dense | When |
|---|---|---|---|
| `fixtures` (default) | Token-overlap scoring in-process | Endpoint-emphasis weighting | Deterministic; what tests run on |
| `opensearch` | Real BM25 `match` | k-NN over `knn_vector` | Running the actual hybrid pipeline |

Both receive the **already-filtered pool** and re-apply it to results, so a rule
can never hide inside a backend where it cannot be tested.

**Dense embeddings:** `all-MiniLM-L6-v2`, 384 dims, normalized. Optional at
runtime — without `sentence-transformers` the planner serves BM25 and logs a
degradation rather than failing.

### 3.4 Enabling OpenSearch

```bash
echo "RETRIEVER_BACKEND=opensearch" >> .env
docker compose --profile ingest run --rm ingest      # indexes the corpus
docker compose up -d --build planner
```

**The ingest is required.** BM25 over an index missing recent services returns
nothing, and retrieval falls back to the in-memory corpus — correct answer, wrong
reason. `GET /health` reports `index_freshness` so drift is visible.

```json
{ "retrieval_backend": "opensearch", "index_freshness": null, "corpus_size": 4740 }
```

### 3.5 Why both retrievers

They genuinely differ. For `"tea country train through the hill station"`:
- sparse → `RM-187-KATUNA-FORT-*` (matches the literal words)
- dense → `RM-18-FORT-HATTON-*` (finds the hill-country train BM25 ranked nowhere)

---

## 4. The timetable corpus

`data/processed/` — the single source of truth for routes, times and fares.

| File | Rows | Content |
|---|---|---|
| `bus_routes.json` | 4,740 | Intercity buses (4,732 flagged `synthetic`) |
| `train_schedules.json` | 12 | Hand-curated Sri Lanka Railways |
| `ntc_bus_fares.json` | 794 | Published NTC fares |

### 4.1 Coverage

Across the 22 major cities (231 directional pairs): **155 direct, 76 reachable
with one change, 0 unreachable.**

### 4.2 The synthetic/honesty rule

Every generated row carries `synthetic: true` and `synthetic_fields` naming
exactly which fields were derived. There are three tiers:

| Tier | Prefix | Fare | Bookable |
|---|---|---|---|
| Published NTC corridor, times generated | `GEN-` | **published** (from the fare chart) | yes |
| No published fare at all | `MOD-` | **none written** | **no** |
| Hand-curated train timetable | `TRAIN-` | published | yes |

`MOD-` rows set `fare_unknown: true`, so the Booking Agent refuses them. The
timetable still shows a price via the estimator, marked `est.` — but
**a modelled fare never reaches the money path.**

### 4.3 Regenerating the corpus

```bash
python -m src.planner.generate_demo_data           # timetable expansion
python -m src.planner.generate_missing_corridors   # complete priced corridors
python -m src.planner.generate_unpublished_corridors  # model unpriced corridors
```

All three are **deterministic** — each service is seeded from its own route id,
so re-running produces no diff against the committed fixture. Modelled
departures follow real intercity patterns (peak bunching, midday gap, tapering
evening), vary between runs of one corridor, and flag overnight arrivals.

### 4.4 The fare estimator

`src/planner/fares.py` — `fare = a * road_km^b`, fitted to published fares in
the corpus. Display only; `fare_estimated: true` travels with every value so any
consumer can refuse to treat it as a quote.

---

## 5. Booking, payment and HITL

### 5.1 The human-in-the-loop gate (R-09)

Approval is **not a boolean**. The Booking Agent mints an HMAC-signed token
bound to session + route + fare + seat count + provider; the traveller returns it
verbatim. A self-asserted `hitl_approved: true` buys nothing — this was finding
**F-03 (Critical)** and is verified by red-team test C-15.

- Token TTL: 10 minutes
- Signing key: `HITL_TOKEN_SECRET`; the agent fails **closed** without it
- The client is never trusted with a fare

### 5.2 Booking flow

```
plan → gate (one token per service) → hold 10 min → payment → settle → ticket
```

Only `card_last4` ever leaves the client. No PAN, no CVV, ever.

### 5.3 Connections are two tickets

A journey with a change is **two services, two tickets**, and is purchased that
way end to end:

| Step | Behaviour |
|---|---|
| Offer | Two legs shown with the change station named |
| Confirm | **One signed token per leg**, each bound to its own route/fare/seats |
| Hold | Seat on **both** legs; if either fails, the other is released |
| Pay | **One payment** settles both transactions |
| Issue | **Two** e-tickets, **two** receipts, both in purchase history |

One token for the pair would let a traveller approve the cheap leg and spend that
approval on the expensive one. Half a hold is worse than none — a traveller
holding one leg is stranded at the change with a ticket for the service they
cannot board.

An interrupted checkout resumes as the **whole** journey: legs share a
`booking_group_id` and pending holds are offered grouped.

A `CONN-…` id is **not bookable on its own** — it names an itinerary, not a
service.

### 5.4 Seat availability

The number of seats left is shown **before approval** ("38 seats left", per leg
on a connection). If there is not enough room the booking is **refused** and the
traveller is offered departures that do have room:

```
⚠️ I can't hold that one — SLTB-2-COLO-MATA-0630 is fully booked.
Nothing has been booked. These other departures do have room:
   1. 08:00 → Matara (SLTB-2-COLO-MATA-0800) — 38 left
```

It never picks a departure on the traveller's behalf. Availability is keyed to
the service (a coach reports the same seats all day) and roughly one service in
eight is genuinely sold out, so the refusal path is reachable and testable.

---

## 6. Conversation design

Route planning is **slot filling**, not single-shot parsing. "I need to go to
Colombo" then "from Kandy at 8am by bus" combines correctly, and only outstanding
questions are asked.

Clarification-first: the planner reports what is still needed (mode, time,
endpoints) rather than guessing. The agent does **not** decide whether a
traveller's time or money matters more — it asks.

A bare one-word reply ("Ella") fills the slot it was asked for, because the
conversation remembers what is outstanding.

Colloquial regions ("the tea country") are **offered as choices**, never silently
resolved to one town.

Supported: English, Singlish. Out of scope: native Sinhala script.

---

## 7. Security posture

This is a graded deliverable (`AI_Vulnerability_Assessment_Report.md`).

### 7.1 Findings and their status

| ID | Finding | Severity | Status |
|---|---|---|---|
| F-01 | Injection guardrail never invoked on the LLM path | Critical | Remediated — gateway at `/chat` ingress **and** in the planner's own endpoints |
| F-02 | Denylist evaded by 10/10 adversarial variants | High | Remediated — normalised matching; 33/33 red-team tests secure |
| F-03 | HITL gate was a client-supplied flag | Critical | Remediated — signed server token |
| F-04 | Raw input re-rendered as Markdown | Medium | Remediated — escaped in both renderers |
| F-05 | PII redaction not wired into chat | High | Remediated — masking at ingress |
| F-06 | Dormant LLM extraction sink, permissive fallback | Medium | Remediated — opt-in, allow-list validated |
| F-07 | Only blocked input was logged | Low | Remediated — allowed queries logged too (metadata only) |

### 7.2 Controls in place

- **Auth** on every user-facing endpoint; sessions are signed HttpOnly cookies
- **scrypt** password hashing (n=2¹⁴)
- **AES-256-GCM** field-level PII encryption; a vault detokenises only at the
  final gateway step
- **Ingress gateway** blocks injection patterns and masks PII before any agent sees
  input — the planner re-enforces it rather than trusting its caller
- **Audit log** (`evaluation/security_audit_log.jsonl`) is a deliverable; pytest
  redirects it so test runs never contaminate the evidence
- **Red team:** 33 live tests against the running stack, all secure

---

## 8. Evaluation & testing

```bash
python -m pytest -q                                  # 714 passed, 16 xfailed
python -m evaluation.evaluate_ir                      # MRR / NDCG@5
python evaluation/redteam_prompt_injection.py --strict # 33/33 secure
cd frontend && npm test && npm run build              # render checks + build
ruff check src/ evaluation/
```

**Current status:** 714 passed, 16 xfailed, 3 xpassed. Red team 33/33.

### 8.1 IR benchmark — read the numbers carefully

`evaluation/evaluate_ir.py` scores 30 queries by MRR and NDCG@5, against a
**target of 0.85**. It reports which backend it measured, because the fixture
backend and OpenSearch score very differently:

| Backend | MRR | NDCG@5 | Failing queries |
|---|---|---|---|
| `fixtures` | 0.878 | 0.901 | 1 |
| `opensearch` (the real hybrid) | 0.794 | 0.830 | 2 |

**OpenSearch is below the 0.85 target.** This was measured after adding the mode
to the BM25 field; before that it scored 0.633 with 8 failures, because the
indexed text never said "train" — so a query for a train matched only on the
place names and same-corridor buses won on term frequency.

The two remaining OpenSearch failures are **benchmark artifacts, not retrieval
bugs**, and should be fixed by updating the benchmark rather than the retriever:

- **Q02** (`Heta ude Makumbura idala Galle yanna bus ekak`) expects one service,
  but the corpus now contains several express services on that exact corridor.
  The single ground truth is no longer uniquely correct.
- **Q26** (`Ada rate Gampaha indan Kandy`) expects `TRAIN-1001`, but **Gampaha is
  not in `MAJOR_CITIES`**, so the planner deliberately does not plan to or from
  it. The benchmark is asking for a journey the product does not support.

`evaluation/benchmark_queries.json` predates the corpus expansion and the current
major-city set. **Do not tune retrieval to raise this number** — fix the stale
ground truths, or the benchmark will start measuring the wrong thing.

### 8.2 The xfails are deliberate

`evaluation/test_fairness.py` keeps native-Sinhala parity as `xfail` rather than
deleting it, so the gap stays visible instead of disappearing from the report.

### 8.3 CI

`.github/workflows/ci.yml` — `backend`, `frontend`, `retrieval-opensearch`,
`redteam`. The retrieval job ingests without embeddings and runs the retrieval
tests against the live index.

---

## 9. Configuration

All optional; the system runs without any of them.

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | LLM intent classification (deterministic fallback if absent) |
| `OPENWEATHER_API_KEY` | Primary weather (Open-Meteo fallback if absent) |
| `RETRIEVER_BACKEND` | `fixtures` (default) or `opensearch` |
| `OPENSEARCH_URL` / `_USER` / `_PASSWORD` / `_INDEX` | Cluster connection |
| `HITL_TOKEN_SECRET` | Approval token signing — **booking fails closed without it** |
| `BOOKING_DB_PATH` / `CONVERSATIONS_DB_PATH` | SQLite locations |
| `SECURITY_AUDIT_LOG` | Audit log destination |
| `NLP_USE_LLM_EXTRACTION` | Opt in to LLM entity extraction |
| `EMBEDDING_MODEL` | Sentence encoder (default `all-MiniLM-L6-v2`) |
| `AGENT_TIMEOUT_SECONDS` | Sub-agent HTTP timeout |

---

## 10. Running it

```bash
cp .env.example .env
docker compose up -d --build
docker compose --profile ingest run --rm ingest   # only if RETRIEVER_BACKEND=opensearch
```

Then open http://localhost:3000 and sign in with the demo account.

The sidebar's **Try a query** panel contains worked examples, each verified
against the live corpus: fares, bus-only corridor, trains, a rail-only corridor,
a two-ticket connection, and a live-conditions advisory.

---

## 11. Known limits — read before making claims

These are real and should not be papered over:

1. **Most of the bus corpus is modelled.** 4,732 of 4,740 rows are `synthetic`.
   Only `GEN-` rows carry a published fare; `MOD-` rows are display-only and
   unbookable. Always state which tier a figure comes from.
2. **Only 12 real train services.** Rail coverage is genuinely thin.
3. **`Ella` and `Monaragala` are 0.15 km apart in `CITY_COORDS`** when they are
   ~50 km apart in reality. The fare estimator correctly refuses to price a
   journey between them. Fixing it needs a gazetteer that covers both.
4. **No native Sinhala.** English and Singlish only.
5. **Payments and operator calls are simulated** (`mock_gateway.py`). The
   workflow, state machine and receipts are real; the partner APIs are not.
6. **The booking agent refuses unpriced routes**, so a connection containing a
   `MOD-` leg is shown but not purchasable. That is deliberate.
7. **Connection ranking may lead with an unbookable option.** The card says why;
   it does not silently reorder.

---

## 12. Things that will bite you

Hard-won knowledge from building this. Each of these was a real bug.

1. **A stale OpenSearch index fails silently.** No error — retrieval falls back to
   the in-memory corpus and the answer is right for the wrong reason. Check
   `index_freshness` on `/health`.
2. **`_station_key` takes only the first token.** "Nuwara Eliya" once keyed to
   `"nuwara"`, matching nothing — its 222 services were all invisible and the
   coverage report quietly excluded the city. Similar spelling splits:
   Kunegala/Kurunegala, Ella/Nuwara Eliya vs `nuwaraeliya`.
3. **List a train's departure terminal in its `stops`.** Eight of twelve trains
   omitted their origin, so `stop_index` could never find it and train search from
   Colombo Fort returned nothing.
4. **Connection legs must survive in the session proposals.** Reading them back
   from what was shown (not re-deriving) is what makes approval meaningful.
5. **Stub `orch._graph` with `monkeypatch`.** Assigning it directly leaves the
   stub for every later test file, which then passes in isolation and fails in
   the suite.
6. **`merge()` skips `None`.** Use `[]` to clear a slot, not `None`.
7. **Re-rolled "random" data makes demos undebuggable.** Seat availability must
   be keyed to the service.
8. **The BM25 field must name the mode.** A train and a coach on one corridor
   otherwise have near-identical indexed text, and same-corridor buses outrank
   the train on term frequency.
9. **Changing the corpus invalidates `benchmark_queries.json`.** Ground truths
   that named a unique service become ambiguous once several exist, and queries
   can ask for corridors the planner never supported. Re-check them; do not
   retune retrieval to fit a stale expectation.

---

## 13. Repository map

```
src/orchestrator/   the only public API; LangGraph; auth; conversations
src/planner/        retrieval, NLP, timetables, fares, geo, corpus generation
src/booking/        state machine, HITL tokens, holds, payment, tickets
src/conditions/     weather, RSS news, incident classification
src/security/       gateway, guardrails, PII masking, encryption, audit
src/responsible_ai/ provenance citations
data/processed/     the timetable corpus (committed fixture)
evaluation/         37 test modules + IR benchmark + red-team harness
frontend/           React SPA (no UI framework) + nginx reverse proxy
docker-compose.yml  6 services + a one-shot ingest profile
```

---

*Everything above is verifiable against the code and the 714-test suite. Where
a number is stated it was measured, not estimated; where a limit exists it is
stated rather than hidden.*