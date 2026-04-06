import csv

with open("too_large.csv", "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["id", "val"])
    for i in range(1000001):
        writer.writerow([i, i * 2])
