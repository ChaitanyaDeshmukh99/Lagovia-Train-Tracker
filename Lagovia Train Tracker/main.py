import asyncio
import difflib
import re
import time
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class Departure(BaseModel):
    train: str
    destination: str
    scheduled_time: str   # ISO 8601, UTC
    delay_minutes: int


class StationResult(BaseModel):
    station_name: str
    departures: list[Departure]


class DeparturesResponse(BaseModel):
    query: str
    stations: list[StationResult]


class ErrorResponse(BaseModel):
    error: str
    message: str
    min_length: int
    received_length: int


# ---------------------------------------------------------------------------
# App + CORS
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Lagovia Train Tracker",
    description=(
        "Search Belgian train stations by name and retrieve departures "
        "scheduled within the next 15 minutes. Data source: iRail API."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# iRail constants and helpers
# ---------------------------------------------------------------------------

IRAIL_BASE = "https://api.irail.be"
STATIONS_URL = f"{IRAIL_BASE}/stations/?format=json&lang=en"
WINDOW_SECONDS = 900  # 15 minutes


async def _fetch_stations(client: httpx.AsyncClient) -> list[dict]:
    resp = await client.get(STATIONS_URL, timeout=10.0)
    resp.raise_for_status()
    return resp.json().get("station", [])


async def _fetch_liveboard(client: httpx.AsyncClient, station_id: str) -> list[dict]:
    url = f"{IRAIL_BASE}/liveboard/?id={station_id}&format=json&lang=en"
    try:
        resp = await client.get(url, timeout=10.0)
        if resp.status_code != 200:
            return []
        return resp.json().get("departures", {}).get("departure", []) or []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Station matching (substring primary, fuzzy bonus)
# ---------------------------------------------------------------------------

# Use rapidfuzz for higher-quality partial matching if installed;
# fall back to stdlib difflib (always available).
try:
    from rapidfuzz import fuzz as _rfuzz

    def _fuzzy_score(q: str, name: str) -> float:
        return _rfuzz.partial_ratio(q, name) / 100.0

except ImportError:
    def _fuzzy_score(q: str, name: str) -> float:  # type: ignore[misc]
        # Also try matching against the first token (e.g. "Antwerpen" from
        # "Antwerpen-Centraal") which gives a much better ratio for partial
        # queries like "Antverpen".
        first_token = re.split(r"[-\s]", name)[0]
        r_full = difflib.SequenceMatcher(None, q, name).ratio()
        r_token = difflib.SequenceMatcher(None, q, first_token).ratio()
        return max(r_full, r_token)


def _match_stations(stations: list[dict], query: str) -> list[dict]:
    q = query.lower()
    seen: set[str] = set()
    results: list[dict] = []

    # Primary: case-insensitive substring
    for s in stations:
        if q in s.get("name", "").lower():
            results.append(s)
            seen.add(s["id"])

    # Bonus: fuzzy — catches typos like "Antverpen" → "Antwerpen-Centraal"
    for s in stations:
        if s["id"] in seen:
            continue
        if _fuzzy_score(q, s.get("name", "").lower()) >= 0.7:
            results.append(s)
            seen.add(s["id"])

    return results


# ---------------------------------------------------------------------------
# Departure filtering
# ---------------------------------------------------------------------------

def _format_train(dep: dict) -> str:
    # Prefer the pre-formatted shortname from vehicleinfo (iRail v1).
    # Fall back to parsing the raw vehicle string for older responses.
    shortname = dep.get("vehicleinfo", {}).get("shortname", "")
    if shortname:
        return shortname
    label = dep.get("vehicle", "").split(".")[-1]
    return re.sub(r"([A-Za-z]+)(\d+)", r"\1 \2", label)


def _filter_departures(raw: list[dict]) -> list[Departure]:
    now_ts = int(time.time())
    cutoff = now_ts + WINDOW_SECONDS
    out: list[Departure] = []

    for d in raw:
        try:
            dep_ts = int(d.get("time", 0))
            delay_sec = int(d.get("delay", 0))
        except (ValueError, TypeError):
            continue

        if not (now_ts <= dep_ts <= cutoff):
            continue

        # iRail v1 uses "station" for destination; older format used "direction.name"
        destination = d.get("station") or d.get("direction", {}).get("name") or "Unknown"

        out.append(Departure(
            train=_format_train(d),
            destination=destination,
            scheduled_time=datetime.fromtimestamp(dep_ts, tz=timezone.utc).isoformat(),
            delay_minutes=delay_sec // 60,
        ))

    return out


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@app.get(
    "/departures",
    response_model=DeparturesResponse,
    responses={400: {"model": ErrorResponse}},
    summary="Upcoming departures for stations matching a name query",
    description=(
        "Returns all departures scheduled within the next **15 minutes** "
        "from every station whose name contains (or fuzzy-matches) the query. "
        "Query must be at least 3 characters."
    ),
)
async def get_departures(
    q: str = Query(..., description="Station name substring, e.g. 'Bru', 'Ghent', 'Liège'"),
) -> DeparturesResponse:
    if len(q) < 3:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "QUERY_TOO_SHORT",
                "message": f"Search query must be at least 3 characters. Got {len(q)}.",
                "min_length": 3,
                "received_length": len(q),
            },
        )

    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            all_stations = await _fetch_stations(client)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail={"error": "UPSTREAM_ERROR", "message": f"iRail station list unavailable: {exc}"},
            ) from exc

        matched = _match_stations(all_stations, q)

        raw_boards = await asyncio.gather(
            *[_fetch_liveboard(client, s["id"]) for s in matched]
        )

    stations: list[StationResult] = []
    for station, raw in zip(matched, raw_boards):
        deps = _filter_departures(raw)
        if deps:
            stations.append(StationResult(station_name=station.get("name", ""), departures=deps))

    return DeparturesResponse(query=q, stations=stations)
