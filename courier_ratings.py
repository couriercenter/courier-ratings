# -*- coding: utf-8 -*-
"""
courier_ratings.py
Τρέχει αυτόματα (ή χειροκίνητα), φέρνει Google Places data για 6 brands,
χτίζει history.json και report.html, κάνει git push στο GitHub Pages.

Ρυθμίσεις: βλ. CONFIG παρακάτω.
"""

import os, time, json, re as _re, subprocess, unicodedata
from datetime import datetime, timezone
from functools import lru_cache
from typing import Dict, List, Any, Optional

import requests
import pandas as pd

# ─────────────────────────── CONFIG ───────────────────────────────────────
API_KEY     = os.environ.get("PLACES_API_KEY", "AIzaSyClyJnmfpRi7GJ8_6HXBBcvo2i4S-pycT4")
REPO_DIR    = os.path.dirname(os.path.abspath(__file__))   # same dir as this script
HISTORY_FILE = os.path.join(REPO_DIR, "history.json")
REPORT_FILE  = os.path.join(REPO_DIR, "index.html")

RATE_SLEEP   = 0.3
HTTP_TIMEOUT = 25
SEARCH_RADIUS_M = 50000

COMMIT_MSG   = "auto: update ratings {date}"
GIT_PUSH     = True   # False για dry-run χωρίς push

# ─────────────────────────── BRANDS & QUERIES ─────────────────────────────
BRANDS: Dict[str, List[str]] = {
    "ACS":                  ["Κατάστημα ACS"],
    "Γενική Ταχυδρομική":   ["Κατάστημα Γενική Ταχυδρομική"],
    "ΕΛΤΑ Courier":         ["Κατάστημα ΕΛΤΑ Courier", "Κατάστημα ELTA Courier"],
    "SPEEDEX":              ["Κατάστημα SPEEDEX"],
    "Courier Center":       ["Κατάστημα Courier Center"],
    "EASYMAIL":             ["Κατάστημα easymail"],
}

GREECE_CENTERS = [
    (37.9838,23.7275),(38.3250,23.3187),(38.4353,22.8764),(38.4371,22.4318),
    (38.9006,22.4338),(38.9182,22.6159),(39.0003,21.7931),(39.3623,22.9427),
    (39.1825,22.7596),(39.6390,22.4196),(39.8897,22.1870),(39.2926,22.3849),
    (39.5550,21.7679),(39.3656,21.9214),(40.2697,22.5061),(40.6401,22.9444),
    (40.5470,23.0213),(40.6757,22.8352),(40.6117,22.9780),(40.5897,22.9507),
    (40.6680,22.9301),(40.6902,22.9004),(40.7868,22.5807),(40.7481,23.0656),
    (40.2414,23.2843),(40.3809,23.4413),(40.2029,23.6645),(40.3953,23.8856),
    (40.5233,22.2033),(40.6293,22.0692),(40.9937,22.8743),(40.0833,21.4275),
    (40.3006,21.7896),(40.5143,21.6786),(40.5200,21.2687),(40.7880,22.4070),
    (40.7858,22.3148),(41.0903,23.5414),(41.1495,24.1474),(40.9396,24.4018),
    (41.1343,24.8877),(41.1169,25.4040),(40.8470,25.8744),(41.5048,26.5297),
    (38.2466,21.7346),(38.2305,21.7371),(38.2523,22.0819),(37.6753,21.4374),
    (37.9381,22.9320),(38.0146,22.7496),(37.5674,22.8069),(37.0738,22.4297),
    (37.5108,22.3735),(37.0387,22.1142),(37.7951,21.3507),(38.3911,21.8277),
    (38.3714,21.4315),(38.6218,21.4074),(39.1585,20.9877),(38.9559,20.7505),
    (35.3387,25.1442),(35.0510,25.7463),(35.1280,25.7308),(35.3655,24.4820),
    (35.4748,23.8044),(35.0514,25.0787),(39.6239,19.9217),(36.4349,28.2176),
    (37.0850,25.1500),(36.3932,25.4615),(37.5379,25.1634),(38.3687,26.1359),
    (36.8928,27.2877),(39.1070,26.5550),
]

FIELDS = ["places.name","places.displayName","places.formattedAddress",
          "places.googleMapsUri","places.rating","places.userRatingCount",
          "places.types","places.id","places.location"]

