import os, base64, datetime, requests
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, Header, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding

# Kalshi base
BASE = "https://api.elections.kalshi.com/trade-api/v2"
API_PREFIX = "/trade-api/v2"

# Env
ACCESS_KEY = os.environ["KALSHI_ACCESS_KEY"]
PRIVATE_PEM = os.environ["KALSHI_PRIVATE_KEY_PEM"].replace("\\n", "\n").encode("utf-8")
SERVICE_API_KEY = os.environ["SERVICE_API_KEY"]

# Load private key for request signing
_private_key = serialization.load_pem_private_key(PRIVATE_PEM, password=None)

def _sign(ts_ms: str, method: str, short_path: str) -> str:
    msg = f"{ts_ms}{method.upper()}{API_PREFIX}{short_path}".encode()
    sig = _private_key.sign(
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(sig).decode()

def _authed_get(short_path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ts = str(int(datetime.datetime.now().timestamp() * 1000))
    headers = {
        "KALSHI-ACCESS-KEY": ACCESS_KEY,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": _sign(ts, "GET", short_path),
    }
    r = requests.get(f"{BASE}{short_path}", params=params, headers=headers, timeout=30)
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()

def _paged_markets(base_params: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    """
    Pulls markets with cursor pagination until reaching limit or no more pages.
    """
    out: List[Dict[str, Any]] = []
    params = dict(base_params)
    params["limit"] = min(100, max(1, limit))  # Kalshi caps page size
    cursor = None

    while len(out) < limit:
        if cursor:
            params["cursor"] = cursor
        data = _authed_get("/markets", params)
        batch = data.get("markets", [])
        out.extend(batch)
        cursor = data.get("cursor")
        if not cursor or not batch:
            break

    return out[:limit]

# FastAPI app
app = FastAPI(title="Kalshi Odds Proxy", version="1.1.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Simple header auth for your proxy
def require_service_key(x_api_key: Optional[str] = Header(None)):
    if x_api_key != SERVICE_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/openapi.json")
def openapi_json():
    # Serve FastAPI generated schema for easy Actions import
    return app.openapi()

@app.get("/odds/search", dependencies=[Depends(require_service_key)])
def odds_search(
    keyword: Optional[str] = Query(None, description="Keyword to match in title or ticker. If omitted, returns all."),
    status: Optional[str] = Query(None, description="Filter by market status, for example open, closed, settled, active."),
    limit: int = Query(300, ge=1, le=1000)
):
    """
    If keyword is provided, filters client side over the paged list.
    If keyword is omitted, returns the raw paged list up to limit.
    """
    base_params: Dict[str, Any] = {}
    if status:
        base_params["status"] = status

    markets = _paged_markets(base_params, limit)

    if keyword:
        kw = keyword.lower()
        markets = [m for m in markets if kw in (m.get("title", "") + " " + m.get("ticker", "")).lower()]

    return {"count": len(markets), "markets": markets}

@app.get("/odds/series", dependencies=[Depends(require_service_key)])
def odds_series(
    series_ticker: str = Query(..., description="Kalshi series ticker, for example KXSWENCOUNTERS"),
    status: Optional[str] = Query(None, description="Filter by market status"),
    limit: int = Query(300, ge=1, le=1000)
):
    base_params: Dict[str, Any] = {"series_ticker": series_ticker}
    if status:
        base_params["status"] = status

    markets = _paged_markets(base_params, limit)
    return {"count": len(markets), "markets": markets}

@app.get("/odds/orderbook", dependencies=[Depends(require_service_key)])
def odds_orderbook(ticker: str = Query(..., description="Exact market ticker")):
    return _authed_get(f"/markets/{ticker}/orderbook")
