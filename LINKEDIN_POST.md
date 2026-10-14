# LinkedIn Post — LankaJourney AI

> **How to use this file.** The text between the rules is the post — paste it
> directly. Everything below the final rule is your upload checklist and is *not*
> part of the post. Items in `[BRACKETS]` need your input.
>
> The brief (line 81) forbids claiming anything that was not implemented or tested.
> Every figure below is from a run we can reproduce. Where something is not built
> — MCP, TLS, encryption at rest — it is stated as a limitation rather than dressed
> up. That is what makes the rest believable.

---

## 🚀 BEYOND TRADITIONAL AI: Building Intelligent Systems That Can Reason, Collaborate, and Act

### 🤖 LankaJourney AI

**"An agentic transit assistant that plans, compares and books real journeys across Sri Lanka — and shows its sources."**

Public transport in Sri Lanka is a genuinely hard planning problem: two operators, fragmented timetables, a language barrier for daily commuters, and a real risk of travellers being overcharged by touts. Someone planning Colombo to Kandy has to reconcile railway schedules, intercity bus routes, and connections that may not actually exist.

LankaJourney AI is a four-agent system that does that conversationally — and books the ticket with a human approval gate.

This project was completed as a requirement of the group assignment *"Design and Implementation of an Agentic AI System Integrating LLMs, NLP, Security, and Information Retrieval"* under **IT3041 – Information Retrieval and Web Analytics**, BSc (Hons) Data Science, Department of Computer Science, Faculty of Computing, **Sri Lanka Institute of Information Technology (SLIIT)**.

---

### 🎯 The Real-World Problem

**Why does this matter?**

Commuters plan across operators that publish data in incompatible formats, often in English and Sinhala, sometimes not at all. Tourists planning the coastal or hill-country routes rely on touts or forums. The practical result: missed connections, unnecessary travel, and fare uncertainty.

The hard part isn't answering "when is the next train." It's knowing when **no direct connection exists** — and finding a two-leg journey that genuinely works, with a fare that is real.

---

### 💡 Our Solution

Unlike a chatbot that guesses, ours retrieves from a structured corpus, refuses to invent routes, and won't sell a ticket it cannot price.

---

### 🧠 Inside Our Architecture

**🔹 Large Language Models** — Groq-hosted `llama-3.1-8b-instant` at temperature 0, used narrowly for intent classification and entity extraction. It never decides a fare, a route, or a seat.

**🔹 NLP** — A rule-based parser with Singlish/English handling: leetspeak, colloquial place names, bilingual station keys, and multi-turn slot filling ("tomorrow morning", "from Matale").

**🔹 Information Retrieval** — Hybrid retrieval in OpenSearch: BM25 lexical search fused with dense kNN over 384-dimensional `all-MiniLM-L6-v2` embeddings, combined with Reciprocal Rank Fusion (k=60) over 4,752 indexed documents.

**🔹 Intelligent Agents** — Four specialised services.

**🔹 Agent Communication** — Services communicate over HTTP/JSON REST, orchestrated by a LangGraph state graph. *(To be precise: these routes are prefixed `/mcp/`, but they are plain REST endpoints — we did not implement the MCP protocol.)*

**🔹 Security & Responsible AI** — PII tokenisation, an ingress gateway that canonicalises input before pattern matching, HMAC-signed human-approval tokens, and an independent red-team suite.

---

### 🏗️ How Our Agents Work Together

**🤖 Agent 01 – Orchestrator** *(LangGraph supervisor)*
Classifies intent, drives the state graph, coordinates the other agents, and renders the response. Holds the session and never delegates authorisation to the client.

**🤖 Agent 02 – Journey Planner** *(NLP + IR)*
Parses the query, runs hybrid retrieval, constructs ranked direct and one-change connections, and acts as the **pricing authority** — the fare comes from a server lookup, never from the user's input.

**🤖 Agent 03 – Booking Engine** *(Execution & Security)*
Manages seat inventory and a staged transaction: **hold → pay → issue**. Approval is a server-signed token bound to session, route, fare, seat count and provider, with a 10-minute expiry. It will not sell an unpublished fare.

**🤖 Agent 04 – Live Conditions** *(Advisory)*
Checks weather and disruption conditions, caches and filters transit-relevant headlines, and adapts recommendations.

---

### 🔄 Our Agentic Workflow

A user types a natural-language request. The Orchestrator classifies it, forwards to the Planner, which either asks a clarifying question or runs retrieval over the corpus and returns ranked journeys with provenance. The user approves, the Planner fetches the authoritative fare, the Booking Agent holds a seat, payment settles the hold, and a ticket is issued.

