#!/usr/bin/env python3
"""Read-only Cloudflare Worker/R2 diagnostic. Never mutates Cloudflare or prints secrets."""
import json, os, sys, urllib.request, urllib.error

TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
API = "https://api.cloudflare.com/client/v4"
WORKER = "svari-aplinka-photos"
BUCKET = "svari-aplinka-foto"
CLAIM = "__sa_pilot_guard__/one-photo-v1"

def api(path):
    request = urllib.request.Request(API + path, headers={
        "Authorization": "Bearer " + TOKEN, "Accept": "application/json"
    }, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=18) as res:
            obj = json.loads(res.read(500000).decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError("Cloudflare API HTTP %s at %s (token value hidden)" % (e.code, path.split("?")[0])) from None
    if not isinstance(obj, dict) or not obj.get("success", False):
        raise RuntimeError("Cloudflare API rejected read at %s (response hidden)" % path.split("?")[0])
    return obj

def main():
    if not TOKEN:
        print("NEPRIJUNGTA: GitHub Secret CLOUDFLARE_API_TOKEN nerastas.")
        sys.exit(2)
    ok = api("/user/tokens/verify")
    state = ok.get("result", {}).get("status")
    print("API_TOKEN: %s" % ("ACTIVE" if state == "active" else "NOT_ACTIVE"))
    if state != "active":
        sys.exit(2)
    account = ACCOUNT_ID
    if not account:
        try:
            result = api("/accounts").get("result", [])
            accounts = [a.get("id", "") for a in result if isinstance(a, dict)]
            if len(accounts) != 1:
                raise RuntimeError("expected exactly one account")
            account = accounts[0]
        except Exception:
            print("NEPRIJUNGTA: negaliu vienareikšmiškai nustatyti Cloudflare paskyros. Reikalingas GitHub Secret CLOUDFLARE_ACCOUNT_ID.")
            sys.exit(2)
    if len(account) != 32 or any(c not in "0123456789abcdef" for c in account.lower()):
        raise RuntimeError("Cloudflare account id must be 32 hexadecimal characters")
    print("ACCOUNT: validated (ID hidden)")
    base = "/accounts/%s" % account
    conf = api(base + "/workers/scripts/" + WORKER + "/settings").get("result") or {}
    bindings = conf.get("bindings") or []
    settings = {b.get("name"): b.get("text") for b in bindings if isinstance(b, dict) and b.get("type") == "plain_text"}
    for name in ("ENABLE_UPLOADS", "PILOT_ENABLED"):
        actual = settings.get(name, "unset")
        print("%s: %s" % (name, actual if actual in ("true","false","unset") else "UNEXPECTED_VALUE"))
    r2_bindings = [b for b in bindings if b.get("name") == "R2_BUCKET"]
    print("R2_BINDING: %s" % ("PRESENT" if r2_bindings else "MISSING"))
    deployments = api(base + "/workers/scripts/" + WORKER + "/deployments").get("result") or {}
    print("DEPLOYMENT: %s" % ("READ_OK" if deployments else "EMPTY"))
    objects = []
    cursor = None
    try:
        for i in range(8):
            import urllib.parse
            route = base + "/r2/buckets/" + BUCKET + "/objects"
            if cursor:
                route += "?cursor=" + urllib.parse.quote(cursor)
            response = api(route)
            group = response.get("result") or {}
            if isinstance(group, list):
                batch = group
            else:
                batch = group.get("objects", []) if isinstance(group, dict) else []
            objects.extend([item.get("key") for item in batch if isinstance(item, dict)])
            details = response.get("result_info") or {}
            cursor = details.get("cursor")
            if not details.get("is_truncated") or not cursor:
                break
        print("R2_OBJECT_LIST: READ_OK")
        print("ONE_SHOT_CLAIM: %s" % ("PRESENT — DO NOT DELETE" if CLAIM in objects else "NOT_FOUND_IN_LIST"))
        print("PHOTO_OBJECTS: %s" % sum(1 for obj in objects if obj and obj.startswith("sa/")))
        if cursor:
            print("R2_LIST_INCOMPLETE: yes, claim status may be incomplete")
    except RuntimeError as exc:
        print("R2_OBJECT_LIST: UNAVAILABLE (%s)" % str(exc))
    req = urllib.request.Request(
        "https://svari-aplinka-photos.svariaplinka-lt.workers.dev/health",
        headers={"Accept": "application/json"}, method="GET"
    )
    with urllib.request.urlopen(req, timeout=15) as res:
        body = json.loads(res.read(5000))
        print("WORKER_HEALTH: %s" % ("200 OK" if res.status == 200 and body.get("status") == "ok" else "BAD_RESPONSE"))
    if settings.get("ENABLE_UPLOADS") != "false" or settings.get("PILOT_ENABLED") != "false":
        raise RuntimeError("SAFETY_STOP: at least one upload switch is not explicitly false")
    print("PASS: read-only diagnostics, both uploads switches off. NO WRITE/UPLOAD/DEPLOY.")
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("DIAGNOSTIC_ERROR: %s" % str(e))
        sys.exit(1)