SEARCH_URL   = "https://places.googleapis.com/v1/places:searchText"
DETAILS_BASE = "https://places.googleapis.com/v1/"
SESSION = requests.Session()

# ─────────────────────────── UNICODE HELPERS ──────────────────────────────
def _strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "")
                   if unicodedata.category(c) != "Mn")

def _norm(s):
    return _re.sub(r"\s+", " ", _strip_accents(s or "").lower()).strip()

def _norm_np(s):
    return _re.sub(r"[^\w\s]", " ", _norm(s))

# ─────────────────────────── BRAND RULES ──────────────────────────────────
KEYWORDS_NORM: Dict[str, List[str]] = {
    "ACS":                ["acs"],
    "Courier Center":     ["courier center"],
    "EASYMAIL":           ["easymail", "easy mail", "easy-mail"],
    "SPEEDEX":            ["speedex", "speed ex"],
    "Γενική Ταχυδρομική": ["γενικη ταχυδρομικη","γενικη","ταχυδρομικη",
                           "geniki tachydromiki","geniki","tachydromiki"],
    "ΕΛΤΑ Courier":       ["ελτα courier","elta courier","ελτα κουριερ",
                           "ταχυμεταφορες ελτα","ελτα","elta"],
}

REASSIGN_ALIASES: Dict[str, List[str]] = {
    "SPEEDEX":            ["speedex","speed ex"],
    "ACS":                ["acs","acs courier"],
    "Γενική Ταχυδρομική": ["γενικη ταχυδρομικη","geniki tachydromiki"],
    "ΕΛΤΑ Courier":       ["ελτα","ελτα courier","elta","elta courier"],
    "Courier Center":     ["courier center"],
    "EASYMAIL":           ["easymail","easy mail"],
}

BLACKLIST = [
    "city courier","city express","general courier","sports center","dpd",
    "smartpoint","smart point","dhl","hub","clever point","ups","artcourier",
    "kritiki tahidromiki","ελτα πρακτορειο","ελτα -","ελληνικα ταχυδρομεια",
    "hellenic post","ταχυδρομικο πρακτορειο","ταχυδρομειο","postal agency",
    "ταχυδρομικο ταμιευτηριο","box express","icc","taxydema",
]
_BL_RE = _re.compile("|".join(_re.escape(t) for t in BLACKLIST), _re.I)

# ─────────────────────────── REGION INFERENCE ────────────────────────────
_ISLAND_KW = {
    "ΚΡΗΤΗ": ["κρητη","heraklion","irakleio","chania","rethymno",
               "agios nikolaos","ierapetra","moires"],
    "ΝΗΣΙΑ_ΔΥΤΙΚΑ": ["κερκυρα","corfu","kerkira","kerkyra"],
    "ΝΗΣΙΑ_ΑΛΛΑ":   ["ροδο","rhodes","παρος","paros","σαντορινη","thira",
                     "θηρα","τηνος","tinos","χιος","chios","κως","kos",
                     "μυτιληνη","lesvos","λεσβος","μυκονος","mykonos",
                     "ναξος","naxos","σαμος","samos","ικαρια","ikaria"],
}

def _bbox(lat, lng, r0, r1, c0, c1):
    return lat is not None and lng is not None and r0 <= lat <= r1 and c0 <= lng <= c1

def infer_region(name, addr, lat, lng):
    txt = _norm(f"{name} {addr}")
    for kw in _ISLAND_KW["ΚΡΗΤΗ"]:
        if kw in txt: return "ΚΡΗΤΗ"
    for kw in _ISLAND_KW["ΝΗΣΙΑ_ΔΥΤΙΚΑ"]:
        if kw in txt: return "Δυτική Ελλάδα (με Κέρκυρα)"
    for kw in _ISLAND_KW["ΝΗΣΙΑ_ΑΛΛΑ"]:
        if kw in txt: return "Υπόλοιπα νησιά"
    if _bbox(lat, lng, 34.7, 35.9, 23.3, 26.7): return "ΚΡΗΤΗ"
    if _bbox(lat, lng, 37.6, 38.4, 23.0, 24.2): return "ΑΤΤΙΚΗ"
    if _bbox(lat, lng, 40.4, 40.9, 22.7, 23.2): return "Θεσσαλονίκη"
    if _bbox(lat, lng, 36.2, 38.5, 21.0, 23.8): return "Πελοπόννησος"
    if lat is not None and lng is not None and (lng < 22.0 or (39.3 <= lat <= 39.9 and 19.5 <= lng <= 20.8)):
        return "Δυτική Ελλάδα (με Κέρκυρα)"
    if lat is not None and lat >= 40.0: return "Βόρεια Ελλάδα"
    return "Κεντρική Ελλάδα"

