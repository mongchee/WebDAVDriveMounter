import base64
import urllib.request
from config_manager import ConfigManager

profiles = ConfigManager.get_profiles()
if not profiles:
    print("No profiles configured.")
    raise SystemExit(0)
p = profiles[0]
user = p["username"]
pwd = p["password"]
host = p["host"]

auth = base64.b64encode(f"{user}:{pwd}".encode()).decode()

req = urllib.request.Request(host, headers={"Authorization": f"Basic {auth}"}, method="PROPFIND")
req.add_header("Depth", "1")
try:
    with urllib.request.urlopen(req, timeout=15) as res:
        xml_data = res.read().decode('utf-8', errors='ignore')
        import re
        hrefs = re.findall(r'<[a-zA-Z:]*href>([^<]+)</[a-zA-Z:]*href>', xml_data)
        print("Root items:", hrefs)
except Exception as e:
    print("Error:", e)
