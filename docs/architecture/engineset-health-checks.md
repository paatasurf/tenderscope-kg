# EngineSet Health Checks

## Version

1.0.0 (Phase 1)

## Endpoints

| Method | Path              | Purpose                                           |
|--------|-------------------|---------------------------------------------------|
| GET    | `/api/v1/graph/health` | Liveness probe — returns 200 if the process is up. |
| GET    | `/api/v1/graph/ready`  | Readiness probe — repository + engines healthy.  |

The legacy prefix `/api/graph` mirrors these endpoints and is governed by the
same deprecation policy as the rest of the legacy API.

## Liveness

`/health` is intentionally cheap. It does not touch the database and returns:

```json
{"status": "alive", "service": "tenderscope-kg"}
```

Use this endpoint for load balancer and container liveness checks.

## Readiness

`/ready` exercises the shared `EngineSet.health()` method:

- Verifies each engine instance is present (`ok`).
- Probes repository connectivity via `BizQueryEngine.graph_statistics()`.
- Returns `200` when everything is healthy:

```json
{
  "status": "ok",
  "repository": "ok",
  "engines": {
    "biz": "ok",
    "cie": "ok",
    "rie": "ok",
    "cei": "ok",
    "bie": "ok",
    "oie": "ok",
    "ede": "ok"
  }
}
```

If the repository probe fails or any engine is missing, `/ready` returns `503`
with a `detail` object describing the failure.

## Railway Configuration

Configure the Railway health check URL to `/api/v1/graph/ready` for readiness
and `/api/v1/graph/health` for liveness.

## Implementation

- `EngineSet.health()` is defined in `src/tenderscope_kg/server_engines.py`.
- `/health` and `/ready` routes are defined in `src/tenderscope_kg/rest_server.py`.
