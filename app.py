# app.py — نسخة ملف واحد تجمع الواجهة + API + الفاحص + التصدير
# تشغيل: uvicorn app:app --reload

import json, uuid, html
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

# ===== مكتبات خارجية =====
import ezdxf
from fastapi import FastAPI, Request, UploadFile, Form
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

# للتصدير
from openpyxl import Workbook
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

# للمعاينة (PNG باستخدام matplotlib backend)
import matplotlib.pyplot as plt
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
# ===== إنشاء تطبيق FastAPI =====
app = FastAPI(
    title="DWG/IFC Checker",
    docs_url="/docs",
    redoc_url=None
)

# مسار فحص سريع للصحة (مفيد للنشر)
@app.get("/healthz")
def health():
    return {"status": "ok"}


# ================= إعداد المسارات =================
BASE = Path(__file__).parent.resolve()
UPLOADS = BASE / "uploads"
RESULTS = BASE / "results"
STATIC = BASE / "static"

for d in (UPLOADS, RESULTS, STATIC):
    d.mkdir(parents=True, exist_ok=True)

# بإمكانك وضع logo وملف التعليمات داخل مجلد static إن رغبتِ
# مثال: STATIC/logo.png و STATIC/instructions.pdf

app = FastAPI(title="DXF Checker")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
app.mount("/results", StaticFiles(directory=str(RESULTS)), name="results")

def safe_json_dump(p: Path, data: dict):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

def safe_json_load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))

def generate_preview(dxf_path: Path, out_png: Path) -> tuple[bool,str]:
    """يحاول رسم Layout الأول كصورة PNG."""
    try:
        doc = ezdxf.readfile(str(dxf_path))
        msp = doc.modelspace()
        ctx = RenderContext(doc)

        fig = plt.figure(figsize=(7.5, 7.5))  # مقاس مناسب للصندوق
        ax = fig.add_axes([0, 0, 1, 1])
        backend = MatplotlibBackend(ax=ax)
        Frontend(ctx, backend).draw_layout(msp, finalize=True)

        ax.set_aspect("equal")
        ax.axis("off")

        # fit content إن توفّر bbox
        try:
            ext = msp.bbox()  # ezdxf>=1.0
            if ext:
                (min_x, min_y, _), (max_x, max_y, _) = ext.extmin, ext.extmax
                ax.set_xlim(min_x, max_x)
                ax.set_ylim(min_y, max_y)
        except Exception:
            pass

        fig.savefig(str(out_png), dpi=140)
        plt.close(fig)
        return True, ""
    except Exception as e:
        try:
            plt.close("all")
        except Exception:
            pass
        return False, f"{type(e).__name__}: {e}"

def summarize(result: dict) -> dict:
    return {
        "rooms": result.get("total_rooms", 0),
        "passed": result.get("passed_rooms", 0),
        "failed": result.get("failed_rooms", 0),
        "min_area": result.get("min_area_m2", 2.0),
    }

# ================= منطق الفحص (dxf_check.py مدمج) =================
MIN_AREA: float = 2.0                       # الحد الأدنى لمساحة الغرفة (م²)
ROOM_LAYER_MUST_INCLUDE: str = "tent"       # الغرفة: اللاير يحتوي "tent"
SITE_BOUNDARY_LAYER: str = "حد المكتب"     # لاير حد المكتب لحساب المساحة

# لايرات العدّ (بلوكات INSERT)
EXIT_LAYER: str  = "01Arc-Site-1-4-6-Exit"          # مداخل فرعية
ENTER_LAYER: str = "01Arc-Site-1-4-5-Enter Arrow"   # مداخل رئيسية

# كلمات مفتاحية للأبواب
DOOR_KEYWORDS       = ["DOOR", "Door", "door", "A-DOOR", "D-", "BAB", "باب"]
DOOR_LAYER_KEYWORDS = ["DOOR", "A-DOOR", "Doors", "A-Doors", "باب", "A-BAB"]

EPS: float = 0.05       # ≈ 5 سم
EDGE_EPS: float = 0.40  # ≈ 40 سم

def is_closed_lwpolyline(e) -> bool:
    return e.dxftype() == "LWPOLYLINE" and (e.closed or e.get_flag_state(1))

def lwpolyline_points(e) -> List[Tuple[float, float]]:
    pts = [(float(p[0]), float(p[1])) for p in e.get_points()]
    if pts and pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts

