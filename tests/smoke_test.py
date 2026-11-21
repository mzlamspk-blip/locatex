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
    visitor = requests.Session()
    r = visitor.get(f"{BASE}/share/{share_token}")
    assert r.status_code == 200 and "Continue" in r.text, "visitor page broken"
    vtoken = re.search(r'name="csrf-token"\s+content="([^"]+)"', r.text).group(1)
    r = visitor.post(f"{BASE}/record/{share_token}",
                     headers={"X-CSRF-Token": vtoken})
    assert r.status_code == 200 and r.json().get("success") is True, r.text
    print("[+] visit recorded")

    # 6. record without a CSRF token must be rejected
    r = requests.post(f"{BASE}/record/{share_token}")
    assert r.status_code == 400, "CSRF protection is missing on /record"

    # 7. unknown links 404
    assert requests.get(f"{BASE}/share/deadbeef" * 4).status_code == 404

    # 8. dashboard shows the visit for this link
    page = admin.get(f"{BASE}/").text
    assert share_token[:8] in page, "visit row missing on dashboard"
    assert "<h1>1</h1>" in page, "visit counter did not increment"
    print("[+] dashboard shows the visit")

    print("ALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
