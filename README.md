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
│   │   ├── state.py                # LangGraph session state schema
│   │   └── schemas.py              # Inter-Agent MCP / JSON-RPC specifications
│   ├── planner/                    # Agent 2: NLP & Information Retrieval
│   │   ├── **init**.py
│   │   ├── nlp_parser.py           # Singlish/English entity extraction
│   │   ├── hybrid_retriever.py     # BM25 + ChromaDB + Reciprocal Rank Fusion
│   │   ├── live_disruptions.py     # Read-only RSS news feed ingestor
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

### 3. Bookings, payment and ticket history

Booking is staged and human-in-the-loop: the agent holds a seat for 10 minutes,
asks for approval, then stops at the payment step. **Only the last four card
digits are ever sent** — no PAN or CVV reaches any agent.

In the sidebar's purchase history:

- A **settled ticket** is clickable and expands to show the operator's contact
  details — name, customer-care line and website — for the company that issued it.
- A booking that is **still awaiting payment** is clickable and reopens the
  payment portal. Since a hold expires after 10 minutes, this is what keeps an
  interrupted checkout recoverable instead of silently losing the seat.

Bookings and the purchase ledger are persisted in SQLite (`data/booking.db`),
so they survive a container restart. Without `BOOKING_DB_PATH` the store is
in-memory, which is what the test suite uses.

### 4. Example Test Queries

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
`data/processed/bus_routes.json`. Those fixtures are **hand-curated from published
timetables**, which keeps every row trustworthy but caps coverage — currently the
main intercity corridors rather than every SLTB route.

When no direct service runs between two stations, the planner falls back to a
**one-stop connection** and prefers one that mixes train with bus (for example
train to Colombo Fort, then a coach onwards). Connections are built from service
endpoints only, because the fixtures carry no per-stop times, so an itinerary is
never proposed on invented timings.

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
