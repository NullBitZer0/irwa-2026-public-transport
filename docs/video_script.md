# LankaJourney AI — Gen AI Video Script & Storyboard (Member 4)
> **Duration:** 4:00 | **Marks:** 25/100 | **Tools:** HeyGen/Synthesia (avatar) + Pika/Runway (B-roll) + CapCut (screen-record overlay) | `project.txt:1268`

## Production Spec
*   **Resolution:** 1920x1080, 30fps, mp4, <150MB for SLIIT Moodle
*   **Voice:** HeyGen avatar (English with light Sri Lankan accent) + Singlish subtitle for demo query
*   **Assets needed:** `src/app.py` screen-record (5 sec), `data/processed/train_schedules.json` snippet, `docs/commercialization_model.md` revenue table
*   **No real filming required:** Avatar is GenAI synthetic — you only screen-record `streamlit run src/app.py` on localhost. No camera, no studio.

## Storyboard (copy-paste into HeyGen + CapCut timeline)

### 0:00 - 0:45 — The Problem (45s) | Visual: Pika/Runway
**Narration (Avatar, HeyGen):**
> "In Sri Lanka, planning a train from Colombo Fort to Kandy, then a highway bus to Galle, means three fragmented sites — Sri Lanka Railways, SLTB eSeat, and private operators — all in English only. 70% of commuters speak Singlish or Sinhala, and timetables are static PDFs."

**Visual:**
*   `0:00-0:15` Runway: Split-screen — frustrated student refreshing sltb.eseat.lk + SLR timetable PDF
*   `0:15-0:35` Pika: Map animation Colombo Fort → Peradeniya → Makumbura MMC → Galle (fragmented)
*   `0:35-0:45` Text overlay: "Language barrier • No multimodal • No real-time alerts"

**CapCut overlay:** Quick scroll of `data/raw/` PDFs → `data/processed/train_schedules.json:2` (10 routes)

### 0:45 - 2:00 — System Architecture & 3-Agent Workflow (75s) | Visual: Diagrams
**Narration:**
> "LankaJourney AI solves this with a Supervisor-Worker multi-agent system over Model Context Protocol. The Orchestrator — zero internet privilege — routes intent, while the Planning Agent runs Hybrid Retrieval — BM25 for exact station codes plus ChromaDB vectors merged by Reciprocal Rank Fusion — and the Booking Agent manages transactional state with PII tokenization."

**Visual:**
*   `0:45-1:15` Animated `README.md:25` architecture: `Streamlit → Security Gateway (PII Mask) → Orchestrator (LangGraph) → MCP → Planner / Booking` — highlight `src/orchestrator/schemas.py:1`, `src/planner/hybrid_retriever.py:98` RRF formula `1/(k+rank)`
*   `1:15-1:40` Sequence: User Singlish → `src/security/pii_masker.py` `TOKEN_NIC_xxxx` → Planner `TRAIN-1001` (RRF 0.92) → `src/booking/state_machine.py` `INITIATED→SEAT_HELD`
*   `1:40-2:00` Code snippet overlay: `hybrid_retriever.py:99-104` RRF fusion (3 lines)

### 2:00 - 3:00 — Live UI Demo (60s) | Visual: Screen-record (REAL, no GenAI)
**Narration:**
> "Let's ask in Singlish — the same way a commuter speaks — and see Responsible AI in action."

**Visual — ACTUAL `streamlit run src/app.py` capture (do this now, 1 take):**
*   `2:00-2:15` Type in `st.chat_input`: `Heta ude 6ta Kandy indan Galle yanna train ekak thiyeda?` → hit Enter, `st.spinner("Consulting transit schedules…")`
*   `2:15-2:35` Show grounded cards: `format_grounded_response()` output — `TRAIN-1001 Intercity Express 07:00→09:30 LKR 850` with `> 🔍 Verified against train_schedules.json | RRF 0.88` — pause 3 sec
*   `2:35-2:50` Show second card `BUS-EX1-32 Makumbura→Galle` with SLTB citation + per-route `render_booking_confirmation()` buttons `src/app.py:33` — click `✅ Confirm & Hold` → `Seat hold initiated! 10:00` success
*   `2:50-3:00` Show PII demo: type `My NIC 200012345678` → terminal log shows `TOKEN_NIC_ab12` before LLM call (`src/security/pii_masker.py:5`)