REGION_ORDER = ["ΑΤΤΙΚΗ","Κεντρική Ελλάδα","Θεσσαλονίκη","Βόρεια Ελλάδα",
                "Δυτική Ελλάδα (με Κέρκυρα)","Πελοπόννησος","ΚΡΗΤΗ","Υπόλοιπα νησιά"]

# ─────────────────────────── API CALLS ────────────────────────────────────
def _headers():
    return {"Content-Type":"application/json",
            "X-Goog-Api-Key": API_KEY,
            "X-Goog-FieldMask": ",".join(FIELDS)}

def _det_headers():
    return {"Content-Type":"application/json",
            "X-Goog-Api-Key": API_KEY,
            "X-Goog-FieldMask": "rating,userRatingCount,location,formattedAddress,googleMapsUri,displayName"}

def search_text(query):
    results = []
    for clat, clng in GREECE_CENTERS:
        payload = {"textQuery": query, "languageCode": "el", "regionCode": "GR",
                   "locationBias": {"circle": {"center": {"latitude": clat, "longitude": clng},
                                               "radius": SEARCH_RADIUS_M}}}
        r = SESSION.post(SEARCH_URL, headers=_headers(), json=payload, timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            results.extend(r.json().get("places", []))
        time.sleep(RATE_SLEEP)
    return results

@lru_cache(maxsize=4096)
def fetch_details(resource_name):
    r = SESSION.get(DETAILS_BASE + resource_name, headers=_det_headers(), timeout=HTTP_TIMEOUT)
    return r.json() if r.status_code == 200 else {}

# ─────────────────────────── DATA COLLECTION ──────────────────────────────
def collect():
    rows, seen = [], set()
    for brand, queries in BRANDS.items():
        brand_start = len(rows)
        for q in queries:
            try:
                places = search_text(q)
            except Exception as e:
                print(f"[WARN] {brand} / '{q}': {e}"); continue
            for p in places:
                rid = p.get("name") or p.get("id")
                if not rid or rid in seen: continue
                seen.add(rid)
                det = fetch_details(p["name"]) if not p.get("rating") else {}
                loc = (p.get("location") or det.get("location") or {})
                rows.append({
                    "brand": brand,
                    "place_name": (p.get("displayName") or {}).get("text"),
                    "address": p.get("formattedAddress") or det.get("formattedAddress"),
                    "rating": p.get("rating") or det.get("rating"),
                    "user_rating_count": p.get("userRatingCount") or det.get("userRatingCount"),
                    "maps_url": p.get("googleMapsUri") or det.get("googleMapsUri"),
                    "lat": loc.get("latitude"),
                    "lng": loc.get("longitude"),
                })
        print(f"[INFO] {brand}: {len(rows)-brand_start} places")
    return pd.DataFrame(rows)

# ─────────────────────────── CLEANING ─────────────────────────────────────
def clean(df):
    # Blacklist
    bl = df["place_name"].fillna("").str.contains(_BL_RE, na=False)
    if bl.sum(): print(f"[CLEAN] Blacklist removed: {bl.sum()}")
    df = df[~bl].copy()

    # Reassign
    changed = 0
    for idx, row in df.iterrows():
        txt = _norm(f"{row.get('place_name','')} {row.get('address','')}")
        for canonical, aliases in REASSIGN_ALIASES.items():
            if any(a and a in txt for a in aliases):
                if canonical != row["brand"]:
                    df.at[idx, "brand"] = canonical; changed += 1
                break
    if changed: print(f"[REASSIGN] Changed: {changed}")

    # Enforce brand⇒place_name rule
    def ok(row):
        b = str(row.get("brand",""))
        txt = _norm_np(str(row.get("place_name","")))
        kws = KEYWORDS_NORM.get(b, [])
        return any(kw in txt for kw in kws) if kws else True
    mask = df.apply(ok, axis=1)
    if (~mask).sum(): print(f"[FILTER] Brand-name rule removed: {(~mask).sum()}")
    df = df[mask].copy()

    # Dedup
    def make_key(row):
        url = (row.get("maps_url") or "").strip()
        if url: return ("URL", url)
        return ("PNAD", f"{_norm(str(row.get('place_name','')))}|{_norm(str(row.get('address','')))}") 
    chosen = {}
    for _, row in df.iterrows():
        key = make_key(row)
        cur = chosen.get(key)
        if cur is None: chosen[key] = row; continue
        cur_urc  = float(cur.get("user_rating_count") or 0)
        row_urc  = float(row.get("user_rating_count") or 0)
        if row_urc > cur_urc: chosen[key] = row
    before = len(df)
    df = pd.DataFrame(list(chosen.values())).reset_index(drop=True)
    if before - len(df): print(f"[DEDUP] Removed: {before-len(df)}")
    return df

# ─────────────────────────── SUMMARISE ────────────────────────────────────
def summarize(df):
    out = {}
    for brand, g in df.groupby("brand"):
        g = g.dropna(subset=["rating"]).copy()
        r   = pd.to_numeric(g["rating"],            errors="coerce").dropna()
        urc = pd.to_numeric(g.loc[r.index,"user_rating_count"], errors="coerce").fillna(0)
        total = int(urc.sum())
        out[brand] = {
            "weighted_avg":  round(float((r*urc).sum()/total), 2) if total else None,
            "simple_avg":    round(float(r.mean()), 2) if len(r) else None,
            "total_reviews": total,
            "store_count":   len(g),
        }
    return out

def summarize_regions(df):
    df = df.copy()
    df["region"] = df.apply(
        lambda r: infer_region(str(r.get("place_name","")), str(r.get("address","")),
                               r.get("lat"), r.get("lng")), axis=1)
    out = {}
    for (region, brand), g in df.groupby(["region","brand"]):
        g = g.dropna(subset=["rating"]).copy()
        r   = pd.to_numeric(g["rating"],            errors="coerce").dropna()
        urc = pd.to_numeric(g.loc[r.index,"user_rating_count"], errors="coerce").fillna(0)
        total = int(urc.sum())
        key = f"{region}||{brand}"
        out[key] = {
            "region": region, "brand": brand,
            "weighted_avg":  round(float((r*urc).sum()/total), 2) if total else None,
            "total_reviews": total,
            "store_count":   len(g),
        }
    return out

def places_list(df):
    rows = []
    for _, r in df.dropna(subset=["rating"]).iterrows():
        rows.append({
            "brand":      str(r.get("brand","")),
            "place_name": str(r.get("place_name","")),
            "address":    str(r.get("address","")),
            "rating":     round(float(r["rating"]),1),
            "reviews":    int(r.get("user_rating_count") or 0),
            "maps_url":   str(r.get("maps_url","")),
            "lat":        float(r["lat"]) if pd.notna(r.get("lat")) else None,
            "lng":        float(r["lng"]) if pd.notna(r.get("lng")) else None,
        })
    return rows

# ─────────────────────────── HISTORY ──────────────────────────────────────
def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"snapshots": []}

