import json
import os
from pathlib import Path
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="eShopCo Latency Metrics API")

# ── CORS ────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# ── Load telemetry bundle once at cold-start ─────────────────────────────────
_DATA_PATH = Path(__file__).parent.parent / "data" / "q-vercel-latency.json"

def _load_data():
    with open(_DATA_PATH, "r") as fh:
        return json.load(fh)

_TELEMETRY: list = _load_data()


# ── Helpers ──────────────────────────────────────────────────────────────────
def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile (same as numpy default)."""
    if not values:
        return 0.0
    sv = sorted(values)
    n = len(sv)
    k = (n - 1) * pct / 100.0
    lo = int(k)
    hi = lo + 1
    if hi >= n:
        return sv[lo]
    return sv[lo] + (k - lo) * (sv[hi] - sv[lo])


# ── Schema ────────────────────────────────────────────────────────────────────
class MetricsRequest(BaseModel):
    regions: List[str]
    threshold_ms: float


class RegionMetrics(BaseModel):
    avg_latency: float
    p95_latency: float
    avg_uptime: float
    breaches: int


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/")
def health():
    return {"status": "ok", "service": "eShopCo Latency Metrics"}


@app.post("/metrics", response_model=dict)
def get_metrics(req: MetricsRequest):
    """
    Accepts:  {"regions": ["apac", "emea"], "threshold_ms": 184}
    Returns:  per-region avg_latency, p95_latency, avg_uptime, breaches
    """
    if not req.regions:
        raise HTTPException(status_code=400, detail="'regions' list must not be empty.")

    # Group telemetry by region (case-insensitive)
    grouped: dict[str, dict] = {}
    for record in _TELEMETRY:
        r = record["region"].lower()
        if r not in grouped:
            grouped[r] = {"latencies": [], "uptimes": []}
        grouped[r]["latencies"].append(record["latency_ms"])
        grouped[r]["uptimes"].append(record["uptime"])

    result = {}
    for region in req.regions:
        key = region.lower()
        if key not in grouped:
            raise HTTPException(
                status_code=404,
                detail=f"No telemetry data found for region '{region}'. "
                       f"Available: {sorted(grouped.keys())}",
            )

        latencies = grouped[key]["latencies"]
        uptimes   = grouped[key]["uptimes"]
        threshold = req.threshold_ms

        result[region] = {
            "avg_latency": round(sum(latencies) / len(latencies), 4),
            "p95_latency": round(_percentile(latencies, 95), 4),
            "avg_uptime":  round(sum(uptimes) / len(uptimes), 6),
            "breaches":    sum(1 for l in latencies if l > threshold),
        }

    return result
