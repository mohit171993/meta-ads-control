import base64
import hmac
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
import requests

app = Flask(__name__)

GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v26.0")
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"
WINDSOR_KEY_FILE = Path("/data/windsor_api_key")


# ---------- auth / config ----------

def _tokens():
    values = []
    primary = os.getenv("META_ACCESS_TOKEN", "").strip()
    if primary:
        values.append(primary)

    extra = os.getenv("META_ACCESS_TOKENS", "").strip()
    if extra:
        try:
            parsed = json.loads(extra)
            if isinstance(parsed, list):
                values.extend(str(x).strip() for x in parsed if str(x).strip())
            elif isinstance(parsed, dict):
                values.extend(str(x).strip() for x in parsed.values() if str(x).strip())
        except Exception:
            values.extend(x.strip() for x in extra.replace("\n", ",").split(",") if x.strip())

    # de-duplicate without exposing values
    out = []
    seen = set()
    for t in values:
        if t not in seen:
            out.append(t)
            seen.add(t)
    return out


def _token():
    ts = _tokens()
    if not ts:
        raise RuntimeError("META_ACCESS_TOKEN is not configured")
    return ts[0]


def _account_id():
    value = os.getenv("META_AD_ACCOUNT_ID", "").strip()
    return value.removeprefix("act_") if value else ""


def _session_serializer():
    secret = (
        os.getenv("WINDSOR_SESSION_SECRET", "").strip()
        or os.getenv("DASHBOARD_PASSWORD", "").strip()
    )
    if not secret:
        return None
    return URLSafeTimedSerializer(secret_key=secret, salt="windsor-dashboard-session")


def _windsor_session_valid():
    token = request.cookies.get("windsor_session", "")
    serializer = _session_serializer()
    if not token or not serializer:
        return False
    try:
        payload = serializer.loads(token, max_age=60 * 60 * 24 * 7)
    except (BadSignature, SignatureExpired):
        return False
    expected_user = os.getenv("DASHBOARD_USER", "admin")
    return hmac.compare_digest(str(payload.get("u", "")), expected_user)


def _dashboard_authorized():
    if _windsor_session_valid():
        return True

    user = os.getenv("DASHBOARD_USER", "admin")
    password = os.getenv("DASHBOARD_PASSWORD", "")
    if not password:
        return True
    auth = request.authorization
    if not auth:
        return False
    return hmac.compare_digest(auth.username or "", user) and hmac.compare_digest(auth.password or "", password)


def _require_dashboard_auth():
    if _dashboard_authorized():
        return None
    return Response(
        "Authentication required",
        401,
        {"WWW-Authenticate": 'Basic realm="Meta Ads Dashboard"'},
    )


@app.route("/windsor-login", methods=["GET", "POST"])
def windsor_login():
    if _windsor_session_valid():
        return redirect("/dashboard")

    error = ""
    username = request.form.get("username", "admin")
    if request.method == "POST":
        expected_user = os.getenv("DASHBOARD_USER", "admin")
        expected_password = os.getenv("DASHBOARD_PASSWORD", "")
        supplied_user = request.form.get("username", "")
        supplied_password = request.form.get("password", "")

        if (
            (not expected_password)
            or (
                hmac.compare_digest(supplied_user, expected_user)
                and hmac.compare_digest(supplied_password, expected_password)
            )
        ):
            serializer = _session_serializer()
            response = redirect("/dashboard")
            if serializer:
                token = serializer.dumps({"u": expected_user})
                response.set_cookie(
                    "windsor_session",
                    token,
                    max_age=60 * 60 * 24 * 7,
                    secure=True,
                    httponly=True,
                    samesite="Lax",
                    path="/",
                )
            return response

        error = "Incorrect username or password."
        username = supplied_user or "admin"

    return render_template("windsor_login.html", error=error, username=username)


@app.get("/windsor-logout")
def windsor_logout():
    response = redirect("/windsor-login")
    response.delete_cookie("windsor_session", path="/")
    return response


@app.before_request
def _protect_dashboard():
    if request.path == "/dashboard" or request.path.startswith("/api/") or request.path == "/windsor-data-setup":
        return _require_dashboard_auth()
    return None


# ---------- Meta helpers ----------

def _safe_limit(raw):
    try:
        return max(1, min(int(raw or 25), 100))
    except Exception:
        return 25


