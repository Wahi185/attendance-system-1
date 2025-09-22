import csv, os
import qrcode

INPUT = "employees.csv"
OUTDIR = "qrcodes"
os.makedirs(OUTDIR, exist_ok=True)

with open(INPUT, newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        name = row["name"].strip()
        code = row["qr_code_value"].strip()
        img = qrcode.make(code)
        filename = f"{name}_{code}.png".replace(" ", "_")
        path = os.path.join(OUTDIR, filename)
        img.save(path)
        print("Generated:", path)

print("Done. Open the 'qrcodes' folder and print the PNGs.")
