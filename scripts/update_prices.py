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
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,text/csv,*/*;q=0.8",
    "Accept-Language": "th-TH,th;q=0.9,en;q=0.8",
}

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


def get(url, tries=3, timeout=90):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # timeout / HTTP error: ลองใหม่
            last = e
            print(f"    (ครั้งที่ {i + 1}/{tries} ล้มเหลว: {e})")
            time.sleep(4 * (i + 1))
    raise last


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
        tds = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if not tds:
            continue
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", tds[0].strip())
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


def diagnose(html):
    """พิมพ์ข้อมูลช่วยหาสาเหตุเมื่ออ่านตารางไม่ได้"""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else "(ไม่มี title)"
    print(f"    [วินิจฉัย] ขนาดหน้า {len(html)} ตัวอักษร | title: {title}")
    print(f"    [วินิจฉัย] จำนวน <table>: {len(soup.find_all('table'))} | <tr>: {len(soup.find_all('tr'))}")
    dates = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", html)
    print(f"    [วินิจฉัย] พบข้อความวันที่แบบ dd/mm/yyyy ในหน้า {len(dates)} ที่ ตัวอย่าง: {dates[:3]}")
    trs = soup.find_all("tr")
    if len(trs) > 2:
        print("    [วินิจฉัย] แถวที่ 3:", [c.get_text(" ", strip=True)[:25] for c in trs[2].find_all(["th", "td"])][:12])
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    print("    [วินิจฉัย] ข้อความต้นหน้า:", text[:300])


def fetch_thai(years):
    res = {}
    for y in years:
        try:
            html = get(BANGCHAK.format(y))
            data, widths = parse_bangchak(html)
            if not data:
                diagnose(html)
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


STOOQ = "https://stooq.com/q/d/l/?s={}&i=d"


def fetch_stooq(code):
    rows = csv.reader(io.StringIO(get(STOOQ.format(code), tries=2, timeout=60)))
    head = next(rows, None)
    if not head or head[0].lower() != "date":
        raise ValueError("รูปแบบข้อมูลไม่ตรงที่คาด: " + str(head)[:80])
    pts = {}
    for r in rows:
        if len(r) >= 5 and r[0] >= f"{START_YEAR}-01-01":
            v = num(r[4])  # Close
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
    for sid, code, stooq, name in [("brent", "DCOILBRENTEU", "cb.f", "น้ำมันดิบ Brent"), ("wti", "DCOILWTICO", "cl.f", "น้ำมันดิบ WTI")]:
        for src, fn in [("FRED " + code, lambda: fetch_fred(code)), ("Stooq " + stooq + " (ราคาฟิวเจอร์ส)", lambda: fetch_stooq(stooq))]:
            try:
                pts = fn()
                if not pts:
                    raise ValueError("ไม่มีข้อมูล")
                merge(series, sid, name, "USD/บาร์เรล", "crude", False, src, pts)
                print(f"  {sid}: {len(pts)} ค่า จาก {src}")
                ok += 1
                break
            except Exception as e:
                print(f"  ! {sid}: {src} ดึงไม่สำเร็จ ({e})")

    if not ok:
        sys.exit("ดึงข้อมูลไม่ได้เลย ไม่แก้ไฟล์")
    out = {"updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "series": series}
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("เขียน", OUT, "เรียบร้อย")


if __name__ == "__main__":
    main()
