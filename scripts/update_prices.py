#!/usr/bin/env python3
"""อัปเดต data/prices.json

- ราคาน้ำมันไทย: ตารางราคาย้อนหลังของบางจาก (ราคาปลีก กทม. ก่อนภาษีบำรุงท้องถิ่น)
- น้ำมันดิบ Brent / WTI: FRED (CSV สาธารณะ ไม่ต้องใช้ API key, ข้อมูลช้ากว่าตลาดประมาณ 1 สัปดาห์)

ใช้งาน:
  python scripts/update_prices.py            # ดึงเฉพาะปีนี้และปีก่อน + น้ำมันดิบ
  python scripts/update_prices.py --full     # ดึงย้อนหลังทุกปีตั้งแต่ START_YEAR
  python scripts/update_prices.py --inspect 2024   # ดูหัวตาราง/แถวแรกของปีนั้น (ไว้ตรวจการ map คอลัมน์)
"""
import argparse, csv, datetime as dt, io, json, pathlib, re, sys, time, urllib.request

from bs4 import BeautifulSoup

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "prices.json"
START_YEAR = 2017  # หน้าบางจากมีข้อมูลตั้งแต่ปี 2017 (พ.ศ. 2560)
BANGCHAK = "https://www.bangchak.co.th/th/oilprice/historical?year={}"
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}&cosd={}-01-01"
UA = {"User-Agent": "Mozilla/5.0 (compatible; thai-oil-tracker/1.0)"}

META = {
    "gsh95":      ("แก๊สโซฮอล์ 95", "บาท/ลิตร", "thai"),
    "gsh91":      ("แก๊สโซฮอล์ 91", "บาท/ลิตร", "thai"),
    "e20":        ("แก๊สโซฮอล์ E20", "บาท/ลิตร", "thai"),
    "e85":        ("แก๊สโซฮอล์ E85", "บาท/ลิตร", "thai"),
    "diesel":     ("ดีเซล (B7)", "บาท/ลิตร", "thai"),
    "diesel_b20": ("ดีเซล B20", "บาท/ลิตร", "thai"),
    "hi_diesel":  ("ไฮพรีเมียม ดีเซล", "บาท/ลิตร", "thai"),
    "hi_98":      ("แก๊สโซฮอล์ 98 พรีเมียม", "บาท/ลิตร", "thai"),
}
# ลำดับคอลัมน์ของตารางบางจากในปี 2026 (หัวตารางเป็นรูปภาพ จึง map ตามตำแหน่ง ตรวจเทียบกับราคาจริงแล้ว)
COLS_8 = ["hi_diesel", "diesel", "diesel_b20", "hi_98", "e85", "e20", "gsh91", "gsh95"]
# ปีที่ตารางมีจำนวนคอลัมน์ไม่ใช่ 8: เก็บเฉพาะคอลัมน์ขวาสุด (แก๊สโซฮอล์ 95) ซึ่งตรวจแล้วว่าตรงกับราคาประกาศ
# ถ้าตรวจด้วย --inspect แล้วมั่นใจลำดับของปีเก่า ให้เพิ่มรายการใน COLS_BY_WIDTH ได้
COLS_BY_WIDTH = {8: COLS_8}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def num(txt):
    t = txt.strip().replace(",", "")
    try:
        return float(t)
    except ValueError:
        return None  # "-" หรือว่าง


def parse_bangchak(html):
    """คืน {series_id: {iso_date: price}} และจำนวนคอลัมน์ที่พบ"""
    soup = BeautifulSoup(html, "html.parser")
    out, widths = {}, set()
    for tr in soup.select("table tr"):
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not tds:
            continue
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", tds[0])
        if not m:
            continue
        d, mo, y = map(int, m.groups())
        if y > 2400:  # พ.ศ. -> ค.ศ.
            y -= 543
        vals = tds[1:]
        while vals and vals[-1] == "":
            vals.pop()  # ตัดคอลัมน์ว่างท้ายแถว
        widths.add(len(vals))
        cols = COLS_BY_WIDTH.get(len(vals), [None] * (len(vals) - 1) + ["gsh95"])
        iso = f"{y:04d}-{mo:02d}-{d:02d}"
        for sid, v in zip(cols, vals):
            p = num(v)
            if sid and p is not None:
                out.setdefault(sid, {})[iso] = p
    return out, widths