def polygon_area(points: List[Tuple[float,float]]) -> float:
    if len(points) < 4:
        return 0.0
    s = 0.0
    for i in range(len(points)-1):
        x1, y1 = points[i]; x2, y2 = points[i+1]
        s += x1*y2 - x2*y1
    return abs(s) / 2.0

def point_on_segment(px, py, x1, y1, x2, y2, eps=EPS) -> bool:
    minx, maxx = min(x1, x2) - eps, max(x1, x2) + eps
    miny, maxy = min(y1, y2) - eps, max(y1, y2) + eps
    if not (minx <= px <= maxx and miny <= py <= maxy):
        return False
    area2 = abs((x2 - x1)*(py - y1) - (px - x1)*(y2 - y1))
    return area2 <= eps

def distance_point_to_segment(px, py, x1, y1, x2, y2) -> float:
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return ((px - x1)**2 + (py - y1)**2) ** 0.5
    t = ((px - x1)*dx + (py - y1)*dy) / (dx*dx + dy*dy)
    t = max(0.0, min(1.0, t))
    projx, projy = x1 + t*dx, y1 + t*dy
    return ((px - projx)**2 + (py - projy)**2) ** 0.5

def distance_point_to_polygon(px, py, poly) -> float:
    dmin = float("inf")
    for i in range(len(poly)-1):
        x1, y1 = poly[i]; x2, y2 = poly[i+1]
        d = distance_point_to_segment(px, py, x1, y1, x2, y2)
        if d < dmin: dmin = d
    return dmin

