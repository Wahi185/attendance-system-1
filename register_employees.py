import csv, requests, sys

# 🔧 Replace this with your Render app URL
API_BASE = "https://attendance-system-1-1.onrender.com"
# 🔧 Must match the ADMIN_API_KEY you set in Render environment
ADMIN_KEY = "supersecretkey@123"

def fetch_map(endpoint):
    """Fetch departments or locations from API and return {name: id} map."""
    r = requests.get(f"{API_BASE}/api/{endpoint}", timeout=30)
    r.raise_for_status()
    return {item["name"]: item["id"] for item in r.json()}

def main():
    dept_map = fetch_map("departments")
    loc_map  = fetch_map("locations")

    with open("employees.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = row["name"].strip()
            qr   = row["qr_code_value"].strip()
            dep  = dept_map.get(row["department"].strip()) if row.get("department") else None
            loc  = loc_map.get(row["location"].strip()) if row.get("location") else None

            payload = {
                "name": name,
                "qr_code_value": qr,
                "department_id": dep,
                "location_id": loc,
            }

            r = requests.post(
                f"{API_BASE}/api/employees",
                json=payload,
                headers={"X-API-Key": ADMIN_KEY},
                timeout=30,
            )

            if r.ok:
                print(f"✅ ADDED: {name} / {qr}")
            else:
                print(f"❌ ERROR {name}: {r.status_code} {r.text}")

    print("Done.")

if __name__ == "__main__":
    if "onrender.com" not in API_BASE:
        print("❌ Please set API_BASE to your Render URL")
        sys.exit(1)
    main()
