#!/usr/bin/env python3
"""
scan_jobs.py — daily job-discovery agent for automation/job-search/.

Subcommands: discover, ingest, queue, mark, render (see USAGE below).
Phase 1 implements discover/render fully. ingest/queue/mark are Phase 2/4 —
present in the dispatch table so the CLI shape is stable, but not yet wired.

WHY THIS EXISTS
Finds candidate job postings from the companies in sources.json, screens them
against job-criteria.md, and writes today-jobs.md — a checklist the user
reviews by hand. No browser, no third-party packages: everything here is
stdlib urllib hitting each ATS's own public JSON API (verified working
without auth for Workday-based companies as of 2026-08-11).

USAGE
    python3 scan_jobs.py discover     # daily main flow: fetch, screen, render
    python3 scan_jobs.py backfill     # re-screen cached rejected/candidate jobs against current criteria
    python3 scan_jobs.py render       # regenerate today-jobs.md from ledger.json only
    python3 scan_jobs.py applied      # regenerate applied-jobs.md (full application history) from ledger.json
    python3 scan_jobs.py progress <job_key> <status> [--note "..."]
                                       # update post-application status (pending/interview/offer/rejected/withdrawn)

CLI CONTRACT
This CLI is a seam, not just a convenience: it is the only writer of ledger.json,
and two other programs cross it as adapters — the dashboard (server.py, via
run_cli) and the apply batcher (apply_batch.py, via load_queue). Neither can
import this module, so everything they rely on is stdout text and an exit code.
That makes the following part of the interface. Changing it breaks them silently.

  Exit codes
    0    the subcommand did what it said
    1    unknown subcommand, or missing/invalid arguments (usage goes to stdout)
    !=0  refused or failed; the reason is a single line, and callers read
         stderr first then fall back to stdout (see server.py run_cli_path)
    `discover` exits non-zero when another scan holds the ledger lock. That is a
    refusal, not a crash — the message names the holder.

  stdout
    `queue` is the ONLY subcommand with machine-readable output: a single JSON
    object, nothing before or after it, keyed by company_id:
        {"asml": [{"key", "title", "url", "locations", "selected_date"}, ...]}
    Jobs are sorted by selected_date within each company. An empty queue is `{}`,
    not an error. Never print anything else to stdout from that path — a stray
    log line makes apply_batch.py fail to parse and abort the whole batch.

    Every other subcommand prints human prose with no guaranteed shape. Callers
    may show it to a person; they must not parse it.
"""
import html
import json
import os
import re
import smtplib
import subprocess
import sys
import time
import socket
import urllib.error
import urllib.request
from collections import Counter
from datetime import date
from email.mime.text import MIMEText

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.dirname(BASE_DIR)  # job-search/ — the user-facing files live one level up from this script

# 共用模組(runlock、config)住在 automation/_internal/。往上找到叫 automation
# 的那一層,不要數 ".." —— 這裡數錯過三次,其中一次讓排程掃描靜靜死了兩天,
# 因為它在寫 log 之前就死了。見 automation/_internal/test_imports.py。
_shared = os.path.abspath(__file__)
while os.path.basename(_shared) != "automation" and _shared != os.path.dirname(_shared):
    _shared = os.path.dirname(_shared)
sys.path.insert(0, os.path.join(_shared, "_internal"))
# Every writer of ledger.json shares one lock (automation/_internal/runlock.py).
# Before it existed the two schedules were merely staggered 20 minutes apart and
# hoped not to collide; the dashboard's manual buttons made that hope untenable.
import config  # noqa: E402
from runlock import ledger_lock, LockBusy  # noqa: E402
SOURCES_PATH = os.path.join(BASE_DIR, "sources.json")


def load_sources():
    """sources.json is public and ships with placeholders for anything secret-ish.
    config.json's optional `source_secrets` block overlays real values onto each
    company's `config`, keyed by company id — that file is gitignored, so a real
    token never lands in the repo. Example:
        "source_secrets": {"asml": {"auth_token": "01-..."}}
    """
    with open(SOURCES_PATH, encoding="utf-8") as fh:
        sources = json.load(fh)
    secrets = config.get("source_secrets", {})
    for company in sources.get("companies", []):
        for key, value in secrets.get(company.get("id"), {}).items():
            company.setdefault("config", {})[key] = value
    return sources
CRITERIA_PATH = os.path.join(OUTPUT_DIR, "job-criteria.md")
LEDGER_PATH = os.path.join(BASE_DIR, "ledger.json")
LEDGER_BAK_PATH = os.path.join(BASE_DIR, "ledger.bak.json")
TODAY_JOBS_PATH = os.path.join(OUTPUT_DIR, "today-jobs.md")
APPLIED_JOBS_PATH = os.path.join(OUTPUT_DIR, "applied-jobs.md")

GMAIL_ADDRESS = config.require("gmail_address")
GMAIL_APP_PASSWORD_KEYCHAIN_SERVICE = config.get(
    "gmail_app_password_keychain_service", "job-scan-smtp-app-password")

TODAY = date.today().isoformat()
VALID_PROGRESS = ("pending", "interview", "offer", "rejected", "withdrawn")
PROGRESS_LABEL = {
    "pending": "待回覆", "interview": "面試中", "offer": "Offer",
    "rejected": "已拒絕", "withdrawn": "已撤回",
}
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

WEEKDAYS_ZH = ["一", "二", "三", "四", "五", "六", "日"]


# ---------------------------------------------------------------------------
# criteria parsing (job-criteria.md — dual human/machine-readable format)
# ---------------------------------------------------------------------------

_KEY_RE = re.compile(r"[(（]([a-z_]+)[)）]")


def load_criteria(path):
    """Parses '## Heading (machine_key)' + '- ' bullets into {key: [[syn,...]]},
    with 'key: value' scalar lines (e.g. thresholds) collected under '_scalars'.
    A stray typo in a (machine_key) just drops that section silently rather
    than crashing — validate_criteria() below is what catches that."""
    out = {"_scalars": {}}
    cur = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s.startswith("##"):
                m = _KEY_RE.search(s)
                cur = m.group(1) if m else None
                if cur:
                    out.setdefault(cur, [])
                continue
            if not cur or not s.startswith("- "):
                continue
            body = s[2:].strip()
            if ":" in body and body.split(":", 1)[0].strip().replace("_", "").isalpha():
                k, v = body.split(":", 1)
                out["_scalars"][k.strip()] = v.strip()
            else:
                out[cur].append([t.strip().lower() for t in body.split("|") if t.strip()])
    return out


def validate_criteria(criteria):
    """Non-negotiable safety check (risk 5 in the plan): a typo'd (machine_key)
    silently produces an empty list, which would otherwise reject every job
    and look like a legitimately-empty day. Abort before render instead."""
    problems = []
    for key in ("allow_locations", "accept_education_fields"):
        if not criteria.get(key):
            problems.append(key)
    if problems:
        raise ValueError(
            "job-criteria.md parsed to an EMPTY list for: %s — check for a typo'd "
            "(machine_key) heading. Aborting before touching today-jobs.md." % ", ".join(problems)
        )


# ---------------------------------------------------------------------------
# screening
# ---------------------------------------------------------------------------

def _matches_any(text, synonym_groups):
    text_l = text.lower()
    for group in synonym_groups:
        for term in group:
            if not term:
                continue
            pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
            if re.search(pattern, text_l):
                return term
    return None


def title_reject_screen(title, criteria):
    """Cheap pre-filter run BEFORE fetching the JD — reject-list only, no
    accept requirement. reject_titles terms (technician/intern/manager/
    assistant/associate engineer) are unambiguous enough to trust from the
    title alone, so they still short-circuit before paying for a detail
    fetch. The accept gate lives in education_accept_screen() instead (see
    below), based on the JD's stated academic background — job function is
    signaled far more reliably by what the JD actually asks for than by an
    employer's own title wording."""
    hit = _matches_any(title, criteria.get("reject_titles", []))
    if hit:
        return ("reject", "reject_titles: %s" % hit)
    return ("pass", None)


