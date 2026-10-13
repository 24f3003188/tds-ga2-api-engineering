from fastapi import FastAPI, Header, HTTPException, Query, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from threading import Lock
from time import monotonic
from uuid import uuid4
import base64
import math

app = FastAPI()

T = 40
R = 19
WINDOW = 10

catalog = [{"id": i} for i in range(1, T + 1)]
idempotency_keys = {}
rate_buckets = {}
lock = Lock()


@app.get("/")
def root():
    return {"message": "Orders API is running"}


@app.post("/orders")
def create_order(
    response: Response,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    with lock:
        if idempotency_key in idempotency_keys:
            response.status_code = 200
            return idempotency_keys[idempotency_key]

        order = {"id": str(uuid4())}
        idempotency_keys[idempotency_key] = order
        response.status_code = 201
        return order


@app.get("/orders")
def list_orders(
    limit: int = Query(10, ge=1),
    cursor: str | None = Query(None),
):
    offset = 0

    if cursor:
        try:
            offset = int(base64.urlsafe_b64decode(cursor.encode()).decode())
            if offset < 0:
                raise ValueError
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid cursor")

    items = catalog[offset:offset + limit]
    next_offset = offset + len(items)

    next_cursor = None
    if next_offset < T:
        next_cursor = base64.urlsafe_b64encode(
            str(next_offset).encode()
        ).decode()

    return {"items": items, "next_cursor": next_cursor}


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    # Let CORS preflight through untouched
    if request.method == "OPTIONS":
        return await call_next(request)

    client_id = request.headers.get("X-Client-Id", "anonymous")
    now = monotonic()

    with lock:
        timestamps = rate_buckets.setdefault(client_id, [])
        timestamps[:] = [t for t in timestamps if now - t < WINDOW]

        if len(timestamps) >= R:
            retry_after = max(1, math.ceil(WINDOW - (now - timestamps[0])))
            return Response(
                content='{"detail":"Rate limit exceeded"}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(retry_after)},
            )

        timestamps.append(now)

    return await call_next(request)


# IMPORTANT: added LAST so it is the OUTERMOST middleware.
# This guarantees CORS headers appear on every response, including 429s.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],