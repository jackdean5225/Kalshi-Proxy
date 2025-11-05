import os, base64, datetime, requests
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding

BASE = "https://api.elections.kalshi.com/trade-api/v2"
API_PREFIX = "/trade-api/v2"

ACCESS_KEY = os.environ["KALSHI_ACCESS_KEY"]
PRIVATE_PEM = os.environ["KALSHI_PRIVATE_KEY_PEM"].replace("\\n", "\n").encode("utf-8")
SERVICE_API_KEY = os.environ["SERVICE_API_KEY"]

_private_key = serialization.load_pem_private_key(PRIVATE_PEM, password=None)

def _sign(ts_ms: str, method: str, short_path: str) -> str:
    msg = f"{ts_ms}{method.upper()}{API_PREFIX}{short_path}".encode()
    sig = _private_key.sign(
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(sig).decode()

def _authed_get(short_path: str, params: Dict[str, Any] = None):
    ts = str(int(datetime.datetime.now().timestamp() * 1000))
    headers = {
        "KALSHI-ACCESS-KEY": ACCESS_KEY,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": _sign(ts, "GET", short_path),
    }
    r = requests.get(f"{BASE}{short_path}", params=params, headers=headers, timeout=20)
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()

app = FastAPI(title="Kalshi Odds Proxy", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)

def require_service_key(x_api_key: Optional[str] = Header(None)):
    if x_api_key != SERVICE_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/odds/search", dependencies=[require_service_key])
def odds_search(keyword: str, status: Optional[str] = None, limit: int = 300):
    params = {"limit": min(100, limit)}
    if status:
        params["status"] = status
    out, cursor = [], None
    while len(out) < limit:
        if cursor:
            params["cursor"] = cursor
        data = _authed_get("/markets", params)
        batch = data.get("markets", [])
        out += batch
        cursor = data.get("cursor")
        if not cursor or not batch:
            break
    kw = keyword.lower()
    hits = [m for m in out if kw in (m.get("title","") + " " + m.get("ticker","")).lower()]
    return {"count": len(hits), "markets": hits}

@app.get("/odds/orderbook", dependencies=[require_service_key])
def odds_orderbook(ticker: str):
    return _authed_get(f"/markets/{ticker}/orderbook")
