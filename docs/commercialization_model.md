# LankaJourney AI — Commercialization & Unit Economics (Member 4)

> Responsible AI, Commercialization & Media Lead | IT3041 IRWA 2026

## 1. Target Market

| Segment | Persona | Pain |
|---|---|---|
| **Daily inter-provincial commuters** | Office workers Colombo <-> Kandy/Galle | Fragmented SLR/SLTB sites, language barrier |
| **University students** | SLIIT/Colombo regional hubs | Last-mile Makumbura MMC transfers, waitlists |
| **Tourists** | Coastal/Ella Odyssey travellers | No Singlish/Sinhala support, scam overcharges |

## 2. Revenue Streams (LKR)

| Stream | Pricing | Assumption |
|---|---|---|
| **Freemium B2C** | Free | Route lookup & timetable (acquisition) |
| **Commuter Pro** | **LKR 450 / month** | Waitlist alerts + SMS disruption updates |
| **Operator Affiliate Fee** | **LKR 50-100 / booking** (model: LKR 75 avg) | Via sltb.eseat.lk & SLR mock gateway |
| **B2G Demand Analytics** | **LKR 100,000 / month** | Anonymized heatmaps for NTC / Municipal |

## 3. Unit Economics (Year 1 @ 4,500 MAU — README § Commercialization)

**Formula:** `Net Margin = (Revenue - OPEX) / Revenue * 100`

| Line | Calculation | Annual LKR |
|---|---|---|
| Pro Subscriptions | 225 users × 450 × 12 | 1,215,000 |
| Affiliate Fees | 3,000 bookings/mo × 75 × 12 | 2,700,000 |
| B2G License | 1 × 100,000 × 12 | 1,200,000 |
| **Total Revenue** |  | **5,115,000** |
| LLM Inference (Groq/GPT-4o-mini) | tokens | 180,000 |
| Cloud (Chroma/hosting) | | 126,000 |
| SMS Gateway (Dialog/Mobitel @1.50/SMS) | | 90,000 |
| **Total OPEX** | | **396,000** |
| **Operating Profit** | 5,115,000 - 396,000 | **4,719,000** |
| **Margin** | 4,719/5,115 | **92.2%** |

> Viva answer: commuters pay 450 because free timetables are static/fragmented — Pro saves hours of portal refreshing + guarantees waitlist seat on high-demand lines (Colombo-Badulla Ella Odyssey).

## 4. Go-to-Market & Deployment Roadmap

**Phase 1 — Beta Pilot (Month 1-3):** PWA + Telegram bot, target Colombo-Malabe & Colombo-Kandy university corridors. Collect 30-query benchmark parity data.
**Phase 2 — Operator Partnerships (Month 4-8):** Dashboard for private highway operators to list unscheduled return trips (Makumbura MMC).
**Phase 3 — B2G Licensing (Month 9-12):** Anonymized OD heatmaps to NTC, municipal demand forecasting.

**Deployment:** Streamlit Frontend (`src/app.py:20`) + Orchestrator :8000 + Planner :8001 + Booking :8002 via FastMCP/JSON-RPC. Vector DB: ChromaDB (local) / Qdrant cloud. LLM: Groq `llama-3.1-8b-instant` (`GROQ_MODEL` in `.env.example:7`).

## 5. Responsible AI Link

Every revenue-generating booking is gated by HITL (`src/app.py:render_booking_confirmation`) and grounded citations (`src/responsible_ai/grounding.py:14`) to meet PDP Act No.9 2022 and prevent hallucinated routes — core to commercial trust.

---
*Source: project.txt:1250 & README.md:96 — Member 4 deliverable*
