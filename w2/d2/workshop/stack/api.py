#!/usr/bin/env python3
"""AIOps workshop API + live replay engine.

Endpoints:
  GET  /api/health
  GET  /api/services              -> list of services with current state
  GET  /api/topology              -> nodes + edges for graph viz
  GET  /api/scenarios             -> list of 10 scenarios with metadata
  GET  /api/metrics?service=X&metric=Y&scenario=SNN&from=&to=
  GET  /api/alerts?scenario=SNN&since=
  GET  /api/log_patterns?scenario=SNN
  GET  /api/traces?scenario=SNN&phase=active
  GET  /api/incidents/{key}/rca   -> on-the-fly RCA via PageRank
  POST /api/trigger/{scenario_id}?speed=60 -> start live replay
  POST /api/stop                  -> stop active replay
  GET  /api/state                 -> current replay state
  GET  /api/live/recent?limit=50  -> latest live events (since trigger)
  GET  /stream                    -> SSE of live events (server-sent)
  GET  /                          -> dashboard.html

Run: uv run python stack/api.py  (or `uvicorn stack.api:app --port 8000`)
"""
from __future__ import annotations
import asyncio, json, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "workshop.db"
DASHBOARD_HTML = ROOT / "stack" / "dashboard.html"

app = FastAPI(title="AIOps Workshop API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# In-memory state for replay
_replay_task: Optional[asyncio.Task] = None
_replay_scenario: Optional[str] = None
_replay_speed: int = 60
_replay_started_at: Optional[float] = None
_event_queue: asyncio.Queue = asyncio.Queue(maxsize=2048)
_pause_event: Optional[asyncio.Event] = None  # created on first trigger; set = running, cleared = paused


def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


@app.get("/api/health")
def health():
    return {"status": "ok", "db": str(DB_PATH), "db_exists": DB_PATH.exists()}


@app.get("/api/services")
def services():
    with db() as c:
        rows = c.execute("SELECT id, name, namespace, baseline_json FROM services").fetchall()
    return [{"id": r["id"], "name": r["name"], "namespace": r["namespace"],
             "baseline": json.loads(r["baseline_json"] or "{}")} for r in rows]


@app.get("/api/topology")
def topology():
    with db() as c:
        edges = c.execute("SELECT src_service, dst_service, edge_type FROM topology").fetchall()
    nodes = set()
    edge_list = []
    for e in edges:
        nodes.add(e["src_service"]); nodes.add(e["dst_service"])
        edge_list.append({"source": e["src_service"], "target": e["dst_service"], "type": e["edge_type"]})
    return {"nodes": [{"id": n} for n in sorted(nodes)], "edges": edge_list}


@app.get("/api/scenarios")
def scenarios():
    with db() as c:
        rows = c.execute("SELECT id, name, root_cause, root_service, block, narrative FROM scenarios ORDER BY id").fetchall()
    return [dict(r) for r in rows]


@app.get("/api/scenarios/{sid}")
def scenario_detail(sid: str):
    with db() as c:
        r = c.execute("SELECT full_json FROM scenarios WHERE id = ?", (sid,)).fetchone()
    if not r:
        raise HTTPException(404, f"scenario {sid} not found")
    return json.loads(r["full_json"])


@app.get("/api/metrics")
def metrics(service: Optional[str] = None, metric: Optional[str] = None,
            scenario: Optional[str] = None, limit: int = 5000):
    q = "SELECT timestamp, service, metric, value, scenario FROM metrics WHERE 1=1"
    params = []
    if service: q += " AND service = ?"; params.append(service)
    if metric: q += " AND metric = ?"; params.append(metric)
    if scenario: q += " AND scenario = ?"; params.append(scenario)
    q += " ORDER BY timestamp LIMIT ?"; params.append(limit)
    with db() as c:
        rows = c.execute(q, params).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/alerts")
def alerts(scenario: Optional[str] = None):
    q = "SELECT * FROM alerts WHERE 1=1"
    params = []
    if scenario:
        q += " AND scenario = ?"; params.append(scenario)
    q += " ORDER BY opened_at"
    with db() as c:
        return [dict(r) for r in c.execute(q, params).fetchall()]


@app.get("/api/log_patterns")
def log_patterns(scenario: Optional[str] = None):
    q = "SELECT * FROM log_patterns WHERE 1=1"
    params = []
    if scenario:
        q += " AND scenario = ?"; params.append(scenario)
    with db() as c:
        return [dict(r) for r in c.execute(q, params).fetchall()]


@app.get("/api/traces")
def traces(scenario: Optional[str] = None, phase: Optional[str] = None):
    q = "SELECT trace_id, scenario, phase, spans_json FROM traces WHERE 1=1"
    params = []
    if scenario:
        q += " AND scenario = ?"; params.append(scenario)
    if phase:
        q += " AND phase = ?"; params.append(phase)
    with db() as c:
        rows = c.execute(q, params).fetchall()
    return [{"trace_id": r["trace_id"], "scenario": r["scenario"], "phase": r["phase"],
             "spans": json.loads(r["spans_json"])} for r in rows]


@app.get("/api/rca/{scenario_id}")
def rca(scenario_id: str, services: Optional[str] = None):
    """Run PageRank on reverse-topology subgraph of alerting services.

    If `services` query is given (comma-separated), use only those as alerting set
    (live partial view). Otherwise fall back to the scenario's full pre-baked alerts.
    """
    try:
        import networkx as nx
    except ImportError:
        return JSONResponse({"error": "networkx not installed"}, status_code=500)
    with db() as c:
        topo_rows = c.execute("SELECT src_service, dst_service FROM topology").fetchall()
        sc = c.execute("SELECT full_json FROM scenarios WHERE id = ?", (scenario_id,)).fetchone()
        if services is None:
            alerts_rows = c.execute("SELECT service FROM alerts WHERE scenario = ?",
                                    (scenario_id,)).fetchall()
            alerting_services = list({a["service"] for a in alerts_rows})
        else:
            alerting_services = [s.strip() for s in services.split(",") if s.strip()]
    if not sc:
        raise HTTPException(404, "scenario not found")
    expected = json.loads(sc["full_json"]).get("expected_rca", {})

    G = nx.DiGraph()
    for e in topo_rows:
        G.add_edge(e["src_service"], e["dst_service"])
    Gr = G.reverse()
    personalization = {n: 1.0 for n in Gr.nodes()}
    for s in alerting_services:
        if s in personalization:
            personalization[s] = 10.0
    if not alerting_services:
        return {
            "scenario": scenario_id,
            "alerting_services": [],
            "ranked_root_causes": [],
            "expected": expected,
        }
    try:
        pr = nx.pagerank(Gr, personalization=personalization, max_iter=100)
    except Exception:
        pr = {n: 1.0 / max(1, len(Gr.nodes())) for n in Gr.nodes()}
    ranked = sorted(pr.items(), key=lambda kv: -kv[1])[:5]
    return {
        "scenario": scenario_id,
        "alerting_services": alerting_services,
        "ranked_root_causes": [{"service": s, "score": round(v, 4)} for s, v in ranked],
        "expected": expected,
    }


@app.get("/api/state")
def state():
    return {
        "active_scenario": _replay_scenario,
        "speed": _replay_speed,
        "started_at": _replay_started_at,
        "elapsed_sec": (time.time() - _replay_started_at) if _replay_started_at else None,
    }


@app.get("/api/live/recent")
def live_recent(limit: int = 50):
    with db() as c:
        rows = c.execute("SELECT * FROM live_events ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
    return [{"seq": r["seq"], "ts": r["ts"], "kind": r["kind"],
             "payload": json.loads(r["payload_json"])} for r in rows]


async def replay_engine(scenario_id: str, speed: int):
    """Stream metrics+alerts+log_patterns of a scenario in chronological order,
    sleeping `1/speed` per real-second of scenario time. Pushes to SSE queue + DB.
    Clears active replay state on natural completion so /api/trigger can re-fire
    without requiring an explicit /api/stop first.
    """
    global _replay_started_at, _replay_scenario
    _replay_started_at = time.time()
    # Pull all events for this scenario, sorted by ts
    with db() as c:
        metrics_rows = c.execute("SELECT timestamp, service, metric, value FROM metrics "
                                 "WHERE scenario = ? ORDER BY timestamp", (scenario_id,)).fetchall()
        alert_rows = c.execute("SELECT * FROM alerts WHERE scenario = ? ORDER BY opened_at",
                               (scenario_id,)).fetchall()
        lp_rows = c.execute("SELECT * FROM log_patterns WHERE scenario = ? ORDER BY first_seen_at",
                            (scenario_id,)).fetchall()

    # Merge into a single timeline
    events = []
    for r in metrics_rows:
        events.append((r["timestamp"], "metric", {"service": r["service"], "metric": r["metric"], "value": r["value"]}))
    for r in alert_rows:
        events.append((r["opened_at"], "alert", {"id": r["id"], "service": r["service"], "rule": r["rule_id"],
                                                  "severity": r["severity"], "phase": r["phase"]}))
    for r in lp_rows:
        events.append((r["first_seen_at"], "log_pattern", {"service": r["service"], "count": r["count"],
                                                            "pattern": r["pattern"], "phase": r["phase"]}))
    events.sort(key=lambda e: e[0])
    if not events:
        return
    t0_scenario = datetime.fromisoformat(events[0][0])
    real_start = time.monotonic()

    for ts_iso, kind, payload in events:
        try:
            dt = (datetime.fromisoformat(ts_iso) - t0_scenario).total_seconds()
        except ValueError:
            dt = 0
        wait = (dt / max(1, speed)) - (time.monotonic() - real_start)
        if wait > 0:
            try:
                await asyncio.sleep(wait)
            except asyncio.CancelledError:
                return
        # Pause check — if paused, block here until /api/resume; advance real_start so timing is preserved.
        if _pause_event is not None and not _pause_event.is_set():
            paused_at = time.monotonic()
            try:
                await _pause_event.wait()
            except asyncio.CancelledError:
                return
            real_start += time.monotonic() - paused_at
        evt = {"scenario_ts": ts_iso, "kind": kind, "payload": payload}
        try:
            _event_queue.put_nowait(evt)
        except asyncio.QueueFull:
            try: _event_queue.get_nowait()
            except: pass
            _event_queue.put_nowait(evt)
        # persist
        try:
            with db() as c:
                c.execute("INSERT INTO live_events(ts, kind, payload_json) VALUES (?,?,?)",
                          (ts_iso, kind, json.dumps(payload)))
                c.commit()
        except Exception:
            pass
    # Natural completion — clear state so next /api/trigger doesn't 409.
    _replay_scenario = None
    _replay_started_at = None


@app.post("/api/trigger/{scenario_id}")
async def trigger(scenario_id: str, speed: int = 60):
    """Start replaying a scenario. speed=60 -> 60x real-time (35 min scenario in 35 sec)."""
    global _replay_task, _replay_scenario, _replay_speed, _pause_event
    if _replay_task and not _replay_task.done():
        return JSONResponse({"error": "another replay is active",
                             "active": _replay_scenario}, status_code=409)
    with db() as c:
        ok = c.execute("SELECT 1 FROM scenarios WHERE id = ?", (scenario_id,)).fetchone()
    if not ok:
        raise HTTPException(404, "scenario not found")
    _replay_scenario = scenario_id
    _replay_speed = speed
    if _pause_event is None:
        _pause_event = asyncio.Event()
    _pause_event.set()  # ensure not paused on new trigger
    _replay_task = asyncio.create_task(replay_engine(scenario_id, speed))
    return {"status": "started", "scenario": scenario_id, "speed": speed}


@app.post("/api/pause")
async def pause_replay():
    if _pause_event is None or _replay_task is None or _replay_task.done():
        return JSONResponse({"error": "no active replay"}, status_code=409)
    _pause_event.clear()
    return {"status": "paused", "scenario": _replay_scenario}


@app.post("/api/resume")
async def resume_replay():
    if _pause_event is None or _replay_task is None or _replay_task.done():
        return JSONResponse({"error": "no active replay"}, status_code=409)
    _pause_event.set()
    return {"status": "resumed", "scenario": _replay_scenario}


@app.post("/api/stop")
async def stop():
    global _replay_task, _replay_scenario, _replay_started_at, _pause_event
    if _pause_event is not None:
        _pause_event.set()  # release any pending paused task before cancel
    if _replay_task and not _replay_task.done():
        _replay_task.cancel()
    s = _replay_scenario
    _replay_scenario = None
    _replay_started_at = None
    return {"status": "stopped", "was": s}


@app.get("/stream")
async def stream():
    async def gen():
        yield "retry: 3000\n\n"
        while True:
            try:
                evt = await asyncio.wait_for(_event_queue.get(), timeout=15.0)
                yield f"data: {json.dumps(evt)}\n\n"
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/")
def root():
    if DASHBOARD_HTML.exists():
        return FileResponse(DASHBOARD_HTML)
    return {"status": "ok", "hint": "dashboard.html not found"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
