"""Quick dashboard template render check (no server or network needed).

Verifies the map switcher markup and all three tile backends are present
in the rendered dashboard HTML.
"""

import os
import sys

# Make the project root importable when running as tests/render_test.py
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

import app as m  # noqa: E402


def main() -> None:
    client = m.app.test_client()
    with client.session_transaction() as sess:
        sess["admin"] = True

    response = client.get("/")
    html = response.data.decode()

    checks = {
        "status 200": response.status_code == 200,
        "map switcher markup": "map-switch" in html,
        "satellite default": 'setMapLayer("hybrid")' in html,
        "esri imagery layer": "World_Imagery" in html,
        "road overlay": "World_Transportation" in html,
        "place labels overlay": "World_Boundaries_and_Places" in html,
        "street layer (CARTO)": "cartocdn" in html,
        "no blocked OSM tiles": "tile.openstreetmap.org" not in html,
    }
    for name, ok in checks.items():
        print(("PASS" if ok else "FAIL"), "-", name)
    assert all(checks.values()), "template render check failed"
    print("RENDER TEST PASSED")


if __name__ == "__main__":
    main()