The Planner acts as pricing authority and the Booking Agent verifies a signed approval — so neither trusts a number supplied by the caller.

📌 *See the architecture diagram and flow sequence attached below.*

---

### ✨ What Makes Our System Interesting

✅ **Two-leg connections booked as one transaction.** One approval, one payment, two tickets — with atomic rollback if either leg fails. Grouped holds survive a page refresh.

✅ **Seat inventory that can't oversell.** Deterministic seat maps per service, checked at approval time *and* at hold time. If seats are gone, the system says so and suggests alternatives rather than failing at checkout.

✅ **Provenance on every result.** Every journey states whether its data is real or synthetic. 1,304 of our 4,740 bus rows carry a published NTC fare; the rest are labelled as estimates and are deliberately not sellable. A fabricated fare is a free ticket.

✅ **Human approval that cannot be forged.** Our red-team found that the original approval gate was a plain client-supplied boolean — any caller could book while claiming pre-authorisation. We replaced it with an HMAC-SHA256 token. While testing the fix, we found a second flaw in our own code: the token was signed but only partly verified, so a one-seat approval could be replayed to hold six (LKR 850 approved against LKR 5,100 billed). We reproduced it end-to-end before fixing it, and both claims are now bound.

✅ **Singlish understood.** "Colombo Fort yanna one, ekata" — we handle the code-mixed English/Sinhala that daily commuters actually type.

---

### 📊 Testing Our System: What Did We Discover?

**🔹 System performance** — 737 automated tests passing (3 skipped, 32 expected-failure cases kept visible rather than deleted).

**🔹 Information retrieval quality** — MRR 0.794 and NDCG@5 0.830 against a target of 0.85/0.90. **We are below target, and the reason is interesting:** investigation showed two benchmark queries with ambiguous or stale ground-truth labels rather than a retrieval regression. Fixing the labels would have made the number look better without making the system better — so we documented it instead.

**🔹 Agent collaboration** — Connection coverage across 22 major cities: 155 direct and 76 one-change routes, 0 unreachable pairs.

**🔹 Security** — An independent 33-case red-team suite covering prompt injection, jailbreaks, prompt leakage, instruction override, prompt manipulation and robustness. Our first run evaded the guardrail 10 times out of 10 through leetspeak, zero-width characters and hyphenation. The fix was to canonicalise input *before* pattern matching rather than to keep adding patterns. Final run: 33/33 resisted.

**🔹 Challenges and limitations** — Synthetic timetable data (route codes and endpoints are real; times are generated), no TLS in the current deployment, no encryption at rest for persisted fields, and an indirect-injection vector through external feeds that is currently latent rather than exploitable. We think honest limitations make the rest of the claims more credible, not less.

---

### 🔐 Responsible AI and Security

- **Privacy** — NICs, phone numbers and passport numbers are tokenised before reaching any model, and the in-memory vault is AES-256-GCM encrypted. We never send PII to an LLM. Full card numbers are never stored — only the brand and last four digits. Aligned to Sri Lanka's **Personal Data Protection Act No. 9 of 2022**; the claim is engineering alignment, not legal advice.
- **Input & output security** — Ingress canonicalisation, escape-first rendering with a fixed allow-list of output nodes, and deterministic handling of model output.
- **Explainability** — Every retrieval result carries a citation naming its dataset, and synthetic values are labelled.
- **Fairness** — English and Singlish queries return identical results; Sinhala and Tamil script receive the same treatment. A trilingual parity suite enforces this.
- **Accountability** — Approved *and* blocked requests are logged, with message text deliberately excluded.

---

### 💼 From Academic Innovation to Business Opportunity

**🎯 Target users** — Daily inter-provincial commuters, university students, and independent travellers who currently depend on touts or forums.

**💰 Proposed model** — Freemium route lookup, a commuter tier at LKR 450/month for waitlist and disruption alerts, operator affiliate fees, and anonymised demand analytics sold to transport authorities.

**📈 Business value** — The differentiation isn't the chatbot. It's provenance and refusal: a system that says "this connection doesn't exist" and "this fare isn't published" is more useful than one that always answers.

**🌍 Future work** — Deploy the conditions feed behind delimiter sandboxing to close the latent indirect-injection path, add TLS and at-rest encryption, native Sinhala, and live operator integrations.

---

### 🎓 Our Learning Experience

The most useful thing we learned is where the boundary between a *control* and a *coincidence* actually sits.