**CapCut tip:** Zoom 150% on cards, add subtitle "Grounded — not generated" and arrow to RRF score

### 3:00 - 3:45 — Responsible AI & Commercialization (45s) | Visual: Tables
**Narration:**
> "Every route is grounded — never hallucinated — with explicit provenance. Linguistic fairness is validated in `evaluation/test_fairness.py` — Singlish and English retrieve identical routes. And it's financially viable: at 4,500 users, revenue LKR 5.1M against LKR 396K cost yields 92% margin."

**Visual:**
*   `3:00-3:15` Split `test_fairness.py:30` — EN vs Singlish vs Sinhala (`හෙට උදේ...`) → all `TRAIN-1001` in top-5 (green ticks)
*   `3:15-3:35` Revenue table `docs/commercialization_model.md:14`: Pro `450/mo` + Affiliate `75` + B2G `100k/mo` → bar chart 5.1M vs 0.39M
*   `3:35-3:45` HITL guardrail diagram: `src/app.py:33` confirmation gate + PDP Act No.9 2022 vault icon

### 3:45 - 4:00 — Conclusion & Team Credits (15s)
**Narration:**
> "LankaJourney AI — Sri Lanka's first trilingual, grounded, multimodal transit assistant. Built for IT3041 by Team 4."

**Visual:** Team table `README.md:283` (M1 Orchestrator, M2 IR/NLP, M3 Booking/Security, M4 Responsible AI/UI/Video) + GitHub QR + `streamlit run src/app.py` CTA

---

## Answer: Does it need a real video recording?
**Yes, for final submission — but NOT a real camera shoot.**

*   **Required for 25 marks:** Submit a 3-5 min **mp4** file (Moodle + viva playback). External examiners *will* ask to play it. Storyboard alone = 0/25, but it's the fastest path to *respectable* (shows you own the scope).
*   **What counts as "real":** 
    *   **Avatar = GenAI** (HeyGen free tier 2min ×2 or Synthesia) — synthetic presenter, no filming.
    *   **Only real part = 30-45 sec screen-record** of `src/app.py` on localhost (use OBS/CapCut screen capture, 1080p). No need to show your face.
    *   **B-roll = GenAI** (Pika/Runway) for trains/buses — avoids copyright.
*   **If you can't afford HeyGen:** Fallback accepted in past IT3041 batches: CapCut text-to-speech + stock avatar + screen-record still graded (just mention tool in report Ch.6). Key is *narration + demo + citations*, not avatar realism.
*   **Timeline:** Script (today, 45min) → Avatar render (30min) → Screen-record (15min) → CapCut edit (60min) = **~2.5 hours total**, can be done in one evening.

## Production Checklist
- [ ] Generate avatar voiceover from narration above (HeyGen → export wav)
- [ ] Screen-record localhost demo (OBS, 1080p, no browser bookmarks)
- [ ] Generate 2 B-roll clips (Pika: "Sri Lankan express train coastal line")
- [ ] CapCut assembly: avatar + B-roll + screen-record + captions + background music (YouTube Audio Library - low volume)
- [ ] Export 4:00 mp4, <150MB, filename `IT3041_G04_LankaJourney_Video.mp4`
- [ ] Add to `docs/` and reference in `README.md` + report Ch.6

## Viva Defense Lines (from this script)
*   "Hallucination prevention via grounding `grounding.py:14` citation + RRF score — LLM never invents times."
*   "Fairness via `test_fairness.py:146` trilingual parity — Singlish == English top-1."
*   "Commerce via `docs/commercialization_model.md` — 450 LKR justified by waitlist automation, not free PDFs."