def point_in_or_on_polygon_or_near(x: float, y: float, poly,
                                   eps: float = EPS, edge_eps: float = EDGE_EPS) -> bool:
    # على الحد
    for i in range(len(poly)-1):
        if point_on_segment(x, y, poly[i][0], poly[i][1], poly[i+1][0], poly[i+1][1], eps):
            return True
    # داخل
    inside = False
    for i in range(len(poly)-1):
        x1, y1 = poly[i]; x2, y2 = poly[i+1]
        if ((y1 > y) != (y2 > y)):
            xinters = (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1
            if xinters >= x - eps:
                inside = not inside
    if inside:
        return True
    # قريب من الحد
    return distance_point_to_polygon(x, y, poly) <= edge_eps

def str_matches_any(s: str, keywords: List[str]) -> bool:
    ss = (s or "").upper()
    return any(k.upper() in ss for k in keywords)

def _layer_contains(s: str, needle: str) -> bool:
    return needle.lower() in (s or "").lower()

def extract_rooms(doc) -> List[Dict[str, Any]]:
    """
    يرجع الغرف من LWPOLYLINE المغلق بشرط أن اسم اللاير يحتوي "tent".
    """
    msp = doc.modelspace()
    rooms = []
    for e in msp:
        if e.dxftype() == "LWPOLYLINE" and is_closed_lwpolyline(e):
            layer = e.dxf.layer or ""
            if not _layer_contains(layer, ROOM_LAYER_MUST_INCLUDE):
                continue
            pts = lwpolyline_points(e)
            rooms.append({
                "entity": e,
                "layer": layer,
                "points": pts,
                "area": polygon_area(pts)
            })
    return rooms

def _collect_entity_points(ent) -> List[Tuple[float,float]]:
    pts: List[Tuple[float,float]] = []
    try:
        dxft = ent.dxftype()
        if dxft in ("LINE", "XLINE", "RAY"):
            s = ent.dxf.start; e = ent.dxf.end
            pts += [(float(s.x), float(s.y)), (float(e.x), float(e.y))]
        elif dxft == "LWPOLYLINE":
            for x, y, *_ in ent.get_points():
                pts.append((float(x), float(y)))
        elif dxft == "POLYLINE":
            for v in ent.vertices:
                p = v.dxf.location
                pts.append((float(p.x), float(p.y)))
        elif dxft in ("CIRCLE", "ARC"):
            c = ent.dxf.center; r = float(ent.dxf.radius)
            pts += [
                (float(c.x + r), float(c.y)),
                (float(c.x - r), float(c.y)),
                (float(c.x), float(c.y + r)),
                (float(c.x), float(c.y - r)),
            ]
        elif dxft == "ELLIPSE":
            c = ent.dxf.center
            mx, my = ent.dxf.major_axis.x, ent.dxf.major_axis.y
            pts += [(float(c.x + mx), float(c.y + my)), (float(c.x - mx), float(c.y - my))]
        elif dxft == "POINT":
            p = ent.dxf.location
            pts.append((float(p.x), float(p.y)))
    except Exception:
        pass
    return pts

def insert_test_points(ins) -> List[Tuple[float,float]]:
    pts: List[Tuple[float,float]] = []
    try:
        ip = ins.dxf.insert
        pts.append((float(ip.x), float(ip.y)))
        xs, ys = [], []
        for part in ins.virtual_entities():
            for (x, y) in _collect_entity_points(part):
                xs.append(x); ys.append(y)
        if xs and ys:
            xmin, xmax = min(xs), max(xs)
            ymin, ymax = min(ys), max(ys)
            cx, cy = (xmin + xmax)/2.0, (ymin + ymax)/2.0
            pts.extend([
                (cx, cy), (xmin, ymin), (xmin, ymax), (xmax, ymin), (xmax, ymax)
            ])
    except Exception:
        pass
    # إزالة التكرار مع تقريب
    out, seen = [], set()
    for x, y in pts:
        k = (round(x,5), round(y,5))
        if k not in seen:
            seen.add(k); out.append((x,y))
    return out

def collect_inserts_robust(doc, max_depth: int = 2) -> List[Dict[str, Any]]:
    msp = doc.modelspace()
    results: List[Dict[str, Any]] = []
    def push(ins):
        rec = {
            "name": ins.dxf.name or "",
            "layer": ins.dxf.layer,
            "test_points": insert_test_points(ins)
        }
        if not rec["test_points"]:
            ip = ins.dxf.insert
            rec["test_points"] = [(float(ip.x), float(ip.y))]
        results.append(rec)

    for e in msp.query("INSERT"):
        push(e)

    def walk(entity, depth: int):
        if depth <= 0: return
        try:
            for part in entity.virtual_entities():
                if part.dxftype() == "INSERT":
                    push(part)
                    walk(part, depth-1)
        except Exception:
            pass

    for e in msp.query("INSERT"):
        walk(e, max_depth)
    return results

def sum_closed_polyline_area_in_layer(doc, layer_name: str) -> Tuple[float, int]:
    """يجمع مساحة كل LWPOLYLINE مغلق في لاير معين."""
    msp = doc.modelspace()
    total = 0.0
    count = 0
    for e in msp:
        if e.dxftype() == "LWPOLYLINE" and is_closed_lwpolyline(e):
            if (e.dxf.layer or "") == layer_name:
                pts = lwpolyline_points(e)
                total += polygon_area(pts)
                count += 1
    return total, count

def count_inserts_in_layer(inserts: List[Dict[str,Any]], layer_name: str) -> int:
    return sum(1 for b in inserts if (b.get("layer") or "") == layer_name)

def check_dxf(path: str) -> Dict[str, Any]:
    doc = ezdxf.readfile(path)

    # الغرف: بوليلين مغلق في لاير يحتوي "tent"
    rooms   = extract_rooms(doc)
    inserts = collect_inserts_robust(doc, max_depth=2)

    # أبواب فقط
    doors = [b for b in inserts if str_matches_any(b["name"], DOOR_KEYWORDS) or
                                   str_matches_any(b["layer"], DOOR_LAYER_KEYWORDS)]

    # معلومات الموقع (حد المكتب + عداد المداخل)
    site_area_m2, site_poly_count = sum_closed_polyline_area_in_layer(doc, SITE_BOUNDARY_LAYER)
    exits_count  = count_inserts_in_layer(inserts, EXIT_LAYER)
    enters_count = count_inserts_in_layer(inserts, ENTER_LAYER)

    results, failed = [], 0

    for idx, room in enumerate(rooms, start=1):
        pts, area = room["points"], room["area"]

        def inside_room(block) -> bool:
            for (x, y) in block["test_points"]:
                if point_in_or_on_polygon_or_near(x, y, pts):
                    return True
            return False

        room_doors = [d for d in doors if inside_room(d)]
        has_door = len(room_doors) > 0
        area_ok  = area >= MIN_AREA
        ok = has_door and area_ok
        if not ok: failed += 1

        msgs = []
        if not has_door: msgs.append("لا يوجد DOOR")
        if not area_ok:  msgs.append(f"المساحة أقل من {MIN_AREA} م² (المساحة={area:.2f})")

        results.append({
            "room_index": idx,
            "layer": room["layer"],
            "area_m2": round(area, 3),
            "doors_count": len(room_doors),
            "windows_count": 0,  # للتوافق مع واجهات قديمة
            "passed": ok,
            "notes": "صحيحة: تحتوي DOOR ومساحتها كافية" if ok else "، ".join(msgs),
        })

    return {
        "total_rooms": len(rooms),
        "failed_rooms": failed,
        "passed_rooms": len(rooms) - failed,
        "min_area_m2": MIN_AREA,
        "door_keywords": DOOR_KEYWORDS,
        "room_layer_contains": ROOM_LAYER_MUST_INCLUDE,
        "site_info": {
            "boundary_layer": SITE_BOUNDARY_LAYER,
            "boundary_polylines": site_poly_count,
            "boundary_area_m2": round(site_area_m2, 3),
            "exits_layer": EXIT_LAYER,
            "exits_count": exits_count,
            "enters_layer": ENTER_LAYER,
            "enters_count": enters_count,
        },
        "rooms": results,
    }

# ================== واجهة HTML (بدون Jinja) ==================
def render_index(token: str, data: Optional[dict], preview_url: Optional[str]) -> str:
    summary = summarize(data["result"]) if data else {"failed":0,"passed":0,"rooms":0}
    site = (data or {}).get("result", {}).get("site_info") if data else None

    def esc(x): return html.escape(str(x if x is not None else ""))

    # بناء صفوف الجدول
    rows_html = ""
    if data and data.get("result", {}).get("rooms"):
        for r in data["result"]["rooms"]:
            status = '<span class="status-pass">Passed</span>' if r.get("passed") else '<span class="status-fail">Failed</span>'
            rows_html += f"""
              <tr>
                <td>{esc(r.get('room_index'))}</td>
                <td>{esc(r.get('layer'))}</td>
                <td>{esc(f"{r.get('area_m2',0):.3f}")}</td>
                <td>{esc(r.get('doors_count'))}</td>
                <td>{status}</td>
                <td class="notes">{esc(r.get('notes'))}</td>
              </tr>
            """
    table_or_hint = f"""
        <table class="table">
          <thead>
            <tr>
              <th>#</th><th>Layer</th><th>(م²) المساحة</th><th>Doors</th><th>الحالة</th><th class="notes">ملاحظات</th>
            </tr>
          </thead>
          <tbody>{rows_html}</tbody>
        </table>
    """ if rows_html else '<p class="hint">لا توجد نتائج بعد. ارفعي ملف CAD لبدء الفحص.</p>'

    # بطاقة ملخص الموقع
    site_html = """
      <p class="hint" style="margin:0">لا توجد بيانات بعد. ارفعي ملف CAD لعرض الملخص.</p>
    """
    if site:
        site_html = f"""
          <div style="font-size:14px; color:var(--fg); line-height:1.6">
            <div>المساحة الإجمالية: <b>{esc(f"{site.get('boundary_area_m2',0):.3f}")}</b></div>
            <div>عدد المداخل الفرعية: <b>{esc(site.get('exits_count',0))}</b></div>
            <div>عدد المداخل الرئيسية: <b>{esc(site.get('enters_count',0))}</b></div>
          </div>
        """

    preview_img = f'<img src="{esc(preview_url)}" alt="Preview">' if preview_url else ""

    export_html = f"""
      <a class="btn" href="/export-pdf?token={esc(token)}">تصدير PDF</a>
      <a class="btn" href="/export-excel?token={esc(token)}">تصدير Excel</a>
    """ if token else '<span class="hint">سيظهر التصدير بعد رفع الملف.</span>'

    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>فاحص ملفات CAD</title>
  <style>
    :root{{
      --bg:#ffffff; --fg:#0f172a; --muted:#6b7280;
      --line:#e5e7eb; --soft:#f6f7fb; --brand:#2563eb;
      --danger:#dc2626; --good:#16a34a;
      --card:#ffffff;
    }}
    *{{box-sizing:border-box;font-family:system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans",Arial}}
    body{{margin:0;background:var(--bg);color:var(--fg)}}
    .container{{max-width:1280px;margin:auto;padding:20px}}
    .headerbar{{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;}}
    .logo{{height:100px;max-width:240px;object-fit:contain}}
    .title{{font-size:42px;font-weight:800;color:var(--fg)}}
    .main{{display:grid;grid-template-columns:2fr 1fr;gap:18px}}
    .panel{{background:var(--card);border:1px solid var(--line);border-radius:12px}}
    .panel .body{{padding:14px}}
    .preview-wrap{{padding:14px}}
    .preview-box{{height:360px; border:2px dashed var(--line); background:linear-gradient(90deg,#fbfcff 50%,#f3f6fb 50%); border-radius:12px; overflow:hidden; position:relative; display:flex; align-items:center; justify-content:center}}
    .preview-box img{{max-width:100%;max-height:100%;display:block;margin:auto}}
    .summary-line{{padding:10px 8px 14px; color:var(--muted); font-size:14px;display:flex;gap:10px;flex-wrap:wrap;align-items:center;}}
    .summary-line b{{color:#111827}}
    .btn{{display:inline-block; padding:10px 16px; border-radius:12px; border:2px solid var(--brand); color:var(--brand); background:#fff; text-decoration:none; font-weight:700; transition:0.15s;}}
    .btn:hover{{background:#eef4ff}}
    .btns{{display:flex;gap:12px;padding:0 14px 14px}}
    .form-row{{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}}
    .form-row label{{font-weight:700}}
    input[type="file"]{{max-width:260px}}
    .upload-btn{{width:100%;padding:12px;border:0;border-radius:12px;background:var(--brand);color:#fff;font-weight:800;cursor:pointer}}
    .hint{{color:var(--muted);font-size:13px;margin-top:6px}}
    .table{{width:100%;border-collapse:collapse}}
    .table th,.table td{{padding:14px 10px;border-bottom:1px solid var(--line);text-align:center}}
    .status-fail{{color:var(--danger);font-weight:800}}
    .status-pass{{color:var(--good);font-weight:800}}
    .notes{{text-align:right}}
    @media (max-width:1000px){{ .main{{grid-template-columns:1fr}} .logo{{height:80px}} }}
    @media (max-width:600px){{ .logo{{height:60px}} .title{{font-size:28px}} }}
  </style>
</head>
<body>
  <div class="container">

    <div class="headerbar">
      <img src="/static/logo.png" alt="Logo" class="logo">
      <div class="title">فاحص ملفات CAD</div>
    </div>

    <div class="main">
      <div class="panel">
        <div class="preview-wrap">
          <div class="preview-box">{preview_img}</div>
        </div>
        <div class="summary-line">
          <span><b>{esc(summary.get('failed',0))}</b> :فاشلة</span>
          <span>•</span>
          <span><b>{esc(summary.get('passed',0))}</b> :ناجحة</span>
          <span>•</span>
          <span><b>{esc(summary.get('rooms',0))}</b> :غرف</span>
        </div>
        <div class="btns">{export_html}</div>
      </div>

      <div class="panel">
        <div class="body">
          <h3 style="margin:0 0 14px">ارفع ملف المخططات.</h3>

          <div style="margin-bottom:14px">
            <a href="/static/instructions.pdf" target="_blank" style="color:var(--brand);font-weight:700;text-decoration:none;">
              تنزيل تعليمات الفاحص (PDF)
            </a>
          </div>

          <form action="/upload-cad" method="post" enctype="multipart/form-data">
            <div class="form-row">
              <label>ملف التصميم (CAD):</label>
              <input type="file" name="cad_file" accept=".dxf" required />
            </div>

            <div class="form-row">
              <label>ملف Excel (اختياري):</label>
              <input type="file" name="excel_file" accept=".xlsx,.xls" />
            </div>

            <button class="upload-btn" type="submit">رفع وفحص</button>

            <div class="hint">لم يتم اختيار أي ملف حتى الآن</div>
            <div class="hint" style="margin-top:8px">يرجى الرسم بالطريقة المعتمدة حسب ملف التعليمات لضمان دقة الفحص.</div>

            <div class="panel" style="margin-top:12px; border:1px solid var(--line); border-radius:10px; background:var(--soft);">
              <div class="body" style="padding:10px">
                <h4 style="margin:0 0 8px; font-size:15px; font-weight:700; color:var(--fg)">ملخص الموقع</h4>
                {site_html}
              </div>
            </div>
          </form>
        </div>
      </div>
    </div>

    <div class="panel" style="margin-top:16px">
      <div class="body">
        {table_or_hint}
      </div>
    </div>
  </div>
</body>
</html>"""

# ================== المسارات ==================
@app.get("/", response_class=HTMLResponse)
async def index(request: Request, token: Optional[str] = None):
    data = None
    preview_url = None
    if token:
        p = RESULTS / f"{token}.json"
        if p.exists():
            data = safe_json_load(p)
            if data.get("preview_ext") == "png":
                preview_url = f"/results/{token}.png"
    return HTMLResponse(render_index(token or "", data, preview_url))

@app.post("/upload-cad")
async def upload_cad(request: Request, cad_file: UploadFile, excel_file: UploadFile | None = None):
    # حفظ DXF
    token = uuid.uuid4().hex
    dxf_path = UPLOADS / f"{token}.dxf"
    dxf_bytes = await cad_file.read()
    dxf_path.write_bytes(dxf_bytes)

    # فحص
    result = check_dxf(str(dxf_path))

    # توليد المعاينة
    preview_path = RESULTS / f"{token}.png"
    ok, err = generate_preview(dxf_path, preview_path)

    data_to_store = {
        "token": token,
        "source_dxf": str(dxf_path),
        "result": result,
        "preview_ext": "png" if ok else "",
        "preview_error": "" if ok else err,
    }
    safe_json_dump(RESULTS / f"{token}.json", data_to_store)

    # رجوع للواجهة الرئيسية مع التوكن
    return RedirectResponse(url=f"/?token={token}", status_code=303)

@app.get("/export-excel")
def export_excel(token: str):
    p = RESULTS / f"{token}.json"
    if not p.exists():
        return HTMLResponse("Token not found.", status_code=404)
    data = safe_json_load(p)
    result = data["result"]

    wb = Workbook()
    ws = wb.active
    ws.title = "DXF Check"

    # رأس الجدول (بدون عمود Windows)
    ws.append(["#","Layer","مساحة (م²)","Doors","الحالة","ملاحظات"])
    for r in result["rooms"]:
        ws.append([
            r["room_index"], r["layer"], r["area_m2"],
            r["doors_count"],
            "Passed" if r["passed"] else "Failed",
            r["notes"]
        ])

    out_xlsx = RESULTS / f"{token}.xlsx"
    wb.save(str(out_xlsx))
    return FileResponse(str(out_xlsx), filename=f"DXF_Check_{token}.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.get("/export-pdf")
def export_pdf(token: str):
    p = RESULTS / f"{token}.json"
    if not p.exists():
        return HTMLResponse("Token not found.", status_code=404)
    data = safe_json_load(p)
    result = data["result"]

    out_pdf = RESULTS / f"{token}.pdf"
    c = canvas.Canvas(str(out_pdf), pagesize=A4)
    w, h = A4

    # عنوان
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, h-50, "تقرير فحص DXF")

    # ملخص
    c.setFont("Helvetica", 12)
    summary = summarize(result)
    c.drawString(40, h-80, f"عدد الغرف: {summary['rooms']}  |  ناجحة: {summary['passed']}  |  فاشلة: {summary['failed']}  |  الحد الأدنى للمساحة: {summary['min_area']} م²")

    # صورة المعاينة (إن وجدت)
    img_path = RESULTS / f"{token}.png"
    if img_path.exists():
        try:
            img = ImageReader(str(img_path))
            img_w, img_h = img.getSize()
            scale = min(520 / img_w, 350 / img_h)
            draw_w, draw_h = img_w * scale, img_h * scale
            c.drawImage(img, 40, h-80-20-draw_h, width=draw_w, height=draw_h, preserveAspectRatio=True, mask='auto')
            y = h-80-20-draw_h-20
        except Exception:
            y = h-180
    else:
        y = h-180

    # جدول بسيط (أول 25 صفًا)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, "تفاصيل الغرف:")
    y -= 16

    headers = ["#", "Layer", "مساحة", "Doors", "الحالة", "ملاحظات"]
    widths  = [25, 90, 55, 40, 55, 300]
    c.setFont("Helvetica-Bold", 10)
    x = 40
    for head, wcol in zip(headers, widths):
        c.drawString(x, y, str(head)); x += wcol
    y -= 14
    c.setFont("Helvetica", 9)

    for r in result["rooms"][:25]:
        x = 40
        row = [
            r["room_index"], r["layer"], r["area_m2"],
            r["doors_count"],
            "Passed" if r["passed"] else "Failed",
            r["notes"]
        ]
        for val, wcol in zip(row, widths):
            c.drawString(x, y, str(val)); x += wcol
        y -= 12
        if y < 40:
            c.showPage(); y = h-50
            c.setFont("Helvetica", 9)

    c.showPage()
    c.save()
    return FileResponse(str(out_pdf), filename=f"DXF_Check_{token}.pdf", media_type="application/pdf")