def _meta_get_with_token(token, path, params=None, timeout=25):
    p = dict(params or {})
    p["access_token"] = token
    r = requests.get(f"{GRAPH_BASE}/{path.lstrip('/')}", params=p, timeout=timeout)
    try:
        payload = r.json()
    except Exception:
        payload = {"raw": r.text[:1000]}
    if not r.ok:
        return None, {"status": r.status_code, "meta": payload}
    return payload, None


def _meta_get(path, params):
    return _meta_get_with_token(_token(), path, params)


def _paginate(token, path, params=None, max_pages=25):
    rows = []
    payload, err = _meta_get_with_token(token, path, params or {})
    if err:
        return [], err

    pages = 0
    while payload and pages < max_pages:
        rows.extend(payload.get("data", []) or [])
        next_url = ((payload.get("paging") or {}).get("next"))
        if not next_url:
            break
        try:
            r = requests.get(next_url, timeout=25)
            payload = r.json()
            if not r.ok:
                return rows, {"status": r.status_code, "meta": payload}
        except Exception as exc:
            return rows, {"status": 500, "meta": {"message": str(exc)}}
        pages += 1
    return rows, None


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


def search_location(query, limit=25):
    params = {
        "type": "adgeolocation",
        "q": query,
        "limit": limit,
        "location_types": '["region"]'
    }
    payload, err = _meta_get("search", params)
    if err:
        return [], err
    rows = []
    for item in payload.get("data", []) or []:
        if not isinstance(item, dict):
            continue
        row = {k: item.get(k) for k in ("key","name","type","country_code","region","region_id","supports_region") if k in item}
        if row.get("type") == "region":
            rows.append(row)
    return rows, None


def _discover_accounts():
    found = {}
    errors = []
    for idx, token in enumerate(_tokens()):
        rows, err = _paginate(
            token,
            "me/adaccounts",
            {
                "fields": "id,name,account_status,currency,timezone_name,business_name",
                "limit": 200,
            },
            max_pages=10,
        )
        if err:
            errors.append({"token_index": idx, "error": err})
            continue
        for row in rows:
            account_id = str(row.get("id", "")).removeprefix("act_")
            if not account_id:
                continue
            if account_id not in found:
                found[account_id] = {
                    "id": account_id,
                    "name": row.get("name") or row.get("business_name") or f"Ad Account {account_id}",
                    "currency": row.get("currency") or "",
                    "timezone": row.get("timezone_name") or "",
                    "account_status": row.get("account_status"),
                    "_token": token,
                }

    # Keep configured account visible even if /me/adaccounts is temporarily incomplete.
    configured = _account_id()
    if configured and configured not in found and _tokens():
        found[configured] = {
            "id": configured,
            "name": f"Ad Account {configured}",
            "currency": "",
            "timezone": "",
            "account_status": None,
            "_token": _tokens()[0],
        }
    return list(found.values()), errors


def _date_range():
    preset = (request.args.get("range") or "today").lower()
    today = date.today()
    if preset == "yesterday":
        since = until = today - timedelta(days=1)
    elif preset in ("7d", "last7"):
        since, until = today - timedelta(days=6), today
    elif preset in ("30d", "last30"):
        since, until = today - timedelta(days=29), today
    elif preset == "custom":
        try:
            since = date.fromisoformat(request.args.get("since", ""))
            until = date.fromisoformat(request.args.get("until", ""))
        except Exception:
            since, until = today, today
        if until < since:
            since, until = until, since
        if (until - since).days > 92:
            since = until - timedelta(days=92)
    else:
        preset = "today"
        since = until = today
    return preset, since.isoformat(), until.isoformat()


def _action_map(actions):
    out = {}
    for item in actions or []:
        t = item.get("action_type")
        if not t:
            continue
        try:
            out[t] = float(item.get("value") or 0)
        except Exception:
            out[t] = 0
    return out


def _first_action(amap, names):
    for name in names:
        if name in amap:
            return amap.get(name, 0) or 0
    return 0


def _money(v):
    try:
        return round(float(v or 0), 2)
    except Exception:
        return 0.0


def _minor_to_major(v, currency):
    try:
        n = float(v or 0)
    except Exception:
        return 0.0
    decimals = 0 if currency in {"JPY", "KRW", "VND"} else 2
    return round(n / (10 ** decimals), decimals)


def _primary_result(row):
    choices = [
        ("Purchases", row.get("purchases", 0)),
        ("Registrations", row.get("registrations", 0)),
        ("Leads", row.get("leads", 0)),
        ("Landing Page Views", row.get("landing_page_views", 0)),
        ("Link Clicks", row.get("link_clicks", 0)),
        ("Clicks", row.get("clicks", 0)),
    ]
    for label, value in choices:
        if value and value > 0:
            return label, value
    return "—", 0


