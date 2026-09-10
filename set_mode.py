import json, os

path = "/home/cyrus/TARS/calibration.json"
if os.path.exists(path):
    with open(path, "r") as f:
        data = json.load(f)
else:
    data = {}

data["gesture_mode"] = "4_WAY"
with open(path, "w") as f:
    json.dump(data, f, indent=2)
print("UPDATED_CALIBRATION_4_WAY")
