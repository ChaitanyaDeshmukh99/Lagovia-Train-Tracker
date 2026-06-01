import asyncio
import difflib
import re
import time
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# --- Models ---

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


# --- App ---

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


# --- iRail helpers ---

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


# --- Station matching ---

def _match_stations(stations: list[dict], query: str) -> list[dict]:
    q = query.lower()
    results: list[dict] = []

    for s in stations:
        name = s.get("name", "").lower()
        first_token = re.split(r"[-\s]", name)[0]
        is_substring = q in name
        fuzzy_score = max(
            difflib.SequenceMatcher(None, q, name).ratio(),
            difflib.SequenceMatcher(None, q, first_token).ratio(),
        )
        if is_substring or fuzzy_score >= 0.7:
            results.append(s)

    return results


# --- Departure filtering ---

def _format_train(dep: dict) -> str:
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

        destination = d.get("station") or d.get("direction", {}).get("name") or "Unknown"
        out.append(Departure(
            train=_format_train(d),
            destination=destination,
            scheduled_time=datetime.fromtimestamp(dep_ts, tz=timezone.utc).isoformat(),
            delay_minutes=delay_sec // 60,
        ))

    return out


# --- Endpoint ---

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