def _fetch_account_report(account, since, until):
    token = account["_token"]
    aid = account["id"]
    currency = account.get("currency") or ""

    ads, ads_err = _paginate(
        token,
        f"act_{aid}/ads",
        {
            "fields": "id,name,status,effective_status,adset_id,campaign_id",
            "limit": 500,
        },
    )
    if ads_err:
        return {"account": account, "error": ads_err, "ads": []}

    insights, insights_err = _paginate(
        token,
        f"act_{aid}/insights",
        {
            "level": "ad",
            "fields": "ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,spend,impressions,reach,clicks,ctr,cpc,cpm,frequency,actions",
            "time_range": json.dumps({"since": since, "until": until}),
            "limit": 500,
        },
    )

    adsets, _ = _paginate(
        token,
        f"act_{aid}/adsets",
        {
            "fields": "id,name,status,effective_status,daily_budget,lifetime_budget,campaign_id",
            "limit": 500,
        },
    )
    campaigns, _ = _paginate(
        token,
        f"act_{aid}/campaigns",
        {
            "fields": "id,name,status,effective_status,daily_budget,lifetime_budget",
            "limit": 500,
        },
    )

    insight_by_ad = {str(x.get("ad_id")): x for x in insights or [] if x.get("ad_id")}
    adset_by_id = {str(x.get("id")): x for x in adsets or [] if x.get("id")}
    campaign_by_id = {str(x.get("id")): x for x in campaigns or [] if x.get("id")}

    rows = []
    for ad in ads:
        ad_id = str(ad.get("id"))
        ins = insight_by_ad.get(ad_id, {})
        amap = _action_map(ins.get("actions"))

        link_clicks = _first_action(amap, ["link_click"])
        landing = _first_action(amap, ["landing_page_view"])
        leads = _first_action(amap, ["offsite_conversion.fb_pixel_lead", "lead", "leadgen_grouped"])
        registrations = _first_action(
            amap,
            [
                "offsite_conversion.fb_pixel_complete_registration",
                "complete_registration",
                "omni_complete_registration",
            ],
        )
        purchases = _first_action(
            amap,
            [
                "offsite_conversion.fb_pixel_purchase",
                "purchase",
                "omni_purchase",
            ],
        )

        adset_id = str(ad.get("adset_id") or ins.get("adset_id") or "")
        campaign_id = str(ad.get("campaign_id") or ins.get("campaign_id") or "")
        aset = adset_by_id.get(adset_id, {})
        camp = campaign_by_id.get(campaign_id, {})

        budget_kind = ""
        budget_value = 0
        if aset.get("daily_budget"):
            budget_kind = "Ad set daily"
            budget_value = _minor_to_major(aset.get("daily_budget"), currency)
        elif aset.get("lifetime_budget"):
            budget_kind = "Ad set lifetime"
            budget_value = _minor_to_major(aset.get("lifetime_budget"), currency)
        elif camp.get("daily_budget"):
            budget_kind = "Campaign daily"
            budget_value = _minor_to_major(camp.get("daily_budget"), currency)
        elif camp.get("lifetime_budget"):
            budget_kind = "Campaign lifetime"
            budget_value = _minor_to_major(camp.get("lifetime_budget"), currency)

        row = {
            "account_id": aid,
            "account_name": account.get("name"),
            "currency": currency,
            "campaign_id": campaign_id,
            "campaign_name": ins.get("campaign_name") or camp.get("name") or "",
            "campaign_status": camp.get("effective_status") or camp.get("status") or "",
            "adset_id": adset_id,
            "adset_name": ins.get("adset_name") or aset.get("name") or "",
            "adset_status": aset.get("effective_status") or aset.get("status") or "",
            "ad_id": ad_id,
            "ad_name": ad.get("name") or ins.get("ad_name") or "",
            "status": ad.get("status") or "",
            "effective_status": ad.get("effective_status") or ad.get("status") or "",
            "spend": _money(ins.get("spend")),
            "impressions": int(float(ins.get("impressions") or 0)),
            "reach": int(float(ins.get("reach") or 0)),
            "clicks": int(float(ins.get("clicks") or 0)),
            "ctr": round(float(ins.get("ctr") or 0), 2),
            "cpc": _money(ins.get("cpc")),
            "cpm": _money(ins.get("cpm")),
            "frequency": round(float(ins.get("frequency") or 0), 2),
            "link_clicks": int(link_clicks),
            "landing_page_views": int(landing),
            "leads": int(leads),
            "registrations": int(registrations),
            "purchases": int(purchases),
            "budget_kind": budget_kind,
            "budget_value": budget_value,
        }
        label, value = _primary_result(row)
        row["result_type"] = label
        row["results"] = int(value)
        row["cost_per_result"] = round(row["spend"] / value, 2) if value else None
        rows.append(row)

    # Include insight-only rows if Meta omitted an old/archived ad from /ads.
    existing = {x["ad_id"] for x in rows}
    for ad_id, ins in insight_by_ad.items():
        if ad_id in existing:
            continue
        amap = _action_map(ins.get("actions"))
        row = {
            "account_id": aid,
            "account_name": account.get("name"),
            "currency": currency,
            "campaign_id": str(ins.get("campaign_id") or ""),
            "campaign_name": ins.get("campaign_name") or "",
            "campaign_status": "",
            "adset_id": str(ins.get("adset_id") or ""),
            "adset_name": ins.get("adset_name") or "",
            "adset_status": "",
            "ad_id": ad_id,
            "ad_name": ins.get("ad_name") or "",
            "status": "",
            "effective_status": "HISTORICAL",
            "spend": _money(ins.get("spend")),
            "impressions": int(float(ins.get("impressions") or 0)),
            "reach": int(float(ins.get("reach") or 0)),
            "clicks": int(float(ins.get("clicks") or 0)),
            "ctr": round(float(ins.get("ctr") or 0), 2),
            "cpc": _money(ins.get("cpc")),
            "cpm": _money(ins.get("cpm")),
            "frequency": round(float(ins.get("frequency") or 0), 2),
            "link_clicks": int(_first_action(amap, ["link_click"])),
            "landing_page_views": int(_first_action(amap, ["landing_page_view"])),
            "leads": int(_first_action(amap, ["offsite_conversion.fb_pixel_lead", "lead", "leadgen_grouped"])),
            "registrations": int(_first_action(amap, ["offsite_conversion.fb_pixel_complete_registration", "complete_registration", "omni_complete_registration"])),
            "purchases": int(_first_action(amap, ["offsite_conversion.fb_pixel_purchase", "purchase", "omni_purchase"])),
            "budget_kind": "",
            "budget_value": 0,
        }
        label, value = _primary_result(row)
        row["result_type"] = label
        row["results"] = int(value)
        row["cost_per_result"] = round(row["spend"] / value, 2) if value else None
        rows.append(row)

    total_spend = round(sum(x["spend"] for x in rows), 2)
    total_impressions = sum(x["impressions"] for x in rows)
    total_reach = sum(x["reach"] for x in rows)
    total_clicks = sum(x["clicks"] for x in rows)
    total_results = sum(x["results"] for x in rows)
    summary = {
        "spend": total_spend,
        "impressions": total_impressions,
        "reach": total_reach,
        "clicks": total_clicks,
        "ctr": round((total_clicks / total_impressions * 100), 2) if total_impressions else 0,
        "cpc": round(total_spend / total_clicks, 2) if total_clicks else 0,
        "results": total_results,
        "cost_per_result": round(total_spend / total_results, 2) if total_results else None,
        "active_ads": sum(1 for x in rows if x["effective_status"] == "ACTIVE"),
        "review_ads": sum(1 for x in rows if x["effective_status"] in {"IN_PROCESS", "PENDING_REVIEW", "WITH_ISSUES"}),
        "disapproved_ads": sum(1 for x in rows if x["effective_status"] == "DISAPPROVED"),
    }
    return {
        "account": {k: v for k, v in account.items() if not k.startswith("_")},
        "summary": summary,
        "ads": rows,
        "error": insights_err,
    }



