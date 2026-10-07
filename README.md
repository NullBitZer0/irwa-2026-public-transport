# 🚆 LankaJourney AI: Multi-Agent Public Transit Planning & Booking System

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/your-org/lankajourney-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/lankajourney-ai/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Framework: LangGraph / FastMCP](https://img.shields.io/badge/Orchestration-LangGraph%20%7C%20FastMCP-orange.svg)](https://github.com/langchain-ai/langgraph)
[![IR: Hybrid BM25 + ChromaDB](https://img.shields.io/badge/IR-Hybrid%20RAG%20(RRF)-purple.svg)](https://www.trychroma.com/)

> **Information Retrieval and Web Analytics (IT 3041)**  
> **Course Lecturer:** Mr. Samadhi Chathuranga Rathnayake  
> **Institution:** Sri Lanka Institute of Information Technology (SLIIT)

---

## 📌 Executive Summary

**LankaJourney AI** is a multi-agent system built to navigate Sri Lanka's fragmented public transit landscape. The platform bridges the gap between Sri Lanka Railways (SLR), the Sri Lanka Transport Board (SLTB), and private highway express services.

Commuters can query in colloquial **Singlish** or **English** — the two input languages the system actually parses. The system coordinates three autonomous agents across strict communication protocols to formulate multimodal itineraries, evaluate live disruption notices, and execute ticket reservations under a Responsible AI framework.

---

## 🏗️ System Architecture


```

```
                   ┌──────────────────────────────────────────────┐
                   │      React Web Interface (nginx, :3000)     │
                   │        (Singlish / English Inputs)            │
                   └──────────────────────┬───────────────────────┘
                                          │
                               [Sanitized User Prompt]
                                          ▼
                   ┌──────────────────────────────────────────────┐
                   │          Security & Privacy Gateway          │
                   │  • Prompt Injection Guardrails (Regex/NeMo)  │
                   │  • PII Masking: NIC / Phone -> Token Vault   │
                   └──────────────────────┬───────────────────────┘
                                          │
                                          ▼
                   ┌──────────────────────────────────────────────┐
                   │       Agent 1: Orchestrator (Supervisor)     │
                   │  • Intent Routing & Conversation State Graph │
                   │  • Zero-Internet Privilege (Zero Trust)      │
                   └──────────────┬───────────────┬───────────────┘
                                  │               │
    [MCP / JSON-RPC Protocol]     │               │  [MCP / JSON-RPC Protocol]
 ┌────────────────────────────────┘               └────────────────────────────────┐
 ▼                                                                                 ▼

```

┌────────────────────────────────────────┐                       ┌────────────────────────────────────────┐
│   Agent 2: Journey Planning Agent      │                       │     Agent 3: Booking Engine Agent      │
├────────────────────────────────────────┤                       ├────────────────────────────────────────┤
│ • NLP: Pydantic Entity Extraction      │ <== MCP Bus Sync ===> │ • State Machine: INITIATED -> HELD     │
│ • Hybrid IR: BM25 + ChromaDB (RRF)     │   (Inter-Agent Flow)  │ • Mock Gateways: SLR & sltb.eseat.lk   │
│ • RSS Feed Alerts (Read-Only Web)      │                       │ • Human-in-the-Loop (HITL) Validation  │
└────────────────────────────────────────┘                       └────────────────────────────────────────┘

```

---

## 🤖 Agent Roles & Communication Protocols

The system uses a **Supervisor-Worker** design pattern adhering to the **Model Context Protocol (MCP)** and typed JSON-RPC data contracts:

1. **Orchestration Agent (Supervisor):**
   * Manages dialogue memory and routes tasks to specialized sub-agents.
   * Enforces Zero Trust: Operates in an isolated runtime without direct web browsing permissions.
   * Halts execution at Human-in-the-Loop (HITL) checkpoints before financial or booking commitments.
2. **Journey Planning Agent (Information Retrieval & NLP):**
   * Extracts entities (`origin`, `destination`, `mode`, `time`) from mixed-language prompts.
   * Runs hybrid search combining lexical (BM25) and dense semantic vectors (ChromaDB) merged via Reciprocal Rank Fusion (RRF).
   * Ingests public RSS feeds to incorporate real-time service delay alerts.
3. **Booking Engine Agent (Execution & Security):**
   * Manages transactional states (`INITIATED` $\rightarrow$ `SEAT_HELD` $\rightarrow$ `CONFIRMED` $\rightarrow$ `EXPIRED`).
   * Interfaces with simulated API adapters reflecting `sltb.eseat.lk` and Sri Lanka Railways reservation formats.
   * Resolves masked PII tokens to true commuter credentials only at the final mock gateway call.

---

## 🛡️ Responsible AI, Security, and Ethics

* **PII Data Protection:** Complies with the principles of Sri Lanka's Personal Data Protection Act No. 9 of 2022. National Identity Card (NIC) numbers and telephone details are tokenized into ephemeral UUIDs (`TOKEN_NIC_xxxx`) prior to LLM processing.
* **Grounding & Provenance:** Mitigates generative hallucination by binding route results to verified timetable documents. Every recommendation displays an explicit data provenance citation.
* **Linguistic Fairness:** Validated across dual-language query benchmarks, ensuring colloquial Singlish inputs achieve parity in accuracy with formal English searches.
* **Financial Guardrails (HITL):** Autonomous actions stop before booking execution. Seat holds require explicit, manual verification from the user via the interface.

---

## 💼 Commercialization Strategy

* **Target Market:** Daily inter-provincial commuters, university students traversing regional hubs, and international tourists.
* **Revenue Streams:**
  * **Freemium B2C:** Complimentary route lookups and timetable schedules.
  * **Commuter Pro:** LKR 450/month for automated seat-waitlist alerts and SMS trip updates.
  * **Operator Affiliate Fees:** LKR 50–100 per completed booking through transportation partnerships.
  * **B2G Demand Analytics:** LKR 100,000/month flat enterprise tier delivering anonymized passenger demand heatmaps to regional transit boards.
* **Unit Economics:** At 4,500 active monthly users, projected annual revenue is **LKR 5.115M** against infrastructure and inference overhead of **LKR 396K**, yielding an estimated **92% operating margin**.

---

## 📰 The incident scraper

The Conditions Agent keeps a cache of transit headlines and refreshes it on a
background timer, so "checked for incidents" means something rather than
depending on when someone last asked a question.

| Setting | Value | Why |
|---|---|---|
| Scrape interval | **10 minutes** | A report appears within ten minutes of publication without anyone asking. |
| Headline TTL | **5 hours** | An afternoon trip still sees a morning strike. |
| Storage | In-memory | It is a cache, not a record of record; a restart costs one interval. |
| Age measured from | When *we* saw it | Feed timestamps are missing or wrong often enough that trusting them would expire live reports. |

Expired entries are **evicted**, not hidden: a five-hour-old "services suspended"
headline must stop counting as a current disruption, or the classifier warns about
yesterday's strike forever.

Inspect it any time:

```bash
curl -s localhost:8103/mcp/headline_cache | python3 -m json.tool
```

`overdue: true` means nobody has scraped recently — so "no incidents reported"
currently means "nobody has looked". That distinction is reported rather than
glossed over.

Two filters keep the noise out, and both are needed:

- A headline must contain an **incident** word (*strike, protest, suspended,
  accident, landslide…*). A bare context word is not evidence of a disruption.
- It must also contain a **transport** word (*railway, bus, expressway, SLR…*).

Matching is on **word boundaries**, so "bus" inside *business* and "port" inside
*Spaceport* do not match — without that, ordinary business and space news was
being cached and reported as a warning on real routes.

## 🌦️ Weather sources

The Conditions Agent is the only component that decides anything about the
weather; the orchestrator relays its verdict and adds no judgement of its own.

| Source | Role | Key needed |
|---|---|---|
| **OpenWeather** | Primary. Current conditions plus the 3-hourly forecast for your departure time. | `OPENWEATHER_API_KEY` |
| **Open-Meteo** | Fallback, used automatically when no key is set, the key is rejected, or OpenWeather is unreachable. | None |

Set the key in `.env` (git-ignored, never committed):

```
OPENWEATHER_API_KEY=your_key_here
```

Both providers' condition codes are mapped onto this project's own severity
vocabulary rather than trusting their severity fields, so the planner acts the
same way whichever one answered. **The response names the source**, and says when
a fallback was used, so a degraded reading is visible rather than silent.

One Call 3.0 is deliberately unused: it is a paid plan and answers `401` on the
free tier, so this integration depends only on the free 2.5 endpoints.

## 📂 Repository Directory Structure


```

lanka-journey-ai/
├── .github/
│   └── workflows/
│       └── python-tests.yml        # CI linting & test validation
├── data/
│   ├── raw/                        # Original timetable circulars & gazettes
│   └── processed/
│       ├── train_schedules.json    # Structured Sri Lanka Railways timetables
│       ├── bus_routes.json         # SLTB & Southern Expressway routes
│       └── transit_knowledge.json  # NTC policies, hub layouts, baggage rules
├── src/
│   ├── orchestrator/               # Agent 1: Supervisor & Coordinator
│   │   ├── **init**.py
│   │   ├── router.py               # Intent classifier & supervisor node
│   │   ├── accounts.py                # Sign-in, sessions, profile (card: last4 only)
│   │   ├── conversations.py           # Chat history + read-only enforcement
│   ├── state.py                # LangGraph session state schema
│   │   └── schemas.py              # Inter-Agent MCP / JSON-RPC specifications
│   ├── planner/                    # Agent 2: NLP & Information Retrieval
│   │   ├── **init**.py
│   │   ├── nlp_parser.py           # Singlish/English entity extraction
│   │   ├── hybrid_retriever.py     # BM25 + ChromaDB + Reciprocal Rank Fusion
│   │   ├── live_disruptions.py     # Read-only RSS news feed ingestor
│   │   ├── geo.py                  # Boundary rings, city coordinates, corridors
│   │   └── server.py               # Planning Agent FastMCP microservice
│   ├── booking/                    # Agent 3: Action & Ticketing
│   │   ├── **init**.py
│   │   ├── state_machine.py        # Reservation lifecycle engine
│   │   ├── store.py                # SQLite persistence for bookings & ledger
│   │   ├── providers.py            # Operator contact details per provider
│   │   ├── mock_gateway.py         # Simulated SLR & SLTB eSeat endpoints
│   │   └── server.py               # Booking Agent FastMCP microservice
│   ├── security/                   # Security & Privacy Gateway
│   │   ├── **init__.py
│   │   ├── pii_masker.py           # Ephemeral tokenization for NIC & phone
│   │   ├── audit_log.py            # Append-only JSONL security audit trail
│   │   ├── gateway.py              # Ingress enforcement used by every agent
│   │   └── guardrails.py           # Prompt injection & adversarial filtering
│   ├── responsible_ai/
│   │   ├── **init__.py
│   │   └── grounding.py            # Citation verification & data provenance
├── src/conditions/                 # Agent 5: Live Conditions & Advisory
│   ├── gather.py                   # Open-Meteo weather + public RSS news
│   ├── analysis.py                 # Classify → advisory (no LLM in this path)
│   └── server.py                   # Conditions Agent microservice (:8103)
├── frontend/                       # React UI (the shipped interface)
│   └── src/
│       ├── App.jsx                 # Chat, HITL gate, payment portal
│       ├── api.js                  # Client for the orchestrator /api routes
│       ├── markdown.js             # Escaping Markdown renderer
│       └── components/             # Sidebar, ChatMessage, PaymentPortal
├── data/booking/                   # SQLite booking DB (runtime state)
├── evaluation/
│   ├── benchmark_queries.json      # 30 transit validation test cases
│   ├── evaluate_ir.py              # IR metrics calculator (MRR & NDCG@5)
│   ├── redteam_prompt_injection.py # 33-test injection & jailbreak harness
│   ├── redteam_evidence.json       # Latest harness evidence
│   ├── security_audit_log.jsonl    # Security audit trail
│   ├── test_security.py            # Automated PII & injection tests
│   ├── test_slot_filling.py        # Multi-turn slot accumulation
│   ├── test_guardrail_hardening.py # Obfuscation evasion + false positives
│   ├── test_booking_persistence.py # Restart survival & state integrity
│   └── test_ticket_contact.py      # Ticket contact details & resumable holds
├── docs/
│   ├── system_architecture.png     # Full architecture diagram
│   └── commercialization_model.md  # Detailed unit economics
├── requirements.txt                # Production dependencies
├── pyproject.toml                  # Linting & build configurations
└── README.md                       # Documentation

```

---

## 🚀 Getting Started

### Prerequisites
* **Python 3.11+**
* An API key for your chosen LLM provider (OpenAI API key or a local instance of [Ollama](https://ollama.ai/) running Llama 3)

### Installation Steps

1. **Clone the Repository:**
   ```bash
   git clone [https://github.com/your-org/lanka-journey-ai.git](https://github.com/your-org/lanka-journey-ai.git)
   cd lanka-journey-ai

```

2. **Create and Activate a Virtual Environment:**
```bash
python3 -m venv venv
# On Linux/macOS:
source venv/bin/activate
# On Windows:
venv\Scripts\activate

```


3. **Install Dependencies:**
```bash
pip install --upgrade pip
pip install -r requirements.txt

```


4. **Configure Environment Variables:**
Create a `.env` file in the root directory:
```env
OPENAI_API_KEY="your-actual-api-key"
PLANNER_AGENT_PORT=8001
BOOKING_AGENT_PORT=8002
ORCHESTRATOR_PORT=8000
DEBUG_MODE=True

```



---

## 💻 Usage & Execution Guide

To run the complete system, launch the worker services followed by the user interface:

### 1. Launch the Agent Services (Terminals 1 & 2)

In separate terminal sessions with the virtual environment active:

* **Start Planning Agent Worker (Port 8001):**
```bash
uvicorn src.planner.server:app --port 8001 --reload

```


* **Start Booking Agent Worker (Port 8002):**
```bash
uvicorn src.booking.server:app --port 8002 --reload

```



### 2. Launch the Web Interface

The shipped interface is the React frontend, served by nginx on port 3000.
The quickest path is Docker Compose, which brings up the whole stack:

```bash
docker compose up -d --build
```

Open your browser and navigate to `http://localhost:3000`.

<details>
<summary>Running the agents locally without Docker</summary>

```bash
# Backend agents
uvicorn src.planner.server:app    --port 8001
uvicorn src.booking.server:app    --port 8002
uvicorn src.orchestrator.server:app --port 8000

# Frontend dev server, proxying /api to the orchestrator
cd frontend && npm install && npm run dev
```

There is no Python UI layer: the legacy Streamlit prototype was removed and the
React frontend is the only interface.

</details>

### 3. Why the app needs `docker compose up` after a rebuild

The frontend proxies `/api/*` to the orchestrator. nginx resolves that hostname
**once, when it loads its config**, and keeps the address for the life of the
process — so recreating a container (which gives it a new IP) leaves the proxy
dialling an address that no longer exists and returning **502**, even though the
backend is healthy and its own healthcheck passes.

`frontend/nginx.conf` therefore uses Docker's embedded DNS as an explicit
`resolver` with a variable upstream, which re-resolves per request. The symptom
to recognise is a 502 with every container reporting healthy.

### 4. Retrieval: two interchangeable backends

`src/planner/hybrid_retriever.py` has one retrieval interface and two
implementations, selected with `RETRIEVER_BACKEND`:

| Backend | What it does | When to use it |
|---|---|---|
| `fixtures` (default) | Keyword scoring over the JSON timetables | Always available, deterministic, no infrastructure |
| `opensearch` | Real BM25 + k-NN vector search, fused with RRF | Demonstrating the IR component |

Everything *after* the search — the mode filter, the direction check that stops
"Colombo → Jaffna" being answered with a Jaffna → Colombo service, and the RRF
merge — is shared, so switching backend changes how candidates are *ranked*, never
what counts as a valid answer. Both are tested against the same invariants
(`evaluation/test_retrieval_backends.py`).

**Enabling OpenSearch:**

```bash
echo "RETRIEVER_BACKEND=opensearch" >> .env
RETRIEVER_BACKEND=opensearch python -m src.planner.opensearch_ingest   # 812 routes
docker compose up -d --build planner
```

The ingest creates the index with both a `text` (BM25) and a `knn_vector` (dense)
field. Indexing is reproducible: it recreates the index rather than appending, so
a re-ingest cannot leave stale documents behind.

Two deliberate design choices:

- **It falls back.** If the cluster or the index is unavailable, retrieval
  degrades to the fixtures and records why on `retriever.fallback_reason`. Route
  planning cannot go offline because a search container is down.
- **Dense retrieval is optional.** Losing semantic recall is a real degradation;
  losing retrieval because an optional dependency is missing is worse.

### ⚠️ What "hybrid" means in practice — read this before claiming it

| Where you run it | Indexed | Query-time retrieval |
|---|---|---|
| Host / laptop venv | text **+ 384-dim vectors** | **BM25 + k-NN (true hybrid)** |
| Docker (planner container) | text **+ vectors**, if ingested from the host | **BM25 only** |
| Docker, compose `ingest` service | text only | **BM25 only** |

The reason is one dependency: `sentence-transformers` is deliberately excluded
from the container image (it pulls in PyTorch, ~2GB), and query-time k-NN needs
an encoder to turn the question into a vector. So **in Docker the planner serves
BM25 results and logs that once** — the index still holds vectors, they just are
not queried.

Getting true hybrid query-time retrieval in Docker needs one of:

1. run the ingest *and* the planner from the host venv (what the table's first
   row means), or
2. add a small `embedder` sidecar that the planner calls for query vectors —
   about 200MB instead of 2GB, and the cleaner design, or
3. bake `sentence-transformers` into the planner image (not recommended: 2GB for
   one call).

Being precise about this matters: presenting BM25-only retrieval as "hybrid
dense retrieval" would be the kind of claim that falls apart under a question.

The compose `ingest` service exists so indexing does not need your host venv:

```bash
docker compose --profile ingest run --rm ingest          # BM25-only
docker compose --profile ingest run --rm ingest --no-embeddings   # same, explicit
```

CI runs exactly that BM25-only path, so the index, the mapping and the search are
verified on every push. The encoder path is verified locally, and the embedding
dimension is pinned by a unit test so a mismatch fails before it reaches a live
index.

**Coverage note:** the timetable corpus has 2,162 bus services and 12 trains. The Kandy → Jaffna
corridor was added from `data/curated/kandy_jaffna_train.csv` through the
supported ingestion path — real endpoints and intermediate stations, with the
generated times and fare flagged `synthetic` in the API and named in
`synthetic_fields`, which the UI no longer surfaces. It
is northbound only, so the reverse query correctly returns nothing.

### 5. Live weather and news

A fifth agent gathers current weather (Open-Meteo, no API key) and live transit
news (public RSS), and feeds the Planning Agent a verdict for your journey. The
planner then either confirms the normal route or recommends an alternative.

```
Weather and live news look fine for this journey — let's use the normal routes.
```

```
⚠️ I've seen reports of a strike for your route. Rail services are the ones
affected — I'd recommend an alternative service instead.
```

Reports about places **not** on your journey are ignored, and a report about the
other mode is mentioned briefly rather than shouted about. If weather or news
cannot be reached, the agent says conditions are unknown — it never claims
"everything is fine" it could not verify.

**Nothing fetched reaches a language model.** News text is reduced here, by
rules, to a category and a severity; the sentence you see is assembled from a
fixed vocabulary. A headline cannot become an instruction, and cannot be quoted
back to you.

**Demo control.** Waiting for a real accident to demonstrate this is impractical,
so the sidebar has a **🧪 Demo: incident alert** button. It pushes a *simulated*
incident into the agent and re-asks a route question. It runs through the same
classification and planner logic as a live headline — nothing is special-cased —
and is always labelled as simulated. `GET :8103/mcp/sources` lists what the
agent reads.

### 6. Sign-in and your profile

The app opens on a login page. Create an account, or use the demo traveller:

```
demo@lankajourney.lk  /  demotravel123
```

Your profile is in the sidebar, under the initials button. It shows your name,
contact number and card, and all three are editable.

**On the card number — read this before you change it.** A card number is not a
profile field. Storing a full PAN puts this project in PCI-DSS scope, makes the
database worth stealing, and puts the number one careless log line away from
being exfiltrated. The rest of this system refuses to send a PAN to any agent;
writing one to disk would undo that.

So the number is validated (Luhn), branded, reduced to its last four digits, and
**discarded**. It is never written to disk, never logged, never returned by an
endpoint, and never sent to an agent or a model. The profile shows
`Visa •••• 1111` — which is what you need to recognise your own card, and what a
receipt needs to record. A real payment would take the card straight from the
browser to the payment provider without passing through this server.

Passwords are hashed with scrypt (memory-hard, standard library) and compared in
constant time. Sessions are random tokens stored only as SHA-256 digests, in an
`HttpOnly` cookie — so a stolen database yields neither passwords nor usable
sessions.

**Authentication is the edge of the trust boundary, not a feature of the login
form.** Every user-facing endpoint requires a session: `/chat`,
`/conversations`, `/payment`, `/purchases`, `/pending_holds`, `/demo_incident`,
`/schedules`, `/map-routes`, `/profile`. Without it, anyone who can reach the
port can read your conversations and book tickets as you. The red-team harness
registers a throwaway traveller before probing, and its preflight fails if an
unauthenticated `/chat` is anything other than `401` — a security report that
cannot tell whether the target was protected is not evidence.

### 7. Timetables and the route map

Two views in the sidebar, both served from the same corpus as the chat.

**Timetables** lists bus and train services sorted by departure time, filterable
by mode (**All / Buses / Trains** — each returns only its own mode) and
searchable by town, route or operator.

**Times are modelled, not stamped from a grid.** Departures follow a
peak/off-peak pattern scaled by journey length — a 40 km hop does not run every
30 minutes, and a full-day intercity run does — with a stable per-route offset so
two routes on the same corridor do not depart in lockstep. Journey duration comes
from the distance between the endpoints. The result: **453 distinct departure
times** across 2,162 bus services, with the most common one accounting for 1% of
the network. It used to be 42 distinct times, with 28% of all services leaving at
06:30. Services running past midnight are flagged `+1 day`.

**Fares: published where they exist, estimated where they do not.** 62% of the
bus corpus had no price at all, which made the timetable look broken. Unpriced
services are now given a distance-based estimate, marked **est.** in the fare
cell and counted in a note above the table:

| Fare class | Model | Accuracy vs published |
|---|---|---|
| Ordinary | `6.9 × km^0.955` | median error 31%, 80% within 41% |
| Semi-luxury | `69.9 × km^0.593` | median error 4%, 80% within 9% |

Both are least-squares fits to the services in this corpus that carry a published
fare, so an estimate sits in the same range as a real price. A linear fit was
tried first and had a better median but a far worse tail — it priced a 41 km hop
at LKR 410 where the published fare is LKR 170, because the long-distance
intercept dominates at short distances.

Two rules the estimates obey:

- **A published fare is never replaced.**
- **An estimate is never charged.** Booking still refuses an unpriced service,
  because quoting a modelled price and taking a payment for it are different
  things. Same-city hops (Fort → Pettah) get no estimate at all: there is no
  distance to model, so any number would be invented.

**Route map** draws the available routes on a real outline of Sri Lanka as inline
SVG, so it works offline and leaks no viewport to a tile server. Click a line for
its services, operators and fare range; line thickness is service count.

The coastline is **Natural Earth 1:10m admin-0 boundary data**, baked into
`data/geo/sri_lanka_boundary.json` (public domain): 757 points for the main
island plus four significant offshore islands. Mannar is a town on Mannar Island,
so an outline that kept only the largest ring put it in the sea. Drawing bounds
are served with the geometry rather than hardcoded in the frontend, so the
projection cannot drift out of step with the shape it projects. If the data file
is ever missing, the map falls back to a coarse built-in trace and says so on
screen.

City markers use settlement centres from `src/planner/geo.py`, cross-checked
against the Natural Earth gazetteer where it has an entry — which is how Moratuwa
was found sitting 8.5 km off. The coastline is generalised outward by a couple of
kilometres, so a coastal town can plot slightly into the sea (Galle, genuinely on
the shore, sits 2.4 km off this line), and the tests assert that distance rather
than pretending it is zero.

The map is honest about what it is:

- Corridors are **city-to-city**. Bus stops inside Greater Colombo (Pettah,
  Kotahena, Dehiwala) are clustered into Colombo, because drawing them as
  separate pins a few kilometres apart would imply precision the data does not
  have.
- Services that run entirely within one city have no line to draw. They are
  counted in the coverage note and listed in the timetables tab — 1,494 of 2,174
  services are drawn as corridors, and the map says so rather than quietly
  omitting the rest.
- Every place in the corpus is mapped explicitly (`src/planner/geo.py`). Anything
  unmapped is reported, never silently dropped, because a route that vanishes is
  reported by users as "the map is missing my bus".

### 8. No direct service? Faster or cheaper

When nothing runs directly from A to B, the agent says so and **asks whether you
want it soon or cheap** rather than deciding for you:

```
Agent: There's no direct service from **Anuradhapura** to **Matara**, but you
       can change once along the way. Would you rather get there **as soon as
       possible**, or **spend as little as possible**?
       [⚡ Fastest]  [💰 Cheapest]

You: cheaper
Agent: Here are the cheapest ways to get there, changing once:
       1. 🕐 18:15 → 10:00 +1 day · Anuradhapura → Colombo Bastian Mawatha →
          Colombo Fort → Matara (bus + train) · 15h 45m · LKR 1,676
```

Choosing **fastest** gives the soonest arrival (two buses, 8h 50m, LKR 2,216);
choosing **cheapest** gives the lowest fare (bus + train, LKR 1,676). Which mode
ends up in each leg is decided by the data, not hardcoded — a train is quicker on
the long leg and a bus is quicker on the short one, and on some corridors that
reverses.

Details worth knowing:

- **The train/bus buttons are gone.** The traveller described a journey, not a
  preference for an operator; the mode is the planner's problem. (The question
  still appears in prose, since the planner cannot know what the traveller will
  accept.)
- **An unpublished fare is never shown as free.** Many bus services have no
  published fare. A change with an unpriced leg is reported as
  *"fare not published"* and sorted *after* priced options — otherwise zero would
  win every "cheapest" search by not having a price.
- **Arrival times say "+1 day"** when the journey crosses midnight, so a clock
  time is not mistaken for the same morning.
- **Nothing is offered before the question is answered**, so the agent's opinion
  of what matters more to you is not applied silently.
- **A same-day change is preferred over an overnight one, before price is even
  considered.** Ranking purely on fare made "cheapest" a 29h40 train while a 7h30
  change sat below it in the same list. An overnight option is still offered when
  nothing else is, it is just never the headline.
- A change means **two separate tickets**, so these are shown as plans rather
  than something you can hold in one go.

### 9. Conversations, booking and ticket history

**One conversation per trip.** A conversation opens with a new chat, collects
what it needs, and ends when the payment completes. After that it becomes
read-only history — you can open it and read it, but not continue it. That rule is
enforced by the server (a finished conversation returns `409`), not just by a
disabled composer.

The flow it produces:

```
Agent:     Welcome to LankaJourney AI 🚆 Tell me where you want to go and I'll
           plan the journey — in English or Singlish. (Sent when the chat opens)
Traveller: I want to go to Colombo
Agent:     Got it — Colombo Fort. Still need: where are you starting from,
           train or bus, and what time?
Traveller: from Kandy
Agent:     …Still need: train or bus, and what time?
Traveller: how will the weather be?
Agent:     ⚠️ … Currently around 23°C at your departure point. I scanned recent
           transit news and found nothing else affecting this route. (Kandy → Colombo)
Traveller: 8am by train
Agent:     Here are the train options… Are you ready to book? Reply **yes** and
           I'll hold the first one, or name a different service.
Traveller: yes
Agent:     ⚠️ Human-in-the-Loop Confirmation Required — TRAIN-1007 at LKR 850.
```

Notes on that flow:

- **Weather and incident questions are answerable mid-conversation.** They read
  the journey from the session's slots and do not reset them, because "how will
  the weather be?" is a question *about* the trip, not a new trip.
- **Weather is only ever reported for a complete route.** With half a route the
  agent asks for the missing end and keeps what it already knows — it will not
  answer "how is the weather?" from a destination alone, because weather for one
  place is not weather for a journey and "looks fine on your route" implies a
  route that does not exist.
- **Incidents are always checked.** No toggle, no permission: a real reported
  strike must never depend on someone pressing a button, because the failure mode
  of gating it is confidently reporting a route as clear because nobody asked.
  The sidebar toggle controls the *simulated* incident only.
- **A requested time that nothing departs at gets the nearest next departure**,
  and says so — *"next one — nothing that close to your time"* — rather than
  silently answering with a different hour.
- **Every reply asks for whatever is still missing**, one question per line, in
  the order a trip is planned — from, to, when, how — after echoing what has
  already been established. Partial answers are answered with the single
  remaining question, not a repeat of the full list.
- **Seat count is settled before any hold.** Ask for it in the reply with one-tap
  choices, or just say it: *"for 2 seats"*, *"3 tickets"*, *"a family of 4"*.
  It is remembered across turns and can be corrected. The agent does not ask again
  once you have answered, and never quotes one seat and bills for four.
- **Incident checking is off until you switch it on.** The *Simulate incident*
  toggle in the sidebar arms it; nothing is looked for, and no disruption is
  mentioned, until then. Un-checking removes the simulated incident and stops the
  check again. While it is off the agent says so rather than "all clear":
  *"I haven't checked for incidents on your route — turn on the incident check
  in the sidebar if you'd like me to look."* Weather is reported either way,
  since it needs no consent. Pressing the toggle does not send a message for you;
  it arms the check, and the next time the agent offers routes it consults the
  scraped headlines.
- **"Are you ready to book?" is a real gate.** A bare affirmative picks the
  service that was proposed and opens the signed HITL confirmation. Only a short
  affirmative counts, and a refusal is never read as consent — booking a seat
  someone declined is the worst failure this step could have.
- **Payment ends the conversation.** The transaction is bound to its conversation
  server-side when the seat is held, so `/payment` can archive the right one
  without the client claiming which it was. Slot memory is dropped at the same
  moment, so the next chat starts clean.

The greeting is a stored message rather than a rendered one, so a conversation
reopened from history starts the way it actually started.

History lives in SQLite (`CONVERSATIONS_DB_PATH`) and survives a restart.
`data/conversations/` is git-ignored, so CI creates it before starting the stack —
Docker would otherwise make it root-owned and the orchestrator could not open its
database, which fails the live red-team job while every other job passes.
`evaluation/test_compose_ci_agreement.py` asserts compose and CI agree. The
sidebar lists finished conversations with their booking reference and message
count; clicking one opens the transcript in read-only view.

### 10. Bookings, payment and ticket history

Booking is staged and human-in-the-loop: the agent holds a seat for 10 minutes,
asks for approval, then stops at the payment step. **Only the last four card
digits are ever sent** — no PAN or CVV reaches any agent.

Seats are priced per seat and the total is charged. The confirmation states both
— *"LKR 850 per seat (LKR 2,550 for 3 seats)"* — because a traveller being asked
to approve a charge should see the amount, not the unit price. The total is
carried on the transaction (`amount_lkr`), so the reply, the receipt and the
purchase ledger cannot disagree about it.

This was a real bug: the amount charged was the per-seat fare whatever the seat
count, so a three-seat booking cost the price of one. Nothing caught it while
every booking happened to be a single seat in the UI, and a test had encoded the
wrong total as expected behaviour.

In the sidebar's purchase history:

- A **settled ticket** is clickable and expands to show the operator's contact
  details — name, customer-care line and website — for the company that issued it.
- A booking that is **still awaiting payment** is clickable and reopens the
  payment portal. Since a hold expires after 10 minutes, this is what keeps an
  interrupted checkout recoverable instead of silently losing the seat.

Bookings and the purchase ledger are persisted in SQLite (`data/booking.db`),
so they survive a container restart. Without `BOOKING_DB_PATH` the store is
in-memory, which is what the test suite uses.

### 11. Example Test Queries

* **Route Discovery (Singlish):**
> *"Heta ude 6ta Kandy indan Galle yanna train ekak thiyeda?"*


* **Multimodal Expressway Query (English):**
> *"Find me a highway bus from Makumbura MMC to Galle tomorrow evening around 5:00 PM."*


### Multi-turn slot filling

A route request is a conversation, not a single query. The assistant collects the
details it needs — and only what is still missing — then answers:

```
Traveller: I need to go to Colombo
Assistant: Got it — you want to travel to Colombo Fort. Still need:
           where are you starting from? and train or bus? and what time?

Traveller: from Kandy
Assistant: Got it — you want to travel from Kandy to Colombo Fort.
           Still need: train or bus? and what time do you want to travel?

Traveller: at 8am by train
Assistant: Here are the train options from Kandy to Colombo Fort around 08:00…
```

Each turn is a delta: slots gathered earlier are carried in the session, so
answering a question never resets the ones already answered, and a short reply
naming only a time or a mode is understood as an answer to the question that was
asked.

* **Security & Redaction Demonstration:**
> *"Please reserve 2 seats for Route TRAIN-1005. My NIC is 200012345678 and mobile is 0771234567."*
> *(Observe the terminal logs to verify that the NIC is tokenized into `TOKEN_NIC_xxxx` prior to LLM processing).*



---

## 🗂️ Schedule Data & Coverage

Route retrieval reads `data/processed/train_schedules.json` and
`data/processed/bus_routes.json`. Those fixtures start from **published
timetables**, which keeps every row traceable, plus generated rows that are
always flagged `synthetic: true` with the fields that were derived named.

All 21 major cities are now mutually reachable: **134 corridors run direct** and
a further **76 are reachable with a single change**. The fare is published for
`GEN-` rows and estimated for `MOD-` rows — see below for why the difference
matters.

When no direct service runs between two stations, the planner falls back to a
**one-stop connection** and prefers one that mixes train with bus (for example
train to Colombo Fort, then a coach onwards). Connections are built from service
endpoints only, because the fixtures carry no per-stop times, so an itinerary is
never proposed on invented timings.

A connection is **two tickets**, and is booked that way end to end:

| Step | What happens |
| --- | --- |
| Offer | Itinerary shown as two legs with the change station named |
| Confirm | **One signed HITL token per leg**, each bound to its own route, fare and seat count |
| Hold | A seat on **both** legs; if either fails, the other is released |
| Pay | One payment settles **both** transactions |
| Issue | **Two** e-tickets, **two** receipts, both in purchase history |

One token for the pair would let a traveller approve the cheap leg and spend that
approval on the expensive one, so the tokens are per leg. Half a hold is worse
than none: a traveller holding one leg of a two-leg journey is stranded at the
change with a ticket for the service they cannot board, so a partial hold is
rolled back. An interrupted checkout resumes as the whole journey — legs of one
booking share a `booking_group_id` and are offered together.

A connection id (`CONN-…`) is **not** bookable on its own. It names an
itinerary, not a service, so the Booking Agent refuses it rather than issuing a
ticket for something that does not run.

### Completing corridors from the published fare chart

```bash
python -m src.planner.generate_missing_corridors --dry-run
python -m src.planner.generate_missing_corridors
```

`data/processed/ntc_bus_fares.json` prices 794 city pairs. Some of those pairs
had a published NTC fare but no service rows in the corpus, which meant a real,
operating corridor looked like it did not exist. This adds service rows for them.

A pair **in the fare chart** is a corridor NTC operates and has priced, so filling
in its departures completes a record. The **fare is published**, not derived —
only the departure times and the derived journey duration are flagged in
`synthetic_fields`. These rows are bookable.

### Corridors with no published source at all

```bash
python -m src.planner.generate_unpublished_corridors --dry-run
python -m src.planner.generate_unpublished_corridors
```

The remaining major-city pairs have no fare in the chart and no timetable
anywhere in the sources. Nothing can be *completed* for them, so this generator
**models** them instead, and the distinction is kept strict:

| | priced corridors | modelled corridors |
| --- | --- | --- |
| Prefix | `GEN-` | `MOD-` |
| Fare | published by NTC | **none written** |
| Bookable | yes | **no** |

A modelled row carries no `base_fare_lkr` and sets `fare_unknown`, so the Booking
Agent refuses it. The price the timetable shows comes from the estimator in
`fares.py` — the same fitted model used everywhere else — marked `est.` and
carrying `fare_estimated: true`. Charging a traveller an amount we modelled is
not the same as charging what the operator charges, so modelled fares never reach
the money path. `evaluation/test_modelled_corridors.py` asserts this.

Timetables are modelled rather than invented flat. Intercity coaches bunch in the
morning and evening peaks and thin out mid-afternoon, longer corridors run more
services, journey times vary between runs of one corridor, and a long evening
departure is flagged as arriving next day. Each corridor is seeded from its own
route id, so re-running either generator produces no diff — the corpus is a
committed fixture.

All 210 major-city pairs now have a service: 134 direct, 76 reachable with one
change, none unreachable.

### Widening coverage

```bash
# See which official sources are readable from your network
python -m src.planner.schedule_ingest --check

# Start a CSV to fill in from an operator's published timetable
python -m src.planner.schedule_ingest --template data/processed/inbox/buses.csv

# Preview an import, then apply it
python -m src.planner.schedule_ingest --source csv --csv data/processed/inbox/buses.csv --dry-run
python -m src.planner.schedule_ingest --source csv --csv data/processed/inbox/buses.csv
```

The importer holds two rules:

* **It never invents a field.** A row missing an origin, a destination or a valid
  `HH:MM` departure is skipped and reported, never filled in with a guess.
* **It never silently overwrites curated data.** Conflicting `route_id`s are
  reported; only `--force` replaces them.

Every row should record its `source` and `source_url` so a reviewer can check it.
An unreadable source raises an error instead of returning an empty result, so
"we could not read the timetable" is never mistaken for "no service exists".

---

## 📊 Evaluation & Verification Scripts

To replicate the experimental results documented in our technical report:

1. **Evaluate Information Retrieval Accuracy (MRR & NDCG@5):**
```bash
python evaluation/evaluate_ir.py

```


*Expected Benchmark:* **MRR > 0.85** | **NDCG@5 > 0.90** on the 30-query test split.
2. **Run the Security and Redaction Test Suite:**
```bash
pytest evaluation/test_security.py -v
```


3. **Run the full test suite** (NLP parity, retrieval, connections, ingestion):
```bash
pytest evaluation/ -q
```

*Expected:* **328 passed**.

4. **Run the AI vulnerability / prompt-injection harness:**
```bash
RT_ORCHESTRATOR_URL=http://localhost:8100 \
RT_BOOKING_URL=http://localhost:8102 \
python evaluation/redteam_prompt_injection.py --strict
```

Add `--strict` in CI: it also fails on an inconclusive result, and on an
unreachable service, so a dead stack cannot quietly "pass" on fewer tests.

*Expected:* **33 tests | 0 vulnerable | 0 inconclusive**.



---

## 👥 Contributors & Workload Distribution

| Contributor | Role | Core Technical Contributions | Primary Viva Defense Areas |
| --- | --- | --- | --- |
| **Member 1 (Placeholder)** | **System Architect & Orchestrator Lead** | Orchestration Agent (`src/orchestrator/`), FastMCP protocol schemas, conversation state graph, CI/CD pipeline. | Multi-agent coordination, Zero Trust network boundary, LangGraph state design. |
| **Member 2 (Placeholder)** | **NLP & Information Retrieval Lead** | Planning Agent (`src/planner/`), BM25 + ChromaDB hybrid RAG, RapidFuzz normalization, IR benchmarking. | Hybrid retrieval math (RRF), vocabulary mismatch mitigation, MRR/NDCG metrics. |
| **Member 3 (Placeholder)** | **Execution Engine & Security Lead** | Booking Agent (`src/booking/`), PII tokenization vault, state machine transitions, mock API adapters. | Data protection under SL PDP Act, prompt injection defense, transaction state integrity. |
| **Member 4 (Placeholder)** | **Responsible AI, Commercialization & Media Lead** | React frontend UI, route grounding citations, ticket payment/history panel, unit economics model, Gen AI video production. | Linguistic fairness across Singlish/English, financial viability, explainability framework. |

---

## 📄 License & Acknowledgments

This project is developed for educational and research evaluation under module **IT 3041: Information Retrieval and Web Analytics** at the **Sri Lanka Institute of Information Technology (SLIIT)**.

Public timetable structures are derived from schedules published by **Sri Lanka Railways (SLR)** and the **National Transport Commission (NTC)**.

```