def save_history(h):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False, indent=2)

def append_snapshot(history, date_str, label, summary, regions, places):
    # Remove existing snapshot for same date
    history["snapshots"] = [s for s in history["snapshots"] if s["date"] != date_str]
    history["snapshots"].append({
        "date": date_str, "label": label,
        "summary": summary, "regions": regions, "places": places,
    })
    history["snapshots"].sort(key=lambda s: s["date"])
    return history

# ─────────────────────────── HTML REPORT ──────────────────────────────────
def build_html(history):
    """Builds a fully self-contained HTML report from history.json"""
    history_json = json.dumps(history, ensure_ascii=False)
    snaps = history["snapshots"]
    latest = snaps[-1]
    prev   = snaps[-2] if len(snaps) >= 2 else None
    now_label = latest["label"]

    html = f"""<!DOCTYPE html>
<html lang="el">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Courier Ratings Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js"></script>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f4f6f9;color:#1a1a2e;font-size:14px}}
  .topbar{{background:#1a1a2e;color:#fff;padding:14px 24px;display:flex;align-items:center;justify-content:space-between}}
  .topbar h1{{font-size:17px;font-weight:600;letter-spacing:.3px}}
  .topbar .ts{{font-size:12px;color:#aab;margin-top:2px}}
  .container{{max-width:1200px;margin:0 auto;padding:20px 16px}}
  h2{{font-size:15px;font-weight:600;margin:24px 0 12px;color:#1a1a2e;padding-left:4px;border-left:3px solid #3b82f6}}
  /* Summary cards */
  .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:12px;margin-bottom:8px}}
  .card{{background:#fff;border-radius:10px;padding:14px 16px;box-shadow:0 1px 4px rgba(0,0,0,.08);position:relative}}
  .card .brand{{font-size:12px;color:#666;font-weight:500;margin-bottom:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .card .score{{font-size:28px;font-weight:700;line-height:1}}
  .card .meta{{font-size:11px;color:#999;margin-top:4px}}
  .card .delta{{position:absolute;top:14px;right:14px;font-size:12px;font-weight:600;padding:2px 6px;border-radius:20px}}
  .up{{background:#dcfce7;color:#166534}} .dn{{background:#fee2e2;color:#991b1b}} .nc{{background:#f1f5f9;color:#64748b}}
  .cc{{border-top:3px solid #3b82f6}}
  /* Trend chart */
  .chart-wrap{{background:#fff;border-radius:10px;padding:16px;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:8px}}
  .chart-wrap canvas{{max-height:280px}}
  /* Regions table */
  .tbl-wrap{{background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:8px}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  thead th{{background:#1a1a2e;color:#fff;padding:9px 12px;text-align:left;font-weight:500;white-space:nowrap}}
  tbody tr:hover{{background:#f8fafc}}
  tbody td{{padding:8px 12px;border-bottom:.5px solid #e8ecf0}}
  .region-hdr{{background:#f1f5f9!important;font-weight:600;color:#334155}}
  .brand-cc{{font-weight:700;color:#1d4ed8}}
  .pill{{display:inline-block;padding:2px 7px;border-radius:20px;font-size:11px;font-weight:600}}
  .g4{{background:#dcfce7;color:#166534}} .g35{{background:#d1fae5;color:#065f46}}
  .y3{{background:#fef9c3;color:#854d0e}} .r2{{background:#fee2e2;color:#991b1b}}
  /* Movers */
  .movers{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:8px}}
  .mover-box{{background:#fff;border-radius:10px;padding:14px 16px;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
  .mover-box h3{{font-size:13px;font-weight:600;margin-bottom:10px}}
  .mover-row{{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:.5px solid #f0f0f0}}
  .mover-row:last-child{{border-bottom:none}}
  .mover-name{{font-size:12px;color:#334155;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-right:8px}}
  .mover-brand{{font-size:10px;color:#94a3b8;display:block}}
  /* Stores table */
  .filter-bar{{display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap}}
  .filter-bar select,.filter-bar input{{padding:6px 10px;border:.5px solid #d1d5db;border-radius:6px;font-size:13px;background:#fff}}
  .filter-bar input{{flex:1;min-width:150px}}
  @media(max-width:640px){{.movers{{grid-template-columns:1fr}}.cards{{grid-template-columns:repeat(2,1fr)}}}}
</style>
</head>
<body>
<div class="topbar">
  <div>
    <div class="ts">Courier Ratings Dashboard</div>
    <div class="ts" style="font-size:13px;font-weight:600;margin-top:2px">Τελευταία ενημέρωση: {now_label}</div>
  </div>
  <div class="ts" id="run-ts"></div>
</div>

<div class="container">

  <!-- Summary cards -->
  <h2>Συνολική Αξιολόγηση (Weighted Average)</h2>
  <div class="cards" id="summary-cards"></div>

  <!-- Trend chart -->
  <h2>Τάση Weighted Average (Πανελλαδικά)</h2>
  <div class="chart-wrap"><canvas id="trend-chart"></canvas></div>

  <!-- Regions table -->
  <h2>Αποτελέσματα ανά Περιοχή</h2>
  <div class="tbl-wrap"><table id="region-table">
    <thead><tr>
      <th>Περιοχή</th><th>Brand</th>
      <th>Weighted Avg</th><th>Κριτικές</th>
      <th>vs {(prev or {}).get('label','προηγ.')}</th>
    </tr></thead>
    <tbody id="region-tbody"></tbody>
  </table></div>

  <!-- Top movers -->
  <h2>Καταστήματα — Μεγαλύτερες Κινήσεις</h2>
  <div class="movers">
    <div class="mover-box">
      <h3>⬆ Top Ανοδικά (vs {(prev or {}).get('label','προηγ.')})</h3>
      <div id="movers-up"></div>
    </div>
    <div class="mover-box">
      <h3>⬇ Top Καθοδικά (vs {(prev or {}).get('label','προηγ.')})</h3>
      <div id="movers-dn"></div>
    </div>
  </div>

  <!-- All stores -->
  <h2>Όλα τα Καταστήματα</h2>
  <div class="filter-bar">
    <select id="f-brand"><option value="">Όλα τα brands</option></select>
    <select id="f-region"><option value="">Όλες οι περιοχές</option></select>
    <input id="f-search" placeholder="Αναζήτηση ονόματος / διεύθυνσης…">
  </div>
  <div class="tbl-wrap"><table>
    <thead><tr>
      <th>Brand</th><th>Κατάστημα</th><th>Διεύθυνση</th>
      <th>Rating</th><th>Κριτικές</th><th>Δ vs προηγ.</th><th></th>
    </tr></thead>
    <tbody id="stores-tbody"></tbody>
  </table></div>

</div>

<script>
const HISTORY = {history_json};
const REGION_ORDER = {json.dumps(REGION_ORDER)};
const BRAND_COLORS = {{
  "Courier Center":     "#3b82f6",
  "ACS":                "#f59e0b",
  "EASYMAIL":           "#10b981",
  "ΕΛΤΑ Courier":       "#8b5cf6",
  "SPEEDEX":            "#ef4444",
  "Γενική Ταχυδρομική": "#6b7280",
}};

const snaps   = HISTORY.snapshots;
const latest  = snaps[snaps.length-1];
const prev    = snaps.length >= 2 ? snaps[snaps.length-2] : null;
const allBrands = Object.keys(BRAND_COLORS);

// ── Utility ────────────────────────────────────────────────────────────
function ratingClass(r){{
  if(r===null||r===undefined) return '';
  if(r >= 4.0) return 'g4';
  if(r >= 3.5) return 'g35';
  if(r >= 3.0) return 'y3';
  return 'r2';
}}
function deltaHtml(cur, prv){{
  if(prv===null||prv===undefined||cur===null||cur===undefined) return '<span class="pill nc">—</span>';
  const d = (cur-prv);
  const cls = d>0.005?'up':d<-0.005?'dn':'nc';
  const sign = d>0.005?'+':'';
  return `<span class="pill ${{cls}}">${{sign}}${{d.toFixed(2)}}</span>`;
}}

// ── Summary cards ──────────────────────────────────────────────────────
const cardsEl = document.getElementById('summary-cards');
allBrands.forEach(b=>{{
  const cur = latest.summary[b];
  if(!cur) return;
  const prv = prev && prev.summary[b];
  const d   = (prv && cur) ? cur.weighted_avg - prv.weighted_avg : null;
  const dCls = d===null?'nc':d>0.005?'up':d<-0.005?'dn':'nc';
  const dTxt = d===null?'—':(d>0.005?'+':'')+d.toFixed(2);
  const cc = b==='Courier Center' ? ' cc' : '';
  cardsEl.innerHTML += `
    <div class="card${{cc}}">
      <div class="brand">${{b}}</div>
      <div class="score" style="color:${{BRAND_COLORS[b]}}">${{cur.weighted_avg !== null ? cur.weighted_avg.toFixed(2) : '—'}}</div>
      <div class="meta">${{cur.total_reviews ? cur.total_reviews.toLocaleString('el-GR') : '—'}} κριτικές · ${{cur.store_count||'—'}} καταστήματα</div>
      <span class="delta ${{dCls}}">${{dTxt}}</span>
    </div>`;
}});

// ── Trend chart ────────────────────────────────────────────────────────
const labels = snaps.map(s=>s.label);
const datasets = allBrands.map(b=>{{
  return {{
    label: b,
    data: snaps.map(s => s.summary[b] ? s.summary[b].weighted_avg : null),
    borderColor: BRAND_COLORS[b],
    backgroundColor: BRAND_COLORS[b]+'33',
    tension: 0.3,
    pointRadius: 4,
    borderWidth: b==='Courier Center' ? 3 : 1.5,
    spanGaps: true,
  }};
}});
new Chart(document.getElementById('trend-chart'),{{
  type:'line',
  data:{{labels, datasets}},
  options:{{
    responsive:true, maintainAspectRatio:true,
    interaction:{{mode:'index', intersect:false}},
    plugins:{{legend:{{position:'bottom', labels:{{boxWidth:12,font:{{size:11}}}}}},
              tooltip:{{callbacks:{{label:ctx=>`${{ctx.dataset.label}}: ${{ctx.raw !== null ? ctx.raw.toFixed(2) : '—'}}`}}}}}},
    scales:{{y:{{min:2.0, max:5.0, ticks:{{stepSize:0.25, font:{{size:11}}}}}},
              x:{{ticks:{{font:{{size:11}}}}}}}}
  }}
}});

// ── Regions table ──────────────────────────────────────────────────────
const tbody = document.getElementById('region-tbody');
let lastRegion = null;
const regionEntries = Object.values(latest.regions);
regionEntries.sort((a,b)=>{{
  const ra = REGION_ORDER.indexOf(a.region), rb = REGION_ORDER.indexOf(b.region);
  const ord = (ra===-1?99:ra) - (rb===-1?99:rb);
  if(ord!==0) return ord;
  return (b.weighted_avg||0)-(a.weighted_avg||0);
}});
regionEntries.forEach(item=>{{
  const prvVal = prev && prev.regions && prev.regions[`${{item.region}}||${{item.brand}}`];
  const prvAvg = prvVal ? prvVal.weighted_avg : null;
  if(item.region !== lastRegion){{
    tbody.innerHTML += `<tr><td colspan="5" class="region-hdr">📍 ${{item.region}}</td></tr>`;
    lastRegion = item.region;
  }}
  const bcc = item.brand==='Courier Center'?' brand-cc':'';
  tbody.innerHTML += `<tr>
    <td></td>
    <td class="${{bcc}}">${{item.brand}}</td>
    <td><span class="pill ${{ratingClass(item.weighted_avg)}}">${{item.weighted_avg !== null ? item.weighted_avg.toFixed(2) : '—'}}</span></td>
    <td>${{item.total_reviews ? item.total_reviews.toLocaleString('el-GR') : '—'}}</td>
    <td>${{deltaHtml(item.weighted_avg, prvAvg)}}</td>
  </tr>`;
}});

// ── Movers ────────────────────────────────────────────────────────────
const latestPlaces = latest.places || [];
const prevPlaces   = (prev && prev.places) || [];

function keyOf(p){{ return p.maps_url || `${{p.brand}}|${{p.place_name}}|${{p.address}}`; }}
const prevMap = {{}};
prevPlaces.forEach(p=>{{ prevMap[keyOf(p)] = p; }});

const movers = latestPlaces
  .filter(p => p.rating !== null && prevMap[keyOf(p)] && prevMap[keyOf(p)].rating !== null)
  .map(p => ({{ ...p, delta: p.rating - prevMap[keyOf(p)].rating }}))
  .filter(p => Math.abs(p.delta) >= 0.05);
movers.sort((a,b)=>b.delta-a.delta);

function moverRow(p){{
  const sign = p.delta > 0 ? '+' : '';
  const cls  = p.delta > 0 ? 'up' : 'dn';
  return `<div class="mover-row">
    <div class="mover-name">${{p.place_name}}<span class="mover-brand">${{p.brand}} · ${{p.reviews.toLocaleString('el-GR')}} κριτ.</span></div>
    <span class="pill ${{cls}}">${{sign}}${{p.delta.toFixed(2)}}</span>
  </div>`;
}}
const upEl = document.getElementById('movers-up');
const dnEl = document.getElementById('movers-dn');
const top5up = movers.filter(p=>p.delta>0).slice(0,8);
const top5dn = movers.filter(p=>p.delta<0).slice(-8).reverse();
upEl.innerHTML = top5up.length ? top5up.map(moverRow).join('') : '<div style="color:#94a3b8;font-size:12px">Δεν υπάρχουν δεδομένα</div>';
dnEl.innerHTML = top5dn.length ? top5dn.map(moverRow).join('') : '<div style="color:#94a3b8;font-size:12px">Δεν υπάρχουν δεδομένα</div>';

// ── Stores table ──────────────────────────────────────────────────────
const fBrand  = document.getElementById('f-brand');
const fRegion = document.getElementById('f-region');
const fSearch = document.getElementById('f-search');
const storesTbody = document.getElementById('stores-tbody');

// Populate filter dropdowns
allBrands.forEach(b=>{{ fBrand.innerHTML += `<option value="${{b}}">${{b}}</option>`; }});
REGION_ORDER.forEach(r=>{{ fRegion.innerHTML += `<option value="${{r}}">${{r}}</option>`; }});

function regionOf(p){{
  // Use inferred region from regions data
  for(const key of Object.keys(latest.regions)){{
    const entry = latest.regions[key];
    if(entry.brand===p.brand){{
      // rough match by address
      if(p.address && p.address.includes(entry.region.split(' ')[0])) return entry.region;
    }}
  }}
  return '';
}}

function renderStores(){{
  const bFilter = fBrand.value;
  const rFilter = fRegion.value;
  const sFilter = fSearch.value.toLowerCase();
  let rows = latestPlaces.filter(p=>{{
    if(bFilter && p.brand !== bFilter) return false;
    if(sFilter && !p.place_name.toLowerCase().includes(sFilter) && !p.address.toLowerCase().includes(sFilter)) return false;
    return true;
  }});
  rows.sort((a,b)=>b.reviews-a.reviews);
  storesTbody.innerHTML = rows.slice(0,200).map(p=>{{
    const prv = prevMap[keyOf(p)];
    const dHtml = prv && prv.rating ? deltaHtml(p.rating, prv.rating) : '<span class="pill nc">—</span>';
    const mapLink = p.maps_url ? `<a href="${{p.maps_url}}" target="_blank" style="color:#3b82f6;font-size:11px">Maps ↗</a>` : '';
    const bcc = p.brand==='Courier Center'?' brand-cc':'';
    return `<tr>
      <td class="${{bcc}}">${{p.brand}}</td>
      <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${{p.place_name}}</td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#64748b">${{p.address}}</td>
      <td><span class="pill ${{ratingClass(p.rating)}}">${{p.rating.toFixed(1)}}</span></td>
      <td>${{p.reviews.toLocaleString('el-GR')}}</td>
      <td>${{dHtml}}</td>
      <td>${{mapLink}}</td>
    </tr>`;
  }}).join('');
}}
fBrand.addEventListener('change', renderStores);
fRegion.addEventListener('change', renderStores);
fSearch.addEventListener('input', renderStores);
renderStores();

document.getElementById('run-ts').textContent = 'Generated: ' + new Date().toLocaleString('el-GR');
</script>
</body>
</html>"""
    return html