# ---------- Windsor.ai fallback ----------

WINDSOR_CONNECTOR_URL = "https://connectors.windsor.ai/facebook"
WINDSOR_FIELDS = ",".join([
    "account_id","account_name","account_status","currency","account_timezone",
    "campaign_id","campaign","campaign_effective_status",
    "adset_id","adset_name","adset_effective_status",
    "ad_id","ad_name","status","effective_status",
    "spend","impressions","reach","clicks","ctr","cpc","cpm","frequency",
    "actions_link_click","actions_landing_page_view","actions_leadgen_grouped",
    "actions_complete_registration",
    "actions_offsite_conversion_fb_pixel_complete_registration",
    "actions_omni_complete_registration",
    "actions_purchase","actions_offsite_conversion_fb_pixel_purchase",
    "actions_omni_purchase","date"
])


def _windsor_key():
    env_key = (os.getenv("WINDSOR_API_KEY") or "").strip()
    if env_key:
        return env_key
    try:
        if WINDSOR_KEY_FILE.exists():
            return WINDSOR_KEY_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def _save_windsor_key(value):
    key = (value or "").strip()
    if not key:
        raise ValueError("Windsor API key is required")
    WINDSOR_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    WINDSOR_KEY_FILE.write_text(key, encoding="utf-8")
    try:
        os.chmod(WINDSOR_KEY_FILE, 0o600)
    except Exception:
        pass


