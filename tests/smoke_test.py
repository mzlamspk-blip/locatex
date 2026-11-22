"""
End-to-end smoke test for LOCATEX.

Run a fresh local server first, then execute this script:

    python tests/smoke_test.py http://127.0.0.1:5000

It verifies the full happy path plus a few negative cases:
anonymous redirect -> failed login -> successful login -> create link
-> visitor opens link -> clicks Continue -> dashboard counts the visit.
"""

import os
import re
import sys

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000"
PASSWORD = os.environ.get("ADMIN_PASSWORD", "ChangeMe123!")


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    if not match:
        raise AssertionError("CSRF token not found in page")
    return match.group(1)


def main() -> None:
    admin = requests.Session()

    # 1. anonymous visitors are redirected to the login page
    r = admin.get(BASE, allow_redirects=False)
    assert r.status_code == 302, f"/ should redirect, got {r.status_code}"

    # 2. a wrong password is rejected
    token = csrf_from(admin.get(f"{BASE}/login").text)
    r = admin.post(
        f"{BASE}/login",
        data={"password": "wrong-password", "csrf_token": token},
        allow_redirects=False,
    )
    assert "Incorrect password" in r.text, "wrong password was accepted!"

    # 3. the correct password logs in
    token = csrf_from(admin.get(f"{BASE}/login").text)
    r = admin.post(
        f"{BASE}/login",
        data={"password": PASSWORD, "csrf_token": token},
        allow_redirects=False,
    )
    assert r.status_code == 302, f"login failed with {r.status_code}"

    # 4. generate a share link
    page = admin.get(f"{BASE}/").text
    assert "Generate New Link" in page, "dashboard did not render"
    token = csrf_from(page)
    r = admin.post(f"{BASE}/create", data={"csrf_token": token},
                   allow_redirects=False)
    assert r.status_code == 302, f"create failed with {r.status_code}"

    match = re.search(r'value="[^"]*share/([0-9a-f]{32})"', admin.get(f"{BASE}/").text)
    assert match, "generated link not found on dashboard"
    share_token = match.group(1)
    print(f"[+] share link: {BASE}/share/{share_token}")

    # 5. visitor opens the link, sees the notice, clicks Continue
    #    and allows GPS in the browser prompt
    visitor = requests.Session()
    r = visitor.get(f"{BASE}/share/{share_token}")
    assert r.status_code == 200 and "Continue" in r.text, "visitor page broken"
    vtoken = re.search(r'name="csrf-token"\s+content="([^"]+)"', r.text).group(1)
    r = visitor.post(
        f"{BASE}/record/{share_token}",
        headers={"X-CSRF-Token": vtoken, "Content-Type": "application/json"},
        json={"latitude": 28.6139, "longitude": 77.2090, "accuracy": 12.5},
    )
    assert r.status_code == 200 and r.json().get("success") is True, r.text
    visit_id = r.json().get("visit_id")
    assert visit_id, "visit_id missing from record response"
    print("[+] visit recorded with GPS")

    # 5b. live tracking update while the page stays open
    r = visitor.post(
        f"{BASE}/locate/{share_token}",
        headers={"X-CSRF-Token": vtoken, "Content-Type": "application/json"},
        json={"visit_id": visit_id,
              "latitude": 28.6141, "longitude": 77.2093, "accuracy": 8.0},
    )
    assert r.status_code == 200 and r.json().get("success") is True, r.text
    print("[+] live location update accepted")

    # 5c. updates for an unknown visit must be rejected
    r = visitor.post(
        f"{BASE}/locate/{share_token}",
        headers={"X-CSRF-Token": vtoken, "Content-Type": "application/json"},
        json={"visit_id": "000000000000000000000000",
              "latitude": 1.0, "longitude": 1.0},
    )
    assert r.status_code == 404, "unknown visit_id was accepted"

    # 5d. out-of-range coordinates are rejected
    r = visitor.post(
        f"{BASE}/locate/{share_token}",
        headers={"X-CSRF-Token": vtoken, "Content-Type": "application/json"},
        json={"visit_id": visit_id, "latitude": 999, "longitude": 0},
    )
    assert r.status_code == 400, "invalid coordinates were accepted"

    # 6. record without a CSRF token must be rejected
    r = requests.post(f"{BASE}/record/{share_token}")
    assert r.status_code == 400, "CSRF protection is missing on /record"

    # 7. unknown links 404
    assert requests.get(f"{BASE}/share/deadbeef" * 4).status_code == 404

    # 8. dashboard shows the visit for this link, with GPS + map
    page = admin.get(f"{BASE}/").text
    assert share_token[:8] in page, "visit row missing on dashboard"
    total = re.search(r'<div class="card">.*?<h1>(\d+)</h1>', page, re.S)
    assert total and int(total.group(1)) >= 1, "visit counter did not increment"
    assert "Live Location Map" in page, "map section missing"
    assert "&plusmn;8 m" in page, "GPS accuracy value missing"
    print("[+] dashboard shows the visit, accuracy and map")

    print("ALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