def location_screen(locations, criteria):
    """Pure allowlist substring match against allow_locations synonyms —
    deliberately no geographic inference (求職條件.md's explicit rule)."""
    allow_groups = criteria.get("allow_locations", [])
    for loc in locations:
        loc_l = (loc or "").lower()
        for group in allow_groups:
            for term in group:
                if term and term in loc_l:
                    return ("accept", "%s ~ %s" % (loc, term))
    return ("reject", "不在允許縣市: %s" % ", ".join(locations) if locations else "no location data")


_YEARS_EN_RE = re.compile(r"(\d{1,2})\s*(?:\+|\s*[-–]\s*\d{1,2})?\s*\+?\s*(?:or more\s*)?year", re.I)
_YEARS_ZH_RE = re.compile(r"(\d{1,2})\s*年(?:以上|經驗|工作經驗)")


def years_screen(jd_text, scalars):
    """Clean binary: reject if JD's minimum stated years >=
    reject_if_min_years_at_least, otherwise pass. Takes min() of every number
    found near 'year(s)'/'年' so a JD saying '3 years required, 5 preferred'
    screens on 3, not 5."""
    nums = [int(m) for m in _YEARS_EN_RE.findall(jd_text)] + [int(m) for m in _YEARS_ZH_RE.findall(jd_text)]
    nums = [n for n in nums if 0 < n <= 20]
    if not nums:
        return ("pass", None)
    lo = min(nums)
    reject_at = int(scalars.get("reject_if_min_years_at_least", 99))
    if lo >= reject_at:
        return ("reject", "JD 最低年資 %d 年" % lo)
    return ("pass", None)


_DEGREE_RE = re.compile(r"ph\.?\s?d|doctora|博士", re.I)
_DEGREE_REQUIRED_RE = re.compile(r"required|must have|必須|需具備", re.I)
_DEGREE_SOFTEN_RE = re.compile(r"preferred|a plus|佳|尤佳|or\s+(?:ms|master)", re.I)


def degree_screen(jd_text, scalars, structured_degree_levels=None):
    """Reject if a PhD is required and a Master's is not sufficient —
    'PhD preferred', 'MS or PhD', '碩士以上' all still pass, since a Master's
    is enough for those. Hard-rejects rather than flagging: 只要碩士可以做的
    工作 (jobs a Master's is sufficient for) is the explicit bar.

    When structured_degree_levels is available (ASML's own job_degrees tag —
    e.g. ['Master'] or ['Master', 'PhD']), it's authoritative: no regex
    guessing needed, the employer already told us which degrees qualify.
    Falls back to free-text regex otherwise (KLA/AMAT have no such tag)."""
    if structured_degree_levels:
        levels_l = [d.lower() for d in structured_degree_levels]
        if any("master" in d or "碩士" in d for d in levels_l):
            return ("pass", None)
        if any("phd" in d or "doctor" in d or "博士" in d for d in levels_l):
            return ("reject", "structured job_degrees 只列博士：%s" % structured_degree_levels)
        return ("pass", None)  # bachelor-only or unrecognized tag — Master's still clears it

    for m in _DEGREE_RE.finditer(jd_text):
        window = jd_text[max(0, m.start() - 80): m.end() + 80]
        window_l = window.lower()
        if _DEGREE_REQUIRED_RE.search(window_l) and not _DEGREE_SOFTEN_RE.search(window_l):
            return ("reject", "JD 疑似要求博士：…%s…" % window.strip()[:120])
    return ("pass", None)


def education_accept_screen(title, jd_text, criteria, structured_backgrounds=None):
    """The accept gate — replaces the old title-based accept_titles check.
    When structured_backgrounds is available (ASML's job_educational_
    backgrounds tag), match against that directly — it's the employer's own
    clean tag list, not prose to regex-guess against. Otherwise falls back to
    matching accept_education_fields against title+JD combined (KLA/AMAT have
    no structured tag, so this inherits the same recall risk as before: a JD
    saying just 'MSc in technical field' won't match a specific field name)."""
    if structured_backgrounds:
        hit = _matches_any(" ".join(structured_backgrounds), criteria.get("accept_education_fields", []))
        if hit:
            return ("accept", "accept_education_fields(structured): %s" % hit)
        return ("reject", "structured job_educational_backgrounds 沒對到: %s" % structured_backgrounds)

    hit = _matches_any(title + "\n" + jd_text, criteria.get("accept_education_fields", []))
    if hit:
        return ("accept", "accept_education_fields(freetext): %s" % hit)
    return ("reject", "no accept_education_fields match in title or JD")


def new_grad_flag(title, jd_text, prefer_groups):
    hit = _matches_any(title, prefer_groups) or _matches_any(jd_text[:2000], prefer_groups)
    return bool(hit)


def strip_html(raw):
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Workday adapter
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Network retry
# ---------------------------------------------------------------------------
#
# WHY: every ATS endpoint here is a public career API behind a CDN, and they
# intermittently stall — KLA timed out on 2026-08-18 and again on 2026-08-20,
# ASML on 2026-08-20, each time self-healing by the next run. Without a retry
# a single stalled read aborts that whole company's scan for the run, so a new
# posting can sit unseen for half a day. urllib has no built-in retry, so one
# lives here and every outbound request in this file goes through it.
#
# Reads are idempotent GET/POST searches — replaying them is always safe.

HTTP_ATTEMPTS = 3
HTTP_BACKOFF_SECONDS = (3, 9)  # waited after attempt 1 and attempt 2


def _urlopen_retry(req, timeout=45):
    """urlopen with bounded exponential backoff on transient network faults.

    Retries timeouts, connection resets, and 5xx/429. Does NOT retry other 4xx —
    a 404 means the endpoint config is wrong and retrying just hides it."""
    last = None
    for attempt in range(1, HTTP_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504):
                raise
            last = e
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            # URLError wraps the socket error; a genuine DNS failure retries too
            # (cheap) rather than being teased apart from a transient one.
            last = e
        if attempt < HTTP_ATTEMPTS:
            wait = HTTP_BACKOFF_SECONDS[attempt - 1]
            print("  retry %d/%d after %s: %s (waiting %ds)"
                  % (attempt, HTTP_ATTEMPTS - 1, getattr(req, "full_url", "?"),
                     type(last).__name__, wait), file=sys.stderr)
            time.sleep(wait)
    raise last


def _http_post_json(url, payload, timeout=45):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": UA},
    )
    return json.loads(_urlopen_retry(req, timeout))


def _http_get_json(url, timeout=45):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": UA})
    return json.loads(_urlopen_retry(req, timeout))


def _location_term_hit(descriptor, criteria):
    d = (descriptor or "").lower()
    for group in criteria.get("allow_locations", []):
        for term in group:
            if term and term in d:
                return True
    return False