def _windsor_account_status_code(value):
    text = str(value or "").upper()
    mapping = {
        "ACTIVE": 1,
        "DISABLED": 2,
        "UNSETTLED": 3,
        "PENDING_RISK_REVIEW": 7,
        "PENDING_SETTLEMENT": 8,
        "IN_GRACE_PERIOD": 9,
        "PENDING_CLOSURE": 100,
        "CLOSED": 101,
    }
    return mapping.get(text, value)


def _windsor_rows(api_key, fields, date_from=None, date_to=None, select_accounts=None, include_objects_without_insights=False):
    if not api_key:
        return [], {"message": "Windsor API key is not configured on this device."}

    params = {
        "api_key": api_key,
        "fields": fields,
        "_max_rows": 5000,
    }
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    if select_accounts and select_accounts != "all":
        params["select_accounts"] = str(select_accounts).removeprefix("act_")
    if include_objects_without_insights:
        params["options"] = json.dumps({
            "facebook": {"include_objects_without_insights": "true"}
        })

    try:
        response = requests.get(
            WINDSOR_CONNECTOR_URL,
            params=params,
            headers={"User-Agent": "Windsor-Dashboard/1.1"},
            timeout=45,
        )
        try:
            payload = response.json()
        except Exception:
            return [], {
                "status": response.status_code,
                "message": "Windsor returned a non-JSON response.",
            }

        if not response.ok:
            message = payload.get("error") if isinstance(payload, dict) else None
            return [], {
                "status": response.status_code,
                "message": str(message or "Windsor API request failed.")[:300],
            }

        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            if payload.get("error"):
                return [], {"message": str(payload.get("error"))[:300]}
            rows = payload.get("data")
            if rows is None:
                rows = payload.get("result")
            if rows is None:
                rows = payload.get("rows")
            rows = rows or []
        else:
            rows = []

        return [x for x in rows if isinstance(x, dict)], None
    except requests.RequestException as exc:
        return [], {"message": f"Windsor API connection failed: {str(exc)[:200]}"}


def _to_float(value):
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _to_int(value):
    try:
        return int(round(float(value or 0)))
    except Exception:
        return 0


def _first_nonzero(row, names):
    for name in names:
        value = _to_float(row.get(name))
        if value:
            return value
    return 0.0


def _windsor_accounts(api_key):
    fields = "account_id,account_name,account_status,currency,account_timezone"
    rows, err = _windsor_rows(api_key, fields)
    if err:
        return [], [err]

    found = {}
    for row in rows:
        aid = str(row.get("account_id") or "").removeprefix("act_")
        if not aid:
            continue
        found[aid] = {
            "id": aid,
            "name": row.get("account_name") or f"Ad Account {aid}",
            "currency": row.get("currency") or "",
            "timezone": row.get("account_timezone") or "",
            "account_status": _windsor_account_status_code(row.get("account_status") or "UNKNOWN"),
        }
    return list(found.values()), []