# ─────────────────────────── GIT PUSH ─────────────────────────────────────
def git_push(date_str):
    if not GIT_PUSH:
        print("[GIT] Skipped (GIT_PUSH=False)")
        return
    cmds = [
        ["git", "-C", REPO_DIR, "add", "index.html", "history.json"],
        ["git", "-C", REPO_DIR, "commit", "-m", COMMIT_MSG.format(date=date_str)],
        ["git", "-C", REPO_DIR, "push"],
    ]
    for cmd in cmds:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"[GIT] Warning: {' '.join(cmd)}\n{r.stderr}")
        else:
            print(f"[GIT] OK: {' '.join(cmd[-2:])}")

# ─────────────────────────── MAIN ─────────────────────────────────────────
def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    label = datetime.now(timezone.utc).strftime("%d/%m/%Y")
    print(f"\n{'='*55}\nCourier Ratings — {label}\n{'='*55}")

    print("\n[1/5] Collecting places from Google API...")
    df = collect()

    print("\n[2/5] Cleaning data...")
    df = clean(df)
    print(f"      → {len(df)} places after cleaning")

    print("\n[3/5] Summarising...")
    summary = summarize(df)
    regions = summarize_regions(df)
    places  = places_list(df)
    for b, v in sorted(summary.items(), key=lambda x: -(x[1]['weighted_avg'] or 0)):
        print(f"      {b:25s} weighted={v['weighted_avg']}  reviews={v['total_reviews']}")

    print("\n[4/5] Updating history.json...")
    history = load_history()
    history = append_snapshot(history, today, label, summary, regions, places)
    save_history(history)

    print("\n[5/5] Building HTML report...")
    html = build_html(history)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"      → {REPORT_FILE}")

    git_push(today)
    print(f"\n✅ Done — {label}")

if __name__ == "__main__":
    main()
