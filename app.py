import json
import os
from flask import Flask, jsonify, request
import requests

app = Flask(__name__)

GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v26.0")
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"

def _token():
    token = os.getenv("META_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("META_ACCESS_TOKEN is not configured")
    return token

def _account_id():
    value = os.getenv("META_AD_ACCOUNT_ID", "").strip()
    return value.removeprefix("act_") if value else ""

def _safe_limit(raw):
    try:
        return max(1, min(int(raw or 25), 100))
    except Exception:
        return 25

def _meta_get(path, params):
    p = dict(params)
    p["access_token"] = _token()
    r = requests.get(f"{GRAPH_BASE}/{path.lstrip('/')}", params=p, timeout=20)
    payload = r.json() if "application/json" in r.headers.get("content-type", "") else {"raw": r.text}
    if not r.ok:
        return None, {"status": r.status_code, "meta": payload}
    return payload, None

def _normalize(items):
    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        row = {"id": item.get("id"), "name": item.get("name")}
        for key in ("audience_size", "audience_size_lower_bound", "audience_size_upper_bound", "path", "description", "topic"):
            if key in item:
                row[key] = item.get(key)
        if row["id"] and row["name"]:
            out.append(row)
    return out

def search_interest(query, limit=25):
    account = _account_id()
    attempts = []
    if account:
        attempts.append((f"act_{account}/targetingsearch", {"type": "adinterest", "q": query, "limit": limit}))
    attempts.append(("search", {"type": "adinterest", "q": query, "limit": limit}))

    last_error = None
    for path, params in attempts:
        payload, err = _meta_get(path, params)
        if not err:
            return _normalize(payload.get("data", [])), None
        last_error = err
    return [], last_error

@app.get("/")
def root():
    return jsonify({
        "service": "meta-ads-control",
        "mode": "interest-resolver",
        "graph_version": GRAPH_VERSION,
        "configured": bool(os.getenv("META_ACCESS_TOKEN")),
        "endpoints": ["/health", "/interests?q=cricket", "/interests/batch?q=cricket,sports%20betting"]
    })

@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "graph_version": GRAPH_VERSION,
        "token_configured": bool(os.getenv("META_ACCESS_TOKEN")),
        "ad_account_configured": bool(_account_id())
    })

@app.get("/interests")
def interests():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"error": "q is required"}), 400
    try:
        items, err = search_interest(q, _safe_limit(request.args.get("limit")))
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    if err:
        return jsonify({"error": "Meta interest lookup failed", "detail": err}), 502
    return jsonify({"query": q, "count": len(items), "results": items})

@app.get("/interests/batch")
def interests_batch():
    raw = (request.args.get("q") or "").strip()
    queries = [x.strip() for x in raw.split(",") if x.strip()]
    if not queries:
        return jsonify({"error": "q must contain one or more comma-separated queries"}), 400
    queries = queries[:20]
    limit = _safe_limit(request.args.get("limit"))
    result = {}
    try:
        for q in queries:
            items, err = search_interest(q, limit)
            result[q] = {"results": items, "error": err}
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    return jsonify({"queries": queries, "results": result})

def _startup_selftest():
    if os.getenv("META_STARTUP_SELFTEST", "0") != "1":
        return
    queries = ["cricket", "sports betting", "sportsbook", "fantasy cricket", "online gambling"]
    report = {"graph_version": GRAPH_VERSION, "account": _account_id(), "queries": {}}
    try:
        for q in queries:
            items, err = search_interest(q, 12)
            report["queries"][q] = {
                "results": [{"id": x.get("id"), "name": x.get("name")} for x in items],
                "error": err,
            }
    except Exception as exc:
        report["fatal_error"] = str(exc)
    app.logger.warning("META_SELFTEST %s", json.dumps(report, separators=(",", ":")))

_startup_selftest()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