def _windsor_report(api_key, since, until, selected="all"):
    raw_rows, err = _windsor_rows(
        api_key,
        WINDSOR_FIELDS,
        date_from=since,
        date_to=until,
        select_accounts=selected,
        include_objects_without_insights=True,
    )
    if err:
        return {
            "overview": {
                "spend": 0, "currency": "", "impressions": 0, "reach": 0,
                "clicks": 0, "ctr": 0, "cpc": None, "results": 0,
                "cost_per_result": None, "active_ads": 0, "review_ads": 0,
                "disapproved_ads": 0,
            },
            "accounts": [],
            "ads": [],
            "errors": [err],
        }

    # Reporting rows are date-filtered and can carry a stale object status.
    # Fetch current object status separately (without a reporting date range)
    # and overlay it after aggregating performance metrics.
    live_rows, live_err = _windsor_rows(
        api_key,
        "account_id,ad_id,status,effective_status,campaign_effective_status,adset_effective_status",
        select_accounts=selected,
        include_objects_without_insights=True,
    )
    live_status = {}
    if not live_err:
        for item in live_rows:
            aid = str(item.get("account_id") or "").removeprefix("act_")
            ad_id = str(item.get("ad_id") or "")
            if not aid or not ad_id:
                continue
            live_status[(aid, ad_id)] = {
                "status": item.get("status") or "",
                "effective_status": item.get("effective_status") or item.get("status") or "",
                "campaign_status": item.get("campaign_effective_status") or "",
                "adset_status": item.get("adset_effective_status") or "",
            }

    # Windsor returns one row per ad/date for multi-day ranges. Aggregate by ad
    # so ad counts and totals are not multiplied by the number of days.
    grouped = {}
    account_meta = {}

    for item in raw_rows:
        aid = str(item.get("account_id") or "").removeprefix("act_")
        ad_id = str(item.get("ad_id") or "")
        if not aid:
            continue
        if not ad_id:
            ad_id = "|".join([
                str(item.get("campaign_id") or ""),
                str(item.get("adset_id") or ""),
                str(item.get("ad_name") or ""),
            ])

        account_meta[aid] = {
            "id": aid,
            "name": item.get("account_name") or f"Ad Account {aid}",
            "currency": item.get("currency") or "",
            "timezone": item.get("account_timezone") or "",
            "account_status": _windsor_account_status_code(item.get("account_status") or "UNKNOWN"),
        }

        key = (aid, ad_id)
        if key not in grouped:
            grouped[key] = {
                "account_id": aid,
                "account_name": account_meta[aid]["name"],
                "currency": account_meta[aid]["currency"],
                "campaign_id": str(item.get("campaign_id") or ""),
                "campaign_name": item.get("campaign") or (f"Campaign {item.get('campaign_id')}" if item.get("campaign_id") else ""),
                "campaign_status": item.get("campaign_effective_status") or "",
                "adset_id": str(item.get("adset_id") or ""),
                "adset_name": item.get("adset_name") or (f"Ad set {item.get('adset_id')}" if item.get("adset_id") else ""),
                "adset_status": item.get("adset_effective_status") or "",
                "ad_id": ad_id,
                "ad_name": item.get("ad_name") or "",
                "status": item.get("status") or "",
                "effective_status": item.get("effective_status") or item.get("status") or "",
                "spend": 0.0,
                "impressions": 0,
                "reach": 0,
                "clicks": 0,
                "link_clicks": 0,
                "landing_page_views": 0,
                "leads": 0,
                "registrations": 0,
                "purchases": 0,
                "budget_kind": "",
                "budget_value": 0,
            }

        out = grouped[key]
        out["spend"] += _to_float(item.get("spend"))
        out["impressions"] += _to_int(item.get("impressions"))
        out["reach"] += _to_int(item.get("reach"))
        out["clicks"] += _to_int(item.get("clicks"))
        out["link_clicks"] += _to_int(item.get("actions_link_click"))
        out["landing_page_views"] += _to_int(item.get("actions_landing_page_view"))
        out["leads"] += _to_int(item.get("actions_leadgen_grouped"))
        out["registrations"] += _to_int(_first_nonzero(item, [
            "actions_offsite_conversion_fb_pixel_complete_registration",
            "actions_complete_registration",
            "actions_omni_complete_registration",
        ]))
        out["purchases"] += _to_int(_first_nonzero(item, [
            "actions_offsite_conversion_fb_pixel_purchase",
            "actions_purchase",
            "actions_omni_purchase",
        ]))
        out["status"] = item.get("status") or out["status"]
        out["effective_status"] = (
            item.get("effective_status") or item.get("status") or out["effective_status"]
        )
        out["campaign_status"] = item.get("campaign_effective_status") or out["campaign_status"]
        out["adset_status"] = item.get("adset_effective_status") or out["adset_status"]

    rows = []
    for out in grouped.values():
        current = live_status.get((out["account_id"], out["ad_id"]))
        if current:
            out["status"] = current["status"] or out["status"]
            out["effective_status"] = (
                current["effective_status"] or current["status"] or out["effective_status"]
            )
            out["campaign_status"] = current["campaign_status"] or out["campaign_status"]
            out["adset_status"] = current["adset_status"] or out["adset_status"]

        # Show the ad's explicit pause/archive state immediately. Otherwise use
        # effective status so inherited states such as ADSET_PAUSED remain visible.
        own_status = str(out.get("status") or "").upper()
        effective_status = str(out.get("effective_status") or own_status).upper()
        out["display_status"] = (
            own_status if own_status in {"PAUSED", "ARCHIVED"} else (effective_status or own_status)
        )

        out["spend"] = round(out["spend"], 2)
        out["ctr"] = round(
            (out["clicks"] / out["impressions"] * 100) if out["impressions"] else 0,
            2,
        )
        out["cpc"] = round(out["spend"] / out["clicks"], 2) if out["clicks"] else 0
        out["cpm"] = round(out["spend"] / out["impressions"] * 1000, 2) if out["impressions"] else 0
        out["frequency"] = round(out["impressions"] / out["reach"], 2) if out["reach"] else 0
        label, value = _primary_result(out)
        out["result_type"] = label
        out["results"] = int(value)
        out["cost_per_result"] = round(out["spend"] / value, 2) if value else None
        rows.append(out)

    by_account = {}
    for row in rows:
        aid = row["account_id"]
        if aid not in by_account:
            by_account[aid] = {
                "spend": 0.0, "impressions": 0, "reach": 0, "clicks": 0,
                "results": 0, "active_ads": 0, "review_ads": 0,
                "disapproved_ads": 0,
            }
        s = by_account[aid]
        s["spend"] += row["spend"]
        s["impressions"] += row["impressions"]
        s["reach"] += row["reach"]
        s["clicks"] += row["clicks"]
        s["results"] += row["results"]
        state = str(row.get("effective_status") or "").upper()
        if state == "ACTIVE":
            s["active_ads"] += 1
        if state in {"IN_PROCESS", "PENDING_REVIEW", "WITH_ISSUES"}:
            s["review_ads"] += 1
        if state == "DISAPPROVED":
            s["disapproved_ads"] += 1

    account_summaries = []
    currencies = set()
    for aid, summary in by_account.items():
        summary["spend"] = round(summary["spend"], 2)
        summary["ctr"] = round(
            summary["clicks"] / summary["impressions"] * 100, 2
        ) if summary["impressions"] else 0
        summary["cpc"] = round(
            summary["spend"] / summary["clicks"], 2
        ) if summary["clicks"] else 0
        summary["cost_per_result"] = round(
            summary["spend"] / summary["results"], 2
        ) if summary["results"] else None
        meta = account_meta.get(aid, {"id": aid, "name": aid, "currency": ""})
        if meta.get("currency"):
            currencies.add(meta["currency"])
        account_summaries.append({"account": meta, "summary": summary, "error": None})

    currencies.discard("")
    same_currency = len(currencies) <= 1
    total_spend = round(sum(x["spend"] for x in by_account.values()), 2)
    total_impressions = sum(x["impressions"] for x in by_account.values())
    total_reach = sum(x["reach"] for x in by_account.values())
    total_clicks = sum(x["clicks"] for x in by_account.values())
    total_results = sum(x["results"] for x in by_account.values())

    overview = {
        "spend": total_spend if same_currency else None,
        "currency": next(iter(currencies), "") if same_currency else "MULTI",
        "impressions": total_impressions,
        "reach": total_reach,
        "clicks": total_clicks,
        "ctr": round(total_clicks / total_impressions * 100, 2) if total_impressions else 0,
        "cpc": round(total_spend / total_clicks, 2) if total_clicks and same_currency else None,
        "results": total_results,
        "cost_per_result": round(total_spend / total_results, 2) if total_results and same_currency else None,
        "active_ads": sum(x["active_ads"] for x in by_account.values()),
        "review_ads": sum(x["review_ads"] for x in by_account.values()),
        "disapproved_ads": sum(x["disapproved_ads"] for x in by_account.values()),
    }

    return {
        "overview": overview,
        "accounts": account_summaries,
        "ads": sorted(rows, key=lambda x: (x.get("spend", 0), x.get("impressions", 0)), reverse=True),
        "errors": [live_err] if live_err else [],
    }