def fetch_thai(years):
    res = {}
    for y in years:
        try:
            data, widths = parse_bangchak(get(BANGCHAK.format(y)))
            if widths - set(COLS_BY_WIDTH):
                print(f"  ! {y}: จำนวนคอลัมน์ {sorted(widths)} ไม่รู้จัก เก็บเฉพาะ gsh95 (ตรวจด้วย --inspect {y})")
            n = sum(len(v) for v in data.values())
            print(f"  {y}: {n} ค่า, ชุดข้อมูล {sorted(data)}")
            for sid, pts in data.items():
                res.setdefault(sid, {}).update(pts)
        except Exception as e:  # ปีไหนล้มเหลว ข้ามไปและใช้ข้อมูลเดิม
            print(f"  ! {y}: ดึงไม่สำเร็จ ({e})")
        time.sleep(1.5)
    return res


def fetch_fred(code):
    rows = csv.reader(io.StringIO(get(FRED.format(code, START_YEAR))))
    next(rows, None)
    pts = {}
    for r in rows:
        if len(r) >= 2:
            v = num(r[1])
            if v is not None:
                pts[r[0]] = v
    return pts


def merge(series, sid, name, unit, group, step, source, new_pts):
    s = series.setdefault(sid, {"points": []})
    old = {d: v for d, v in s["points"]}
    old.update(new_pts)
    pts = sorted(old.items())
    if step:  # เก็บเฉพาะวันที่ราคาเปลี่ยน
        ded, last = [], None
        for d, v in pts:
            if v != last:
                ded.append([d, v])
                last = v
        pts = ded
    else:
        pts = [[d, v] for d, v in pts]
    s.update(name=name, unit=unit, group=group, step=step, source=source, points=pts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--inspect", type=int)
    a = ap.parse_args()

    if a.inspect:
        soup = BeautifulSoup(get(BANGCHAK.format(a.inspect)), "html.parser")
        t = soup.find("table")
        print("หัวตาราง (ชื่อไฟล์รูป):", [i.get("src", "").split("FileName=")[-1] for i in t.select("thead img, tr:nth-of-type(2) img")])
        data, widths = parse_bangchak(str(soup))
        print("จำนวนคอลัมน์:", widths)
        for sid, pts in data.items():
            k = sorted(pts)[-1]
            print(sid, k, pts[k])
        return

    series = {}
    if OUT.exists():
        series = json.loads(OUT.read_text(encoding="utf-8")).get("series", {})

    this = dt.date.today().year
    years = range(START_YEAR, this + 1) if (a.full or not series) else [this - 1, this]
    ok = 0
    print("บางจาก:")
    thai = fetch_thai(years)
    for sid, pts in thai.items():
        name, unit, group = META[sid]
        merge(series, sid, name, unit, group, True, "Bangchak", pts)
        ok += 1

    print("FRED:")
    for sid, code, name in [("brent", "DCOILBRENTEU", "น้ำมันดิบ Brent"), ("wti", "DCOILWTICO", "น้ำมันดิบ WTI")]:
        try:
            pts = fetch_fred(code)
            merge(series, sid, name, "USD/บาร์เรล", "crude", False, "FRED " + code, pts)
            print(f"  {sid}: {len(pts)} ค่า")
            ok += 1
        except Exception as e:
            print(f"  ! {sid}: ดึงไม่สำเร็จ ({e})")

    if not ok:
        sys.exit("ดึงข้อมูลไม่ได้เลย ไม่แก้ไฟล์")
    out = {"updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "series": series}
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("เขียน", OUT, "เรียบร้อย")


if __name__ == "__main__":
    main()
