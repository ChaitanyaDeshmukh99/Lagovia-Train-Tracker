# Lagovia Train Tracker

A single HTTP endpoint that returns live Belgian train departures for stations matching a name search. Built for the Digital Product School Engineering Track Technical Challenge.

Data source: [iRail API](https://docs.irail.be/) (free, no authentication required).

---

## How to Install and Run Locally

### Prerequisites

- Python 3.10 or later
- `pip`

### Install dependencies

```bash
pip install -r requirements.txt
```

### Start the server

```bash
uvicorn main:app --reload --port 8000
```

The API is now available at `http://localhost:8000`.  
Interactive OpenAPI docs (try it in the browser): `http://localhost:8000/docs`

---

## API Reference

### `GET /departures?q={query}`

Returns all departures scheduled within the next **15 minutes** from every station whose name contains (or fuzzy-matches) the query string.

#### Query parameter

| Parameter | Type   | Required | Description                                      |
|-----------|--------|----------|--------------------------------------------------|
| `q`       | string | Yes      | Station name substring, e.g. `Bru`, `Ghent`, `Liège` |

#### Success response — `200 OK`

```json
{
  "query": "Bru",
  "stations": [
    {
      "station_name": "Brussels-Central",
      "departures": [
        {
          "train": "IC 515",
          "destination": "Ghent-Sint-Pieters",
          "scheduled_time": "2026-05-29T14:32:00+00:00",
          "delay_minutes": 3
        }
      ]
    }
  ]
}
```

`stations` is empty (`[]`) when no matching station has a departure in the 15-minute window.  
`departures` for a station is never empty — stations with no in-window departures are omitted entirely.

#### Error response — `400 Bad Request` (query too short)

```json
{
  "error": "QUERY_TOO_SHORT",
  "message": "Search query must be at least 3 characters. Got 2.",
  "min_length": 3,
  "received_length": 2
}
```

#### Error response — `502 Bad Gateway` (iRail unreachable)

```json
{
  "error": "UPSTREAM_ERROR",
  "message": "iRail station list unavailable: ..."
}
```

#### Example requests

```bash
# Too short → 400
curl "http://localhost:8000/departures?q=Br"

# Substring match
curl "http://localhost:8000/departures?q=Bru"

# Accented characters (URL-encoded automatically by curl)
curl "http://localhost:8000/departures?q=Liège"

# Fuzzy match: "Antverpen" finds "Antwerpen-Centraal"
curl "http://localhost:8000/departures?q=Antverpen"
```

---

## Decisions, Trade-offs, and Known Limitations

### Decisions

**Python / FastAPI** — chosen over Node.js/Express because FastAPI natively supports async/await, generates OpenAPI docs automatically, and Pydantic models self-document the response contract. The async support matters here: when a query matches multiple stations the liveboards are fetched concurrently with `asyncio.gather`, keeping total latency close to a single round-trip instead of multiplying it.

**Station matching** — primary is a simple case-insensitive substring check. A fuzzy bonus pass uses `rapidfuzz.partial_ratio` (or `difflib.SequenceMatcher` on the station's first token if rapidfuzz is absent). This covers common misspellings like "Antverpen" → "Antwerpen-Centraal" without returning meaningless results for short, common substrings.

**15-minute window** — applied against the scheduled departure timestamp (`time` field from iRail), not the actual departure (`time + delay`). This matches the plain-language requirement "departures scheduled within the next 15 minutes" and means a train scheduled at `now + 14m` but running 20 minutes late still appears.

**Error shape** — `QUERY_TOO_SHORT` uses a structured JSON body (not just a string) so API consumers can branch on `error` codes programmatically.

**No station-list caching** — the iRail station list (~600 stations, ~80 KB) is fetched fresh on every request. For a production service a short TTL cache (`functools.lru_cache` with a timestamp check, or Redis) would be the obvious next step, but adds complexity not required by the brief.

### Known Limitations

- **No rate limiting** — a single request can trigger O(N) parallel calls to iRail where N is the number of matching stations. Queries like "S" (after 3 chars) could match many stations. In production this would need throttling.
- **Clock skew** — the 15-minute window is computed from the server clock, not the client's. On a correctly configured server this is fine.
- **iRail availability** — if iRail is down the endpoint returns 502. There is no local cache fallback.
- **Language** — the station list and direction names are requested in English (`lang=en`). Some Belgian station names are returned in French or Dutch regardless; this is an iRail limitation.

---

## AI Usage

See [AI_USAGE.md](AI_USAGE.md).