@app.route("/windsor-data-setup", methods=["GET", "POST"])
def windsor_data_setup():
    message = ""
    error = ""
    configured = bool(_windsor_key())

    if request.method == "POST":
        key = request.form.get("api_key", "")
        try:
            # Validate before persisting so a typo cannot break the dashboard.
            rows, test_error = _windsor_rows(
                key,
                "account_id,account_name,account_status,currency",
            )
            if test_error:
                error = test_error.get("message") or "Windsor API key validation failed."
            elif not rows:
                error = "Windsor connected, but no Facebook Ads accounts were returned."
            else:
                _save_windsor_key(key)
                configured = True
                message = f"Windsor connected successfully. {len(rows)} row(s) returned in validation."
        except Exception as exc:
            error = str(exc)

    return render_template(
        "windsor_setup.html",
        configured=configured,
        message=message,
        error=error,
    )


# ---------- dashboard ----------

@app.get("/")
def root():
    return redirect("/dashboard")


@app.get("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.get("/api/accounts")
def api_accounts():
    windsor_key = _windsor_key()
    if windsor_key:
        accounts, errors = _windsor_accounts(windsor_key)
        return jsonify({
            "source": "windsor",
            "accounts": accounts,
            "errors": errors,
            "needs_windsor_key": False,
        })

    accounts, errors = _discover_accounts()
    clean = [{k: v for k, v in a.items() if not k.startswith("_")} for a in accounts]
    return jsonify({
        "source": "meta_direct",
        "accounts": clean,
        "errors": errors,
        "needs_windsor_key": bool(errors and not clean),
    })


@app.get("/api/report")
def api_report():
    preset, since, until = _date_range()
    selected = (request.args.get("account") or "all").removeprefix("act_")

    windsor_key = _windsor_key()
    if windsor_key:
        result = _windsor_report(windsor_key, since, until, selected)
        result.update({
            "source": "windsor",
            "range": preset,
            "since": since,
            "until": until,
            "needs_windsor_key": False,
        })
        return jsonify(result)

    accounts, discovery_errors = _discover_accounts()
    if selected != "all":
        accounts = [a for a in accounts if a["id"] == selected]

    reports = []
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(accounts)))) as pool:
        futures = [pool.submit(_fetch_account_report, a, since, until) for a in accounts]
        for future in as_completed(futures):
            try:
                reports.append(future.result())
            except Exception as exc:
                reports.append({"error": {"message": str(exc)}, "ads": []})

    all_ads = []
    account_summaries = []
    currencies = set()
    for report in reports:
        if report.get("account"):
            currencies.add(report["account"].get("currency") or "")
        all_ads.extend(report.get("ads") or [])
        if report.get("account") and report.get("summary"):
            account_summaries.append({
                "account": report["account"],
                "summary": report["summary"],
                "error": report.get("error"),
            })

    currencies.discard("")
    same_currency = len(currencies) <= 1
    spend = round(sum((x.get("summary") or {}).get("spend", 0) for x in reports), 2)
    impressions = sum((x.get("summary") or {}).get("impressions", 0) for x in reports)
    reach = sum((x.get("summary") or {}).get("reach", 0) for x in reports)
    clicks = sum((x.get("summary") or {}).get("clicks", 0) for x in reports)
    results = sum((x.get("summary") or {}).get("results", 0) for x in reports)

    overview = {
        "spend": spend if same_currency else None,
        "currency": next(iter(currencies), "") if same_currency else "MULTI",
        "impressions": impressions,
        "reach": reach,
        "clicks": clicks,
        "ctr": round(clicks / impressions * 100, 2) if impressions else 0,
        "cpc": round(spend / clicks, 2) if clicks and same_currency else None,
        "results": results,
        "cost_per_result": round(spend / results, 2) if results and same_currency else None,
        "active_ads": sum((x.get("summary") or {}).get("active_ads", 0) for x in reports),
        "review_ads": sum((x.get("summary") or {}).get("review_ads", 0) for x in reports),
        "disapproved_ads": sum((x.get("summary") or {}).get("disapproved_ads", 0) for x in reports),
    }

    all_errors = discovery_errors + [r.get("error") for r in reports if r.get("error")]
    return jsonify({
        "source": "meta_direct",
        "range": preset,
        "since": since,
        "until": until,
        "overview": overview,
        "accounts": account_summaries,
        "ads": sorted(all_ads, key=lambda x: (x.get("spend", 0), x.get("impressions", 0)), reverse=True),
        "errors": all_errors,
        "needs_windsor_key": bool(all_errors and not all_ads),
    })


# ---------- existing control endpoints ----------



@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "graph_version": GRAPH_VERSION,
        "token_count": len(_tokens()),
        "token_configured": bool(_tokens()),
        "ad_account_configured": bool(_account_id()),
        "dashboard_enabled": bool(os.getenv("DASHBOARD_PASSWORD")),
        "windsor_configured": bool(_windsor_key()),
    })


@app.get("/locations")
def locations():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"error": "q is required"}), 400
    try:
        items, err = search_location(q, _safe_limit(request.args.get("limit")))
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    if err:
        return jsonify({"error": "Meta location lookup failed", "detail": err}), 502
    return jsonify({"query": q, "count": len(items), "results": items})


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




if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
