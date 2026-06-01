# AI Usage Report

## Tools Used

**Claude Code (Anthropic claude-sonnet-4-6)** — used as the primary coding assistant throughout the project via the Claude Code CLI/VS Code extension.

---

## What AI Was Used For

### 1. Implementation Planning

The challenge brief was provided to Claude, which then explored the iRail API structure and designed a detailed implementation plan covering:
- FastAPI route design and Pydantic response models
- Concurrent liveboard fetching with `asyncio.gather`
- Substring + fuzzy station matching strategy
- 15-minute departure window filtering logic
- Error shape for the `QUERY_TOO_SHORT` contract

The plan was reviewed and approved before any code was written.

### 2. Code Generation

Claude generated the initial versions of:
- `main.py` — the full FastAPI application
- `requirements.txt`
- `README.md`
- This file (`AI_USAGE.md`)

---

## What Was Accepted As-Is

- The overall project structure (flat, single `main.py` for a backend-only submission)
- The Pydantic model definitions and response shapes
- The `asyncio.gather` pattern for concurrent liveboard fetches
- The `_format_train` helper (regex to turn `"BE.NMBS.IC515"` → `"IC 515"`)
- The `rapidfuzz` / `difflib` fallback pattern for fuzzy matching

---

## What Was Reviewed and Adjusted

- **Fuzzy threshold**: the plan proposed `ratio >= 0.6`; after thinking through the "Antverpen" → "Antwerpen-Centraal" example, the difflib fallback was adjusted to compare against the station's first token (split on `-` or space) rather than the full name, which gives a much higher ratio for partial-name queries. The final threshold was raised to `0.7` to reduce false positives.
- **Error handling for iRail outage**: added a 502 response if `_fetch_stations` itself fails — not in the original plan but clearly needed for a production-grade API.
- **CORS**: added `CORSMiddleware` with `allow_origins=["*"]` so the endpoint can be tested from browser-based tools (Postman, curl, etc.) without friction.

---

## What Was Rejected

- A suggestion to cache the iRail station list with `functools.lru_cache` — technically sound but adds complexity not required by the brief, so it was noted as a "known limitation" in the README instead.
- Using `rapidfuzz` as a hard dependency — kept as an optional enhancement with a clean `difflib` fallback so the project works out-of-the-box without it.

---

## Implementation Prompts Used

The core planning prompt provided to Claude included:
- The full challenge PDF
- The iRail API endpoint structure
- Desired JSON response shapes for both success and error cases
- Tech stack preference (Python FastAPI)
- A request to design concurrent fetching, fuzzy matching, and input validation logic

The conversation took place in Claude Code (VS Code extension, Plan Mode).
