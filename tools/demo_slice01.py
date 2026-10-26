#!/usr/bin/env python
"""Slice 01 demo (docs/plan/slice-01-identity-rbac-approvals.md, "Demo script").

Runs against the local stack: `make reset && make up && make seed`, then

    backend/.venv/bin/python tools/demo_slice01.py            # http://localhost:8080
    backend/.venv/bin/python tools/demo_slice01.py URL        # another origin

Every identity comes from `db/seed/test_users.json`, the file `load_fixtures` writes into the
database; nothing is typed in here. The script asserts what each step must return and prints one
line per observation, so the transcript is the evidence. It creates one account, so run it on a
fresh volume (or accept that the second run proposes a duplicate address and says so).
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pyotp

ROOT = Path(__file__).resolve().parents[1]
BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080").rstrip("/") + "/api/v1"
CSRF = {"Origin": BASE.rsplit("/api", 1)[0], "X-Hub-CSRF": "1"}
SEED = json.loads((ROOT / "db/seed/test_users.json").read_text())
PASSWORD = SEED["password"]
STAFF = {entry["email"]: entry for entry in SEED["staff"]}
LEAD = STAFF["finance.lead@example.test"]
ADMIN_ONE = STAFF["administrator@example.test"]
ADMIN_TWO = STAFF["administrator.two@example.test"]
KNOWN_PHONE = SEED["customer"]["phone_e164"]
NEW_EMAIL = "demo.support@example.test"


def call(method, path, body=None, token=None, headers=None):
    """One API call; returns (status, lower-cased headers, parsed body)."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request) as response:
            return (
                response.status,
                {k.lower(): v for k, v in response.headers.items()},
                json.loads(response.read() or b"{}"),
            )
    except urllib.error.HTTPError as exc:
        return (
            exc.code,
            {k.lower(): v for k, v in exc.headers.items()},
            json.loads(exc.read() or b"{}"),
        )


def cookie(headers):
    """The refresh token and the raw Set-Cookie line."""
    raw = headers.get("set-cookie", "")
    return raw.split(";", 1)[0].split("=", 1)[1], raw


def sign_in(entry):
    """Password, then the current TOTP: the access token of a fully signed-in account."""
    status, _, body = call("POST", "/auth/staff/login", {"email": entry["email"], "password": PASSWORD})
    assert status == 200, (status, body)
    status, _, body = call(
        "POST",
        "/auth/staff/mfa/verify",
        {"mfa_token": body["data"]["mfa_token"], "totp_code": pyotp.TOTP(entry["totp_secret"]).now()},
    )
    assert status == 200, (status, body)
    return body["data"]["access_token"]


def step_up(token, entry):
    """A fresh step-up, on a TOTP step the login above did not already spend."""
    wait = 30 - (time.time() % 30) + 1
    print(f"-- waiting {wait:.0f} s for the next TOTP step")
    time.sleep(wait)
    status, _, body = call(
        "POST", "/auth/step-up", {"method": "totp", "totp_code": pyotp.TOTP(entry["totp_secret"]).now()}, token=token
    )
    assert status == 200, (status, body)
    return body["data"]["access_token"]


def solve_proof(challenge, difficulty):
    """The hashcash nonce: a digest whose top `difficulty` bits are zero."""
    nonce = 0
    while True:
        digest = hashlib.sha256(f"{challenge}.{nonce}".encode()).digest()
        if int.from_bytes(digest, "big").bit_length() <= 256 - difficulty:
            return f"{challenge}.{nonce}"
        nonce += 1


print("== 1. readiness")
status, _, body = call("GET", "/health/ready")
assert status == 200, body
print(status, body["data"]["status"], [check["name"] for check in body["data"]["checks"]])

print(f"== 2. staff login ({LEAD['email']}): password only")
status, _, body = call("POST", "/auth/staff/login", {"email": LEAD["email"], "password": PASSWORD})
assert status == 200 and body["data"]["mfa_required"], body
print(status, "mfa_required:", body["data"]["mfa_required"], "keys:", sorted(body["data"]))
print("no access token:", "access_token" not in body["data"])