Our model refused every jailbreak we threw at it — but a refusal is not a boundary, because it varies with temperature, model version and phrasing. So we stopped relying on model behaviour and made the controls deterministic: enforce at ingress, allow-list output, sign the authorisation. We also learned to distrust our own fixes — the best bug we found was in the security control we had just built.

Second lesson: measure honestly. Our IR score is below target, and the tempting move was to tune retrieval against the benchmark until the number looked good. That would have optimised the metric rather than the system.

---

### 🙏 Academic Guidance and Appreciation

We are sincerely grateful to **[Tag: Mr. Samadhi Chathuranga Rathnayake]**, Lecturer in Charge of IT3041 – Information Retrieval and Web Analytics, for the opportunity and guidance throughout this project.

We also thank **[Tag: other lecturers who guided the project]** for their valuable feedback and technical guidance.

---

### 🤝 Meet Our Development Team

- **[Tag: Team Member 01]** — `[Role]`
- **[Tag: Team Member 02]** — `[Role]`
- **[Tag: Team Member 03]** — `[Role]`
- **[Tag: Team Member 04]** — `[Role]`

**GitHub:** `github.com/NullBitZer0/irwa-2026-public-transport`

---

### 🌟 Our Biggest Takeaway

*"The real potential of Agentic AI lies not in generating intelligent responses, but in designing systems where specialised agents, knowledge and tools collaborate — and where the system knows the difference between what it knows and what it is guessing."*

### 💬 What real-world challenge do you think Agentic AI could solve next?

We'd love to hear your ideas.

#AgenticAI #ArtificialIntelligence #MachineLearning #InformationRetrieval #NaturalLanguageProcessing #LargeLanguageModels #MultiAgentSystems #ResponsibleAI #AISecurity #AIInnovation #DataScience #SLIIT #SriLanka

---
---

# 📎 UPLOAD CHECKLIST — not part of the post

## A. Architecture diagrams (attach these first)

| # | File | What it shows |
|---|---|---|
| 1 | `docs/diagrams/system_diagram_overview.png` | Full system overview |
| 2 | `docs/diagrams/agent_flow_1_request_ingress.png` | Request ingress → gateway → router |
| 3 | `docs/diagrams/agent_flow_2_langgraph_state_graph.png` | LangGraph orchestration |
| 4 | `docs/diagrams/agent_flow_3_human_in_the_loop_booking_sequence.png` | HITL booking sequence |

## B. Application screenshots (pick 6–8, in this order — it tells a story)

| # | File | Story beat |
|---|---|---|
| 5 | `docs/video/stills/04_query_typed.png` | Natural-language query |
| 6 | `docs/video/stills/05_clarify_response.png` | Slot filling |
| 7 | `docs/video/stills/07_routes_returned.png` | Ranked routes |
| 8 | `docs/video/stills/08_provenance_closeup.png` | **Provenance — the differentiator** |
| 9 | `docs/video/stills/11_hitl_gate.png` | Human approval gate |
| 10 | `docs/video/stills/13_payment_portal.png` | Payment |
| 11 | `docs/video/stills/16_receipt.png` | Ticket issued |
| 12 | `docs/video/stills/20_pii_response.png` | PII tokenisation |

## C. Security & evaluation evidence

| # | File | What it shows |
|---|---|---|
| 13 | `evidence/screenshots/student1_fig_redteam_audit_suite.png` | 33-case red-team run |
| 14 | `evidence/screenshots/student1_fig_prompt_injection_refusal.png` | Injection resisted |
| 15 | `evidence/screenshots/student1_fig_guardrail_regression.png` | Guardrail regression suite |
| 16 | `evidence/screenshots/student2_fig_hitl_token_verification.png` | Signed approval token |
| 17 | `evidence/screenshots/student2_fig_ir_benchmark_metrics.png` | MRR / NDCG results |

## D. Demonstration video — required by the brief

**⚠️ No video file exists yet.** `docs/video/` contains only the 20 stills in
`docs/video/stills/`. The script is at `docs/video_script.md`.

Render the video from the stills, then attach it. The brief states this is mandatory.

---

## Before you post — check these

- [ ] Lecturer tagged, plus any other lecturers
- [ ] All four team members tagged with correct LinkedIn handles
- [ ] GitHub link is public, and no `.env` or API key is exposed
- [ ] Video rendered and attached
- [ ] Roles filled in for each team member
- [ ] You can defend every number in this post if asked

**Claims deliberately NOT made, because they aren't true:** MCP protocol
implementation, encryption at rest, TLS, native Sinhala, real payment gateway,
real operator integration, and any IR score above the actual 0.794 MRR. If an
examiner checks any of these, the post holds up — which is the point.
