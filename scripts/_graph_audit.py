"""
Query the live graph REST API for each target company.
Shows current graph node + alias links (or absence thereof).
"""
import urllib.request, urllib.parse, json, sys

BASE = "https://tenderscope-kg-production.up.railway.app/api/graph"

TARGETS = [
    ("PCL", "pcl constructors westcoast"),
    ("Ledcor", "ledcor"),
    ("EllisDon", "ellisdon"),
    ("Graham", "graham construction"),
    ("Bird Construction", "bird construction"),
    ("Kindred", "kindred construction"),
    ("Omicron", "omicron construction"),
    ("Ventana", "ventana construction"),
    ("Flynn", "flynn canada"),
    ("Clark Builders", "clark builders"),
    ("Western Pacific", "western pacific"),
    ("Lafarge", "lafarge canada"),
    ("AECOM", "aecom"),
    ("Stantec", "stantec"),
    ("Omicron AEC", "omicron architecture"),
    ("Stantec Architecture", "stantec architecture"),
    ("PCL Westcoast", "pcl westcoast"),
    ("PCL Construction INc", "pcl construction westcoast"),
    ("Aecom Canada", "aecom canada"),
    ("Stantec Consulting", "stantec consulting"),
]

def get(path):
    url = BASE + path
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def search(q, limit=5):
    qs = urllib.parse.urlencode({"q": q, "limit": limit})
    return get(f"/companies/search?{qs}")

stats = get("/health")
print(f"Graph stats: {stats.get('by_kind', {})}\n")

print(f"{'Company':<25} {'UID':<16} {'kind':<16} {'aliases':<8} {'graph_name'}")
print("─" * 100)

for label, query in TARGETS:
    try:
        r = search(query, limit=3)
        results = r.get("results", [])
        if not results:
            print(f"{label:<25} {'NO GRAPH NODE':<16}")
            continue
        for hit in results:
            uid = hit["uid"]
            kind = hit["kind"]
            name = hit["name"]
            # fetch identity to get alias count
            try:
                identity = get(f"/companies/{uid}/identity")
                alias_count = len(identity.get("aliases", []))
            except Exception:
                alias_count = "?"
            print(f"{label:<25} {uid:<16} {kind:<16} {str(alias_count):<8} {name}")
            label = ""  # only print label once per company
    except Exception as exc:
        print(f"{label:<25} ERROR: {exc}")