print("== 3. MFA verify: token pair and cookie, no refresh token in the body")
status, headers, body = call(
    "POST",
    "/auth/staff/mfa/verify",
    {"mfa_token": body["data"]["mfa_token"], "totp_code": pyotp.TOTP(LEAD["totp_secret"]).now()},
)
assert status == 200, body
refresh, raw_cookie = cookie(headers)
print(
    status,
    "expires_in:", body["data"]["expires_in"],
    "acr:", body["data"]["principal"]["acr"],
    "refresh token in body:", "refresh_token" in body["data"],
)
print("Set-Cookie:", raw_cookie.replace(refresh, "<redacted>"))

print("== 4. refresh rotation and replay of the spent cookie")
status, headers, _ = call("POST", "/auth/refresh", headers={**CSRF, "Cookie": f"sg_refresh={refresh}"})
successor, _ = cookie(headers)
assert status == 200 and successor, status
print("rotate ->", status, "new cookie:", bool(successor))
status, _, body = call("POST", "/auth/refresh", headers={**CSRF, "Cookie": f"sg_refresh={refresh}"})
assert status == 401 and body["code"] == "refresh_token_reused", body
print("replay spent ->", status, body["code"])
status, _, body = call("POST", "/auth/refresh", headers={**CSRF, "Cookie": f"sg_refresh={successor}"})
assert status == 401, body
print("successor after reuse ->", status, body["code"])

print("== 5. the cookie route refuses a request without the CSRF header")
status, _, body = call("POST", "/auth/refresh", headers={"Cookie": f"sg_refresh={refresh}"})
assert status == 403, body
print("no X-Hub-CSRF ->", status, body["code"])

print("== 6. two administrators sign in and step up")
token_one, token_two = sign_in(ADMIN_ONE), sign_in(ADMIN_TWO)
print("both hold an access token:", len(token_one) > 100 and len(token_two) > 100)
fresh_one = step_up(token_one, ADMIN_ONE)
status, _, body = call(
    "POST",
    "/auth/step-up",
    {"method": "totp", "totp_code": pyotp.TOTP(ADMIN_TWO["totp_secret"]).now()},
    token=token_two,
)
assert status == 200, body
fresh_two = body["data"]["access_token"]
print("step-up -> 200 200, both principals now carry a fresh auth_time")

print("== 7. create a support agent: 202 and an approval, no user yet")
status, headers, body = call(
    "POST",
    "/users",
    {
        "email": NEW_EMAIL,
        "full_name": "Demo Support",
        "roles": ["support_agent"],
        "locale": "fr",
        "reason": "slice 01 demo",
    },
    token=fresh_one,
)
assert status == 202, body
approval = body["data"]["id"]
print("POST /users ->", status, "Location:", headers.get("location"), "approval:", approval)
status, _, listed = call("GET", f"/users?q={NEW_EMAIL}", token=fresh_one)
print("users before approval:", status, len(listed["data"]))

print("== 8. the maker cannot approve; the second administrator can")
status, _, body = call("POST", f"/approvals/{approval}/approve", {}, token=fresh_one)
assert status == 403 and body["code"] == "self_approval_forbidden", body
print("self approval ->", status, body["code"])
status, _, body = call("POST", f"/approvals/{approval}/approve", {}, token=fresh_two)
assert status == 200, body
print("second administrator ->", status, body["data"]["status"])
status, _, listed = call("GET", f"/users?q={NEW_EMAIL}", token=fresh_one)
print("users after approval:", status, len(listed["data"]), [user["email"] for user in listed["data"]])

print("== 9. audit trail, forward-only, with a linked hash chain")
status, _, body = call("GET", "/audit-log?stream=admin", token=fresh_one)
assert status == 200, body
print("GET /audit-log ->", status, "entries:", len(body["data"]))
for entry in body["data"]:
    print(" ", entry["seq"], entry["action"], entry["prev_hash"][:8], "->", entry["hash"][:8])

print("== 10. customer code: an unknown and a known number answer the same shape")
for phone in ("+243819999999", KNOWN_PHONE):
    status, _, challenge = call("GET", "/auth/proof-challenge")
    proof = solve_proof(challenge["data"]["challenge"], challenge["data"]["difficulty"])
    status, _, body = call(
        "POST", "/auth/customer/otp/request", {"phone_e164": phone, "proof_token": proof}
    )
    assert status == 202, body
    print(phone, "->", status, sorted(body["data"]))

print("\nAll ten demo steps passed.")
