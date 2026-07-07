import json
import pandas as pd
import math

with open("output.json", "r", encoding="utf-8") as f:
    data = json.load(f)

rows = []

for item in data:

    start = item.get("StartPoint", {})
    end = item.get("EndPoint", {})

    sx = start.get("X")
    sy = start.get("Y")
    sz = start.get("Z")

    ex = end.get("X")
    ey = end.get("Y")
    ez = end.get("Z")

    length = None

    if None not in [sx, sy, sz, ex, ey, ez]:
        length = math.sqrt(
            (ex - sx) ** 2 +
            (ey - sy) ** 2 +
            (ez - sz) ** 2
        )

    rows.append({
        "Id": item.get("Id"),
        "Guid": item.get("Guid"),

        "Type": item.get("Type"),
        "Direction": item.get("Direction"),
        "Name": item.get("Name"),
        "Profile": item.get("Profile"),
        "Material": item.get("Material"),

        "StartX": sx,
        "StartY": sy,
        "StartZ": sz,

        "EndX": ex,
        "EndY": ey,
        "EndZ": ez,

        "Length": length
    })

df = pd.DataFrame(rows)

df.to_csv("tekla.csv", index=False)

print("✅ CSV Created: tekla.csv")