def workday_list(cfg, criteria, log, delay):
    """Two-step facet probe (country, then county-level within that country)
    so location filtering happens server-side, before any job is fetched.
    Facet parameter NAMES differ per Workday tenant (verified: KLA uses
    State_Province_Region, AMAT uses State_Region_Province) so they're
    discovered at runtime, never hardcoded."""
    base = "%s/wday/cxs/%s/%s" % (cfg["host"], cfg["tenant"], cfg["site"])

    probe = _http_post_json(base + "/jobs", {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
    applied = {}
    for f in probe.get("facets", []):
        for v in f.get("values", []):
            if v.get("descriptor") == "Taiwan":
                applied[f["facetParameter"]] = [v["id"]]

    search_text = ""
    if applied:
        probe2 = _http_post_json(base + "/jobs", {"appliedFacets": applied, "limit": 1, "offset": 0, "searchText": ""})
        for f in probe2.get("facets", []):
            if f["facetParameter"] in applied:
                continue
            ids = [v["id"] for v in f.get("values", []) if _location_term_hit(v.get("descriptor"), criteria)]
            if ids:
                applied[f["facetParameter"]] = ids
    else:
        search_text = "Taiwan"
        log("WARN: %s — 找不到 Taiwan 國別 facet，退回 searchText 模式" % cfg.get("tenant"))

    # `total` is only correct on the offset=0 response (later pages report 0) —
    # AND once offset runs past the real result count, Workday doesn't return an
    # empty page, it silently WRAPS BACK to page 0's content (verified live: at
    # offset=100 with a true total of 83, both `total` and the job list matched
    # offset=0 exactly). So "stop when the page is empty" never fires — capture
    # `total` from the first page only and use that as the sole loop bound.
    time.sleep(delay)
    first_page = _http_post_json(base + "/jobs", {
        "appliedFacets": applied, "limit": 20, "offset": 0, "searchText": search_text,
    })
    total = first_page.get("total", 0)
    out = list(first_page.get("jobPostings", []))
    offset = 20
    while offset < total and offset < 600:  # 600 = safety cap regardless of total
        time.sleep(delay)
        page = _http_post_json(base + "/jobs", {
            "appliedFacets": applied, "limit": 20, "offset": offset, "searchText": search_text,
        })
        posts = page.get("jobPostings", [])
        if not posts:  # defensive fallback if a tenant's `total` proves unreliable
            break
        out.extend(posts)
        offset += 20
    return out


def workday_detail(cfg, posting, delay):
    # externalPath already starts with "/job/..." — do NOT insert an extra
    # "/job" segment here, that produces "/job/job/..." which Workday's edge
    # rejects with 406 (verified live; not a rate-limit/bot-mitigation issue).
    external_path = posting["externalPath"]
    url = "%s/wday/cxs/%s/%s%s" % (cfg["host"], cfg["tenant"], cfg["site"], external_path)
    time.sleep(delay)
    return _http_get_json(url)["jobPostingInfo"]


def workday_req_id(posting):
    bullets = posting.get("bulletFields") or []
    if bullets:
        return str(bullets[0])
    # fallback: last _-separated token of externalPath
    path = posting.get("externalPath", "")
    return path.rsplit("_", 1)[-1] or path


_MULTI_LOCATION_SUMMARY_RE = re.compile(r"^\d+\s+locations?$", re.I)


def workday_list_locations(posting):
    """locationsText is only a REAL location for single-site postings — for
    multi-site ones Workday collapses it to a vague count ('3 Locations'),
    which would silently false-reject a real match if trusted (the actual
    breakdown only exists in the per-job detail fetch). Returns None (means
    'unknown, must fetch detail') for that ambiguous case; verified live
    2026-08-12 this pre-check currently saves zero KLA fetches (every single-
    location Taiwan posting already matches allow_locations) but stays safe
    if that ever changes."""
    lt = posting.get("locationsText", "")
    if _MULTI_LOCATION_SUMMARY_RE.match(lt.strip()):
        return None
    return [lt] if lt else None


# ---------------------------------------------------------------------------
# Eightfold.ai adapter (careers.<employer>.com, "pcsx" API — confirmed working
# unauthenticated for Applied Materials 2026-08-11; same platform family as
# Lam Research per sources.json's disabled note). AMAT's Workday `External`
# site (the other adapter above) turned out to be a partial, semi-hidden
# mirror — missing an entire business unit's worth of Tainan/Display postings
# that only Eightfold (the site actually linked from appliedmaterials.com)
# carries, verified by diffing req IDs between the two: 35 of 100 real Taiwan
# postings were absent from the Workday side. Eightfold is now the sole AMAT
# source; the Workday adapter function above is kept only because KLA uses it.
# ---------------------------------------------------------------------------

def eightfold_list(cfg, criteria, log, delay):
    """Eightfold's own `location=` search param already narrows server-side,
    but (unlike Workday's facet IDs) it's a fuzzy text match, not a strict
    country/county filter — local location_screen() still does the real
    filtering. Pagination is 10/page; unlike Workday, `count` is accurate on
    every page and offset past the end returns an empty list (verified live:
    no wraparound bug here), but the same defensive empty-page break is kept
    anyway since that cost nothing and Workday looked equally trustworthy
    until it wasn't."""
    base = "https://%s/api/pcsx/search" % cfg["host"]

    def _page(start):
        url = "%s?domain=%s&query=&location=%s&start=%d&sort_by=match&filter_include_remote=0" % (
            base, cfg["domain"], cfg.get("search_location", "Taiwan"), start)
        return _http_get_json(url)["data"]

    first = _page(0)
    total = first.get("count", 0)
    out = list(first.get("positions", []))
    offset = 10
    while offset < total and offset < 500:  # 500 = safety cap regardless of total
        time.sleep(delay)
        page = _page(offset)
        positions = page.get("positions", [])
        if not positions:
            break
        out.extend(positions)
        offset += 10

    normalized = []
    for p in out:
        posted_iso = None
        ts = p.get("postedTs")
        if ts:
            posted_iso = date.fromtimestamp(ts).isoformat()
        normalized.append({
            "title": p.get("name", ""),
            "externalPath": p.get("positionUrl", ""),
            "postedOn": posted_iso,
            "locationsText": ", ".join(p.get("locations") or []),
            "locations": p.get("locations") or [],
            "displayJobId": p.get("displayJobId") or str(p.get("id", "")),
        })
    return normalized


_LDJSON_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


def eightfold_detail(cfg, posting, delay):
    """Unlike Workday, there's no separate JSON detail endpoint — the full JD
    is server-rendered into a schema.org JobPosting <script type="application/
    ld+json"> block on the job page itself (verified live: no JS-only client
    fetch needed, plain GET+regex works). jobLocation in that block is only
    the single primary location, so the full multi-location list comes from
    the posting dict captured at list() time instead — that's why this
    adapter's detail() needs the whole posting, not just a path string."""
    url = "https://%s%s?domain=%s" % (cfg["host"], posting["externalPath"], cfg["domain"])
    time.sleep(delay)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    html_raw = _urlopen_retry(req).decode("utf-8", errors="replace")
    m = _LDJSON_RE.search(html_raw)
    ld = json.loads(m.group(1)) if m else {}

    locations = posting.get("locations") or []
    jd_text = re.sub(r"\\([.\-])", r"\1", ld.get("description", ""))  # undo markdown's "1\." escaping
    return {
        "location": locations[0] if locations else "",
        "additionalLocations": locations[1:],
        "externalUrl": ld.get("url") or url,
        "jobDescription": jd_text,
        "startDate": (ld.get("datePosted") or "")[:10] or None,
    }


def eightfold_req_id(posting):
    return posting["displayJobId"]


def eightfold_list_locations(posting):
    """Unlike Workday, this is fully authoritative, not just a pre-check —
    eightfold_detail() reuses this exact same posting['locations'] list
    verbatim (see above), it never gets new location data from the network.
    So checking it before the detail fetch is exactly as accurate as after,
    and safely skips the fetch (and its JD-page GET) entirely for the ~1/3
    of postings location_screen would reject anyway (verified live
    2026-08-12: 25/68 title-surviving AMAT postings)."""
    return posting.get("locations") or None


def sitecore_discover_list(cfg, criteria, log, delay):
    """Sitecore Discover/Search widget API (discover-euc1.sitecorecloud.io) — ASML's
    careers page is Sitecore+Next.js with jobs loaded client-side via this API; found
    by capturing the browser's own fetch calls 2026-08-12 (no public docs). The
    `authorization` bearer token is a static value baked into the site's client JS
    bundle (same for every visitor, no login/cookies involved) — a public search-only
    key, not a user secret. keyphrase must be non-empty (API 400s on ""), "*" works as
    a match-all. Unlike Workday/Eightfold, the full JD is already inline in the list
    response (posting["jobDescription"]) — no separate per-job detail fetch needed."""
    url = "https://%s/discover/v2/%s" % (cfg["host"], cfg["tenant_id"])
    headers = {
        "Content-Type": "application/json", "Accept": "application/json",
        "authorization": cfg["auth_token"], "User-Agent": UA,
    }

    def _page(offset):
        payload = {
            "context": {"locale": {"country": cfg.get("locale_country", "us"), "language": cfg.get("locale_language", "en")}},
            "widget": {"items": [{
                "entity": "content", "rfk_id": cfg["rfk_id"],
                "search": {
                    "limit": 25, "offset": offset, "content": {},
                    "filter": {"type": "and", "filters": [
                        {"name": "job_country", "values": [cfg.get("search_country", "Taiwan")], "type": "anyOf"},
                    ]},
                    "query": {"keyphrase": "*", "operator": "and"},
                    "sort": {"value": [{"name": "sorting_relevance"}]},
                },
            }]},
        }
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        return json.loads(_urlopen_retry(req))["widgets"][0]

    first = _page(0)
    total = first.get("total_item", 0)
    out = list(first.get("content") or [])
    offset = 25
    while offset < total and offset < 500:  # 500 = safety cap regardless of total
        time.sleep(delay)
        page = _page(offset)
        items = page.get("content") or []
        if not items:
            break
        out.extend(items)
        offset += 25

    normalized = []
    for j in out:
        loc = j.get("job_location") or j.get("job_city") or ""
        normalized.append({
            "title": j.get("name", ""),
            "externalPath": j.get("url", ""),
            "postedOn": (j.get("job_date_posted") or "")[:10] or None,
            "locationsText": loc,
            "locations": [loc] if loc else [],
            "displayJobId": j.get("job_id") or j.get("id", ""),
            "jobDescription": j.get("description", ""),
            # ASML tags each posting with its own structured facets — far more
            # reliable than regex-guessing against free JD prose (which often
            # just says "MSc in technical field" without naming a major).
            # Workday/Eightfold have no equivalent, so these stay empty there
            # and process_company() falls back to free-text screening.
            "educationBackgrounds": j.get("job_educational_backgrounds") or [],
            "degreeLevels": j.get("job_degrees") or [],
            "experienceLevels": j.get("job_experience_levels") or [],
        })
    return normalized


def sitecore_discover_detail(cfg, posting, delay):
    return {
        "location": posting["locations"][0] if posting["locations"] else "",
        "additionalLocations": posting["locations"][1:],
        "externalUrl": posting["externalPath"],
        "jobDescription": posting.get("jobDescription", ""),
        "startDate": posting.get("postedOn"),
        "educationBackgrounds": posting.get("educationBackgrounds") or [],
        "degreeLevels": posting.get("degreeLevels") or [],
        "experienceLevels": posting.get("experienceLevels") or [],
    }


def sitecore_discover_req_id(posting):
    return posting["displayJobId"]


ADAPTERS = {
    "workday": {"list": workday_list, "detail": workday_detail, "req_id": workday_req_id,
                "list_locations": workday_list_locations},
    "eightfold": {"list": eightfold_list, "detail": eightfold_detail, "req_id": eightfold_req_id,
                  "list_locations": eightfold_list_locations},
    # sitecore_discover has no list_locations entry: its detail() already makes
    # zero network calls (JD is inline from list()), so there's nothing to skip.
    "sitecore_discover": {"list": sitecore_discover_list, "detail": sitecore_discover_detail, "req_id": sitecore_discover_req_id},
}


# ---------------------------------------------------------------------------
# ledger
# ---------------------------------------------------------------------------

def load_ledger():
    if not os.path.exists(LEDGER_PATH):
        return {"schema": 1, "companies": {}, "jobs": {}}
    with open(LEDGER_PATH, encoding="utf-8") as f:
        return json.load(f)


THIN_STATES = ("rejected", "skipped")
THIN_KEEP = ("state", "title", "first_seen", "state_changed", "last_seen")


def _screen_cause(screen):
    """Which of the five screening rules actually rejected a posting. The screen
    block never flags the deciding rule directly, so it's inferred from which
    keys are present: title_reject_screen bails before any location/education
    work, location before education, and degree/years carry their own verdict."""
    for field in ("years", "degree"):
        sub = screen.get(field)
        if isinstance(sub, dict) and sub.get("verdict") == "reject":
            return field
    if "education_rule" in screen and "location_rule" in screen:
        return "education_rule"
    if "location_rule" in screen and "title_rule" not in screen:
        return "location_rule"
    if "title_rule" in screen:
        return "title_rule"
    return None


def thin_job(job):
    """rejected/skipped jobs are dead weight stored in full: render() never draws
    them, and backfill re-fetches from the live ATS rather than reading cached
    fields, so nothing outside these keys is ever read back. Trimmed 2026-08-25
    on the user's call (494 KB -> 188 KB) because the file had become too big to
    read. state/first_seen/state_changed must survive — _write_record reads all
    three as bare subscripts and would KeyError without them."""
    if job.get("state") not in THIN_STATES:
        return job
    screen = job.get("screen") or {}
    verdict = screen.get("verdict")
    if verdict == "unsure":  # retired 2026-08-21, see cmd_mark's valid_states
        verdict = "pass"
    thin_screen = {"verdict": verdict}
    if verdict == "reject":
        cause = _screen_cause(screen)
        if cause:
            thin_screen["rule"] = cause
    out = {k: job[k] for k in THIN_KEEP if k in job}
    out["screen"] = thin_screen
    return out


def save_ledger(ledger):
    if os.path.exists(LEDGER_PATH):
        with open(LEDGER_PATH, encoding="utf-8") as f:
            backup = f.read()
        with open(LEDGER_BAK_PATH, "w", encoding="utf-8") as f:
            f.write(backup)
    tmp_path = LEDGER_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        payload = dict(ledger)
        payload["jobs"] = {k: thin_job(v) for k, v in ledger["jobs"].items()}
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp_path, LEDGER_PATH)


def retitle_existing_jobs(ledger, criteria, log):
    """Free (no-network) re-check: the per-job screening verdict is otherwise
    cached forever once a job is first seen (cost control — avoids re-fetching
    JD text every run). Title is already stored in the ledger, so re-applying
    title_reject_screen on every run is free and lets reject_titles edits take
    effect immediately for previously-seen jobs too, not just new postings
    going forward. Can't retroactively apply accept_education_fields changes
    this way since JD text isn't cached in the ledger — that needs a full
    re-scan (re-fetching detail), not just this free title recheck."""
    changed = 0
    for job in ledger["jobs"].values():
        if job["state"] not in ("candidate",):
            continue
        verdict, reason = title_reject_screen(job["title"], criteria)
        if verdict == "reject":
            job["state"] = "rejected"
            job["state_changed"] = TODAY
            job["screen"]["verdict"] = "reject"
            job["screen"]["title_rule"] = reason
            log("RE-REJECT %s %s — title (criteria 更新後重篩): %s" % (job["company_id"], job["title"], reason))
            changed += 1
    return changed


def make_job_record(company, req_id, title, locations, posted_date, url, external_path, adapter, state, screen, grad_flag):
    return {
        "company_id": company["id"], "company": company["name"], "req_id": req_id,
        "title": title, "locations": locations, "posted_date": posted_date,
        "url": url, "external_path": external_path, "adapter": adapter, "new_grad_flag": grad_flag,
        "state": state,
        "first_seen": TODAY, "last_seen": TODAY, "state_changed": TODAY,
        "selected_date": None, "applied_date": None, "skipped_date": None,
        "screen": screen,
        "attempts": [], "note": "",
        "progress": None, "progress_changed": None,
    }


# ---------------------------------------------------------------------------
# per-company processing
# ---------------------------------------------------------------------------

def process_company(company, criteria, ledger, log, delay, force=False):
    """force=True re-screens jobs already in the ledger (used by the backfill
    command to apply criteria changes retroactively) instead of just bumping
    last_seen. applied/selected/skipped/closed jobs are user-committed state
    and are never touched even under force. A forced re-screen preserves the
    original first_seen/attempts/note and only bumps state_changed if the
    verdict actually flips, so re-running backfill repeatedly is idempotent."""
    cid = company["id"]
    stats = {"scanned": 0, "passed": 0, "title_reject": 0, "location_reject": 0,
              "education_reject": 0, "years_degree_reject": 0, "status": None, "error": None,
              "new_jobs": []}

    if not company.get("enabled") or company.get("adapter") not in ADAPTERS:
        stats["status"] = "disabled"
        return stats

    adapter = ADAPTERS[company["adapter"]]
    try:
        postings = adapter["list"](company["config"], criteria, log, delay)
        stats["scanned"] = len(postings)

        for p in postings:
            req_id = adapter["req_id"](p)
            key = "%s-%s" % (cid, req_id)

            existing = ledger["jobs"].get(key)
            if existing:
                existing["last_seen"] = TODAY
                if not force or existing["state"] in ("applied", "selected", "skipped", "closed"):
                    continue
                # force=True and state is rejected/candidate — fall
                # through to re-screen below instead of skipping.

            def _write_record(new_rec):
                if existing is not None:
                    new_rec["first_seen"] = existing["first_seen"]
                    new_rec["attempts"] = existing.get("attempts", [])
                    new_rec["note"] = existing.get("note", "")
                    if new_rec["state"] == existing["state"]:
                        new_rec["state_changed"] = existing["state_changed"]
                    else:
                        log("RE-SCREEN %s %s — %s → %s" % (cid, new_rec["title"], existing["state"], new_rec["state"]))
                ledger["jobs"][key] = new_rec

            title = p.get("title", "")
            t_verdict, t_reason = title_reject_screen(title, criteria)
            if t_verdict == "reject":
                stats["title_reject"] += 1
                log("REJECT %s %s — title: %s" % (cid, title, t_reason))
                _write_record(make_job_record(
                    company, req_id, title, [p.get("locationsText", "")], p.get("postedOn"),
                    None, p.get("externalPath"), company["adapter"], "rejected",
                    {"verdict": "reject", "title_rule": t_reason, "screened_on": TODAY}, False,
                ))
                continue

            # Pre-fetch location check: skip the detail fetch entirely when the
            # adapter already has unambiguous location data at list() time
            # (Eightfold always; Workday only for single-site postings — see
            # each adapter's list_locations() docstring for why "unambiguous"
            # matters here).
            list_locations_fn = adapter.get("list_locations")
            list_locs = list_locations_fn(p) if list_locations_fn else None
            if list_locs is not None:
                pre_l_verdict, pre_l_reason = location_screen(list_locs, criteria)
                if pre_l_verdict == "reject":
                    stats["location_reject"] += 1
                    log("REJECT %s %s — location (pre-fetch): %s" % (cid, title, pre_l_reason))
                    _write_record(make_job_record(
                        company, req_id, title, list_locs, p.get("postedOn"),
                        None, p.get("externalPath"), company["adapter"], "rejected",
                        {"verdict": "reject", "location_rule": pre_l_reason, "screened_on": TODAY}, False,
                    ))
                    continue

            detail = adapter["detail"](company["config"], p, delay)
            locations = [detail.get("location", "")] + list(detail.get("additionalLocations") or [])
            url = detail.get("externalUrl") or p.get("externalPath")
            jd_text = strip_html(detail.get("jobDescription", ""))
            # ASML-only structured tags (empty for Workday/Eightfold) — see
            # education_accept_screen/degree_screen docstrings for why these
            # are preferred over regex-guessing against free JD prose.
            education_backgrounds = detail.get("educationBackgrounds") or []
            degree_levels = detail.get("degreeLevels") or []
            experience_levels = detail.get("experienceLevels") or []

            l_verdict, l_reason = location_screen(locations, criteria)
            if l_verdict == "reject":
                stats["location_reject"] += 1
                log("REJECT %s %s — location: %s" % (cid, title, l_reason))
                _write_record(make_job_record(
                    company, req_id, title, locations, detail.get("startDate"), url,
                    p.get("externalPath"), company["adapter"], "rejected",
                    {"verdict": "reject", "location_rule": l_reason, "screened_on": TODAY}, False,
                ))
                continue

            e_verdict, e_reason = education_accept_screen(title, jd_text, criteria, education_backgrounds)
            if e_verdict == "reject":
                stats["education_reject"] += 1
                log("REJECT %s %s — education: %s" % (cid, title, e_reason))
                _write_record(make_job_record(
                    company, req_id, title, locations, detail.get("startDate"), url,
                    p.get("externalPath"), company["adapter"], "rejected",
                    {"verdict": "reject", "location_rule": l_reason, "education_rule": e_reason, "screened_on": TODAY}, False,
                ))
                continue

            years_source_text = " ".join(experience_levels) if experience_levels else jd_text
            years_v, years_ev = years_screen(years_source_text, criteria["_scalars"])
            degree_v, degree_ev = degree_screen(jd_text, criteria["_scalars"], degree_levels)
            grad_flag = new_grad_flag(title, jd_text, criteria.get("prefer_flags", [])) or \
                p.get("workerSubType") == "New College Grad"

            screen = {
                "verdict": None, "location_rule": l_reason, "education_rule": e_reason,
                "years": {"verdict": years_v, "evidence": years_ev},
                "degree": {"verdict": degree_v, "evidence": degree_ev},
                "screened_on": TODAY,
            }

            if years_v == "reject" or degree_v == "reject":
                stats["years_degree_reject"] += 1
                screen["verdict"] = "reject"
                log("REJECT %s %s — years/degree: %s" % (cid, title, years_ev if years_v == "reject" else degree_ev))
                state = "rejected"
            else:
                state = "candidate"
                screen["verdict"] = "pass"
                stats["passed"] += 1
                # Captured for the run report (log + notification email share
                # one string — see build_run_report). Only brand-new postings
                # reach here on a discover run: anything already in the ledger
                # was `continue`d above.
                stats["new_jobs"].append({
                    "key": key, "title": title,
                    "location": next((l for l in locations if l), "—"),
                })

            _write_record(make_job_record(
                company, req_id, title, locations, detail.get("startDate"), url,
                p.get("externalPath"), company["adapter"], state, screen, grad_flag,
            ))

        stats["status"] = "ok"
    except Exception as e:  # noqa: BLE001 — one company's failure must not abort the run
        stats["status"] = "error"
        stats["error"] = "%s: %s" % (type(e).__name__, e)
        log("ERROR %s — %s" % (cid, stats["error"]))

    return stats


def update_company_health(ledger, cid, stats):
    meta = ledger.setdefault("companies", {}).setdefault(
        cid, {"last_scan_date": None, "last_count": None, "consecutive_zero_days": 0, "last_error": None})
    if stats["status"] == "ok":
        if meta.get("last_scan_date") != TODAY:
            meta["consecutive_zero_days"] = 0 if stats["scanned"] > 0 else meta.get("consecutive_zero_days", 0) + 1
            meta["last_scan_date"] = TODAY
        meta["last_count"] = stats["scanned"]
        meta["last_error"] = None
    elif stats["status"] == "error":
        meta["last_scan_date"] = TODAY
        meta["last_error"] = stats["error"]
    return meta


# ---------------------------------------------------------------------------
# render: ledger.json -> today-jobs.md
# ---------------------------------------------------------------------------

def _weekday_zh(d):
    return WEEKDAYS_ZH[d.weekday()]


def _esc_cell(text):
    """GFM table cells break on a raw '|' (splits into extra columns) — real
    job titles do contain literal '|' (e.g. AMAT's "地點: 新竹 | 大量招募中"),
    so every free-text cell must be escaped before going in a table row."""
    return str(text).replace("|", "\\|").replace("\n", " ")


_TABLE_HEADER = "| ✓ | Key | 職稱 | 公司 | 地點 | 上架 |"
_TABLE_DIVIDER = "|---|---|---|---|---|---|"


def _fmt_row(key, job):
    locs = job["locations"] or []
    # Prefer the location that actually matched allow_locations (recorded in
    # screen.location_rule as "<loc> ~ <term>") over locations[0] — for
    # multi-site global reqs, Workday/Eightfold's own "primary" location can
    # be a non-Taiwan site while a later entry in the list is what qualified
    # the posting, and showing that non-Taiwan site first reads as a screening
    # bug even though the filter itself was correct.
    matched_loc = None
    location_rule = (job.get("screen") or {}).get("location_rule") or ""
    if " ~ " in location_rule:
        candidate = location_rule.split(" ~ ", 1)[0]
        if candidate in locs:
            matched_loc = candidate
    loc_full = matched_loc or (locs[0] if locs else "地點未知")
    city = loc_full.split(",")[0].strip()  # "Hsinchu,TWN" / "Hsinchu, Taiwan" → "Hsinchu"
    grad = " 🎓" if job.get("new_grad_flag") else ""
    posted = job.get("posted_date") or "?"

    # 2026-08-12: neither list-item nor table-cell checkboxes render as truly
    # clickable in the user's editor — back to table (readability wins since
    # the checkbox click either way is just manual text-editing `[ ]`→`[x]`).
    title_text = _esc_cell(job["title"]) + grad
    title_cell = "[%s](%s)" % (title_text, job["url"]) if job.get("url") else title_text

    return "| [ ] | `%s` | %s | %s | %s | %s |" % (
        key, title_cell, _esc_cell(job["company"]), _esc_cell(city), posted)


def render(ledger, company_stats=None):
    jobs = ledger["jobs"]

    candidates_new = [(k, j) for k, j in jobs.items() if j["state"] == "candidate" and j["first_seen"] == TODAY]
    candidates_old = [(k, j) for k, j in jobs.items() if j["state"] == "candidate" and j["first_seen"] != TODAY]
    selected = [(k, j) for k, j in jobs.items() if j["state"] == "selected"]

    # Newest posting first (职缺 posted_date, the "上架" date shown on each row),
    # not first_seen — a job with no posted_date sorts last within its section.
    for lst in (candidates_new, candidates_old, selected):
        lst.sort(key=lambda kv: kv[1].get("posted_date") or "", reverse=True)

    today_d = date.fromisoformat(TODAY)
    lines = ["# 今日職缺 — %s（週%s）" % (TODAY, _weekday_zh(today_d)), ""]

    warnings = []
    if company_stats:
        for cid, meta in ledger.get("companies", {}).items():
            if meta.get("consecutive_zero_days", 0) >= 2:
                warnings.append("⚠️ **%s 已連續 %d 天掃到 0 筆 — 可能是端點壞了，不是真的沒新職缺，去看 sources.json 和 logs。**"
                                 % (cid, meta["consecutive_zero_days"]))
    if warnings:
        lines.extend(warnings)
        lines.append("")

    any_jobs = any([candidates_new, candidates_old, selected])
    if not any_jobs:
        lines.append("今天沒有新的符合職缺。")
        lines.append("")
    else:
        lines += [
            "**怎麼用**：想投的 → 把 `[ ]` 改成 `[x]`。確定不投 → 改成 `[-]`。",
            "存檔後跟 Claude 說「投我勾的職缺」。**沒動過的，明天會再出現。**",
            "⚠️ 勾選 ≠ 送出。每一筆送出前 Claude 都會先給你看填好的表單，你說 yes 才會按 Submit。",
            "", "---", "",
        ]

        def _section(title, rows):
            lines.append("## %s — %d 筆" % (title, len(rows)))
            lines.append("")
            if rows:
                lines.append(_TABLE_HEADER)
                lines.append(_TABLE_DIVIDER)
                for k, j in rows:
                    lines.append(_fmt_row(k, j))
            lines.append("")

        _section("📮 待投遞（已勾選，還沒申請）", selected)
        _section("✨ 新職缺", candidates_new)
        _section("🕘 之前看過、還沒決定", candidates_old)

    if company_stats:
        lines += ["---", "## 掃描狀態 · %s" % time.strftime("%Y-%m-%d %H:%M"), ""]
        lines.append("| 公司 | 狀態 | 掃到 | 通過 | 標題不符 | 地點不符 | 教育背景不符 | 年資/學歷 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for cid, s in company_stats.items():
            name = _esc_cell(s.get("name", cid))
            if s["status"] == "disabled":
                lines.append("| %s | ⏸ 尚未設定端點 | — | — | — | — | — | — |" % name)
            elif s["status"] == "error":
                lines.append("| %s | ❌ 錯誤: %s | — | — | — | — | — | — |" % (name, _esc_cell(s["error"])))
            elif s["scanned"] == 0:
                lines.append("| %s | ⚠️ 掃到 0 筆 | 0 | 0 | 0 | 0 | 0 | 0 |" % name)
            else:
                lines.append("| %s | ✅ | %d | %d | %d | %d | %d | %d |" % (
                    name, s["scanned"], s["passed"], s["title_reject"], s["location_reject"],
                    s["education_reject"], s["years_degree_reject"]))
        lines.append("")
        n_applied = sum(1 for j in jobs.values() if j["state"] == "applied")
        n_skipped = sum(1 for j in jobs.values() if j["state"] == "skipped")
        n_pending = len(candidates_new) + len(candidates_old)
        lines.append("累計：已申請 %d · 已略過 %d · 待決定 %d" % (n_applied, n_skipped, n_pending))

    with open(TODAY_JOBS_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def render_applied(ledger):
    """Regenerate applied-jobs.md — a flat, human-facing log of every job ever
    marked 'applied', newest first. Rebuilt from ledger.json each time (source
    of truth), not hand-edited."""
    jobs = ledger["jobs"]
    applied = [(k, j) for k, j in jobs.items() if j["state"] == "applied"]
    applied.sort(key=lambda kv: kv[1].get("applied_date", ""), reverse=True)

    lines = ["# 已投遞職缺紀錄", "", "*由 ledger.json 自動產生，不要手動編輯。*", ""]
    lines.append("目前狀態要跟 Claude 說更新（例如「<職缺 key> 收到面試邀請了」），"
                  "或直接下指令：`python3 scan_jobs.py progress <key> <status>`"
                  "（status 可為 %s）。" % ", ".join(VALID_PROGRESS))
    lines.append("")
    lines.append("共 %d 筆" % len(applied))
    lines.append("")
    lines.append("| 申請日期 | 公司 | 職稱 | 目前狀態 | Key |")
    lines.append("|---|---|---|---|---|")
    for k, j in applied:
        progress = j.get("progress") or "pending"
        label = PROGRESS_LABEL.get(progress, progress)
        # a hand-added row (recruiter reached out, no posting) has no url;
        # linking to "None" is worse than not linking at all
        title_cell = _esc_cell(j.get("title", ""))
        if j.get("url"):
            title_cell = "[%s](%s)" % (title_cell, j["url"])
        lines.append("| %s | %s | %s | %s | `%s` |" % (
            j.get("applied_date", ""), _esc_cell(j.get("company", "")), title_cell, label, k))
    lines.append("")

    with open(APPLIED_JOBS_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# Table row: "| [ ] | `key` | ...". Neither list-item nor table-cell
# checkboxes render as clickable in the user's editor, so the choice between
# them is pure layout preference — table won out for readability. Only the
# checkbox cell and key cell are read; everything else on the row is
# regenerated decoration.
ROW_RE = re.compile(r"^\s*\|\s*\[([ xX\-])\]\s*\|\s*`([^`]+)`")


def ingest_today_jobs(ledger, log):
    """Parse today-jobs.md checkbox state back into ledger.json. Only the
    checkbox char and the `key` are read — everything else on the row is
    regenerated decoration, so the user can freely edit titles/notes without
    affecting state."""
    if not os.path.exists(TODAY_JOBS_PATH):
        return {"selected": 0, "skipped": 0}
    counts = {"selected": 0, "skipped": 0}
    with open(TODAY_JOBS_PATH, encoding="utf-8") as f:
        for line in f:
            m = ROW_RE.match(line)
            if not m:
                continue
            mark, key = m.group(1), m.group(2)
            job = ledger["jobs"].get(key)
            if not job:
                continue
            if mark in ("x", "X") and job["state"] not in ("selected", "applied"):
                job["state"] = "selected"
                job["state_changed"] = TODAY
                job["selected_date"] = TODAY
                log("SELECTED %s %s" % (key, job["title"]))
                counts["selected"] += 1
            elif mark == "-" and job["state"] not in ("skipped", "applied"):
                job["state"] = "skipped"
                job["state_changed"] = TODAY
                job["skipped_date"] = TODAY
                log("SKIPPED %s %s" % (key, job["title"]))
                counts["skipped"] += 1
    return counts


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

LAUNCHD_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "logs", "launchd.log")


def launchd_is_capturing():
    """True when the plist is already redirecting our stdout into launchd.log.

    Both plists pass --scheduled, and nothing else does. That flag is the whole
    signal: with it, printing IS logging and mirroring would double every
    entry; without it (any hand-started run, including one an agent runs for
    the user) nobody is capturing stdout, so the process has to write the log
    itself.

    An earlier version inferred this from sys.stdout.isatty(). That silently
    dropped every run started through a tool or a pipe — not a tty, but not
    launchd either — so those scans left no trace at all. Do not go back to
    guessing; keep the flag."""
    return "--scheduled" in sys.argv


def mirror_to_log(text):
    """Append a manual run's report to the same file launchd writes to, so the
    log is a complete record of every scan rather than only the scheduled ones.
    Best-effort: a read-only disk must not take the scan down with it."""
    try:
        os.makedirs(os.path.dirname(LAUNCHD_LOG_PATH), exist_ok=True)
        with open(LAUNCHD_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(text + "\n\n")   # blank line keeps blocks separated
    except Exception as e:
        print("mirror_to_log failed (non-fatal): %s" % e)


def build_run_report(run_ts, ingested, retitled, company_stats):
    """The single source of truth for what a discover run leaves behind.

    Deliberately returns ONE string that is both written to stdout (which
    launchd appends to logs/launchd.log) and mailed out, so the log and the
    inbox can never disagree about what happened. English-only by request
    (2026-08-21) — today-jobs.md stays Chinese, that is a UI, this is a record.

    Shape mirrors the log format that was already in use, with the old
    "today-jobs.md updated: <path>" trailer replaced by the actual postings
    found, keyed so each one can be grepped straight out of today-jobs.md."""
    lines = ["=== %s ===" % run_ts]

    if ingested["selected"] or ingested["skipped"]:
        lines.append("Checkbox intake: %d to apply, %d skipped"
                     % (ingested["selected"], ingested["skipped"]))
    if retitled:
        lines.append("Re-screened titles: %d previously-listed job(s) now rejected" % retitled)

    for stats in company_stats.values():
        lines.append("%-20s %s%s" % (stats["name"], stats["status"], stats.get("suffix", "")))

    new_jobs = [j for s_ in company_stats.values() for j in s_.get("new_jobs", [])]
    lines.append("")
    if new_jobs:
        lines.append("New candidates (%d):" % len(new_jobs))
        for j in new_jobs:
            lines.append("  %-20s %s - %s" % (j["key"], j["title"], j["location"]))
    else:
        lines.append("New candidates: none")

    return "\n".join(lines)


def send_email_notification(subject, body):
    """Emails the scan result to GMAIL_ADDRESS via Gmail SMTP, using an App
    Password stored in macOS Keychain (never touched by this codebase directly
    — see one-time setup in automation/job-search/README or the plan that
    introduced this). Best-effort, must never break the scan itself: a missing
    Keychain entry (setup not done yet) or a transient network failure is
    swallowed, not raised (launchd has no GUI stdin/stdout to fall back on)."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password",
             "-a", GMAIL_ADDRESS,
             "-s", GMAIL_APP_PASSWORD_KEYCHAIN_SERVICE,
             "-w"],
            capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            print("send_email_notification: no Keychain entry yet for "
                  "service '%s' — skipping email (see plan's one-time setup)"
                  % GMAIL_APP_PASSWORD_KEYCHAIN_SERVICE)
            return
        app_password = result.stdout.strip()

        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = GMAIL_ADDRESS

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(GMAIL_ADDRESS, app_password)
            server.send_message(msg)
    except Exception as e:
        print("send_email_notification failed (non-fatal): %s" % e)


def cmd_discover():
    run_ts = time.strftime("%Y-%m-%d %H:%M:%S")

    def log(msg):
        pass  # per-day log files (logs/YYYY-MM-DD.log) retired 2026-08-13 — REJECT/ERROR
              # detail already persists on each job's "screen" field and
              # companies.<id>.last_error in ledger.json, so nothing is lost.

    criteria = load_criteria(CRITERIA_PATH)
    validate_criteria(criteria)  # aborts (raises) before anything is touched, per risk 5

    ledger = load_ledger()
    ingested = ingest_today_jobs(ledger, log)
    save_ledger(ledger)  # persist checkbox state before touching today-jobs.md at all

    sources = load_sources()
    delay = sources.get("request_delay_seconds", 1.5)

    retitled = retitle_existing_jobs(ledger, criteria, log)

    company_stats = {}
    for company in sources["companies"]:
        prev_error = ledger.get("companies", {}).get(company["id"], {}).get("last_error")
        stats = process_company(company, criteria, ledger, log, delay)
        stats["name"] = company["name"]
        update_company_health(ledger, company["id"], stats)
        company_stats[company["id"]] = stats

        suffix = ""
        if stats["status"] == "error":
            suffix = " (%s)" % stats["error"]
        elif stats["status"] == "ok" and prev_error:
            prev_type = prev_error.split(":", 1)[0]
            suffix = " (%s: fixed %s)" % (prev_type, run_ts)
        stats["suffix"] = suffix

    save_ledger(ledger)
    render(ledger, company_stats)

    report = build_run_report(run_ts, ingested, retitled, company_stats)
    print(report + "\n")
    if not launchd_is_capturing():
        mirror_to_log(report)
    send_email_notification("Job scan complete - %s" % run_ts, report)


def cmd_backfill():
    """Re-screens every rejected/candidate job already in the ledger
    against the CURRENT job-criteria.md, instead of only screening brand-new
    postings (discover's normal, cheaper behavior). Needed because criteria
    changes never retroactively affect cached verdicts otherwise — the ledger
    was accumulated under several earlier, since-replaced versions of the
    screening logic this session (title-allowlist era, intermediate degree/
    education-field iterations), so most of its 375 "rejected" jobs are
    stale, not reflective of the final 5-condition logic. Costs a full
    detail-fetch per KLA/AMAT job still live in each ATS's current listing
    (ASML's detail() is free); applied/selected/skipped jobs are never
    touched. Safe to re-run — idempotent, see process_company's force=True
    docstring."""
    def log(msg):
        pass  # per-day log files retired 2026-08-13 — see cmd_discover's log()

    criteria = load_criteria(CRITERIA_PATH)
    validate_criteria(criteria)

    ledger = load_ledger()
    sources = load_sources()
    delay = sources.get("request_delay_seconds", 1.5)

    before_counts = Counter(j["state"] for j in ledger["jobs"].values())

    company_stats = {}
    for company in sources["companies"]:
        stats = process_company(company, criteria, ledger, log, delay, force=True)
        stats["name"] = company["name"]
        update_company_health(ledger, company["id"], stats)
        company_stats[company["id"]] = stats
        print("%-20s %s" % (company["name"], stats["status"]))

    save_ledger(ledger)
    render(ledger, company_stats)

    after_counts = Counter(j["state"] for j in ledger["jobs"].values())

    print("\nbefore: %s" % dict(before_counts))
    print("after:  %s" % dict(after_counts))
    print("today-jobs.md 已更新：%s" % TODAY_JOBS_PATH)


def cmd_render():
    ledger = load_ledger()
    render(ledger, company_stats=None)
    print("today-jobs.md 已從 ledger.json 重新產生（沒有本次掃描統計）")


def cmd_ingest():
    """Standalone version of the ingest step inside discover — reads checkbox
    state and re-renders, without touching the network. Lets the user run
    "投我勾的職缺" mid-day without waiting for tomorrow's scan."""
    log_lines = []

    def log(msg):
        log_lines.append("[%s] %s" % (time.strftime("%H:%M:%S"), msg))

    ledger = load_ledger()
    counts = ingest_today_jobs(ledger, log)
    save_ledger(ledger)
    render(ledger, company_stats=None)
    print("讀取勾選：%d 筆待投遞、%d 筆已略過" % (counts["selected"], counts["skipped"]))
    for line in log_lines:
        print(line)


def cmd_queue():
    """Print selected (checked, not-yet-applied) jobs as JSON, grouped by
    company_id, for the apply agent to consume."""
    ledger = load_ledger()
    grouped = {}
    for key, job in ledger["jobs"].items():
        if job["state"] != "selected":
            continue
        grouped.setdefault(job["company_id"], []).append({
            "key": key, "title": job["title"], "url": job["url"],
            "locations": job["locations"], "selected_date": job["selected_date"],
        })
    for company_jobs in grouped.values():
        # or "" — 2026-08 之前用 mark 勾的舊紀錄沒有 selected_date,
        # None 之間也不能比大小,排序會整個爆掉。
        company_jobs.sort(key=lambda j: j["selected_date"] or "")
    print(json.dumps(grouped, ensure_ascii=False, indent=2))


def cmd_mark():
    if len(sys.argv) < 4:
        print("usage: scan_jobs.py mark <job_key> <state> [--note \"...\"]")
        sys.exit(1)
    key, state = sys.argv[2], sys.argv[3]
    # "unsure" and "failed" retired 2026-08-21: screening only ever produced
    # candidate/rejected, nothing called mark with failed, and today-jobs.md no
    # longer renders either — a job in one would be invisible.
    valid_states = ("candidate", "selected", "applied", "skipped", "rejected", "closed")
    if state not in valid_states:
        print("invalid state %r — must be one of %s" % (state, ", ".join(valid_states)))
        sys.exit(1)
    note = ""
    if "--note" in sys.argv:
        i = sys.argv.index("--note")
        if i + 1 < len(sys.argv):
            note = sys.argv[i + 1]

    # 讀到寫之間若有掃描插進來,它的 save_ledger 會把這次的變更整份蓋掉。
    # timeout=10:一般情況下沒人跟你搶,拿鎖是瞬間的事。
    try:
        lock = ledger_lock("mark %s" % key, timeout=10)
        lock.__enter__()
    except LockBusy as e:
        sys.exit("%s —— 稍後再試" % e)

    ledger = load_ledger()
    job = ledger["jobs"].get(key)
    if not job:
        lock.__exit__()
        print("no such job in ledger: %s" % key)
        sys.exit(1)

    job["state"] = state
    job["state_changed"] = TODAY
    if state == "selected":
        # ingest_today_jobs 走 today-jobs.md 的核取記號時會蓋這個日期,mark 原本
        # 不會 —— 於是同一家公司裡混著兩種來源的勾選就會讓 cmd_queue 的排序拿
        # None 跟字串比而爆掉。dashboard 全部走 mark,不補這行等於一定踩到。
        job["selected_date"] = TODAY
    if state == "applied":
        job["applied_date"] = TODAY
        job["attempts"].append({"date": TODAY, "result": "applied", "note": note})
        job["progress"] = "pending"
        job["progress_changed"] = TODAY
    elif state == "skipped":
        job["skipped_date"] = TODAY
        job["attempts"].append({"date": TODAY, "result": "skipped", "note": note})
    if note:
        job["note"] = note

    save_ledger(ledger)
    render(ledger, company_stats=None)
    if state == "applied":
        render_applied(ledger)
    lock.__exit__()
    print("已更新 %s → %s" % (key, state))


def cmd_progress():
    """Update the post-application pipeline status of a job already marked
    'applied' (interview/offer/rejected/etc.) — separate from `state`, which
    tracks this project's own discover→apply workflow and stops mattering
    once a job is applied. `progress` tracks what happens after, at the
    employer's pace. Updated either by the user telling Claude, or
    automatically by the interview scan (automation/interview-scan/) when it
    reads an interview invitation, rejection or offer in Gmail — see steps 5.5
    and 5.7 of that automation's prompt.md. `withdrawn` is user-only."""
    if len(sys.argv) < 4:
        print("usage: scan_jobs.py progress <job_key> <status> [--note \"...\"]")
        print("valid statuses: %s" % ", ".join(VALID_PROGRESS))
        sys.exit(1)
    key, status = sys.argv[2], sys.argv[3]
    if status not in VALID_PROGRESS:
        print("invalid status %r — must be one of %s" % (status, ", ".join(VALID_PROGRESS)))
        sys.exit(1)
    note = ""
    if "--note" in sys.argv:
        i = sys.argv.index("--note")
        if i + 1 < len(sys.argv):
            note = sys.argv[i + 1]

    try:
        lock = ledger_lock("progress %s" % key, timeout=10)
        lock.__enter__()
    except LockBusy as e:
        sys.exit("%s —— 稍後再試" % e)

    ledger = load_ledger()
    job = ledger["jobs"].get(key)
    if not job:
        lock.__exit__()
        print("no such job in ledger: %s" % key)
        sys.exit(1)
    if job["state"] != "applied":
        print("warning: %s state is %r, not 'applied' — updating progress anyway" % (key, job["state"]))

    job["progress"] = status
    job["progress_changed"] = TODAY
    if note:
        job["note"] = note

    save_ledger(ledger)
    render_applied(ledger)
    lock.__exit__()
    print("已更新 %s 進度 → %s" % (key, status))


def run_with_heartbeat(fn):
    """Failure heartbeat for the unattended `discover` run.

    cmd_discover sends its success email as its LAST step, so any crash before
    that point (bad criteria file, unreadable ledger, an unhandled adapter
    exception) produces total silence — which is indistinguishable from a day
    with no new postings. That ambiguity is what let a completely dead scan go
    unnoticed for a day on 2026-08-18. Any non-zero exit now sends mail.

    Deliberately re-raises after emailing: launchd should still record the
    non-zero exit, and the traceback still belongs in logs/launchd.log."""
    try:
        fn()
    except BaseException as e:
        import traceback
        tb = traceback.format_exc()
        print(tb)
        fail_ts = time.strftime("%Y-%m-%d %H:%M:%S")
        report = (
            "=== %s ===\n"
            "scan_jobs.py discover aborted - this run did not finish.\n"
            "No success mail != no new postings today; the scan itself broke.\n\n"
            "%s: %s\n\n%s" % (fail_ts, type(e).__name__, e, tb[-1500:]))
        if not launchd_is_capturing():
            mirror_to_log(report)
        send_email_notification("\u26a0\ufe0f Job scan FAILED - %s" % fail_ts, report)
        raise


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "discover":
        # timeout=0: a discover run takes minutes, so queueing behind another
        # scan would look like a hang. Refuse and say who holds the lock.
        try:
            with ledger_lock("職缺掃描"):
                run_with_heartbeat(cmd_discover)
        except LockBusy as e:
            sys.exit(str(e))
    elif cmd == "backfill":
        cmd_backfill()
    elif cmd == "render":
        cmd_render()
    elif cmd == "ingest":
        cmd_ingest()
    elif cmd == "queue":
        cmd_queue()
    elif cmd == "mark":
        cmd_mark()
    elif cmd == "progress":
        cmd_progress()
    elif cmd == "applied":
        render_applied(load_ledger())
        print("已投遞清單已更新：%s" % APPLIED_JOBS_PATH)
    else:
        print("unknown command: %s" % cmd)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
