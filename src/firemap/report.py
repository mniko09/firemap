"""report.py -- export PDF "rapport" par commune (carte + resume + zones
prioritaires) pour une lecture hors-ligne (reunion, transmission SDIS...).

Ne recalcule RIEN : relit les memes fichiers (risk_classes.tif, priorites.geojson,
metadata.json) que sert deja l'API web -- une seule source de verite pour les
chiffres, le PDF n'est qu'une autre presentation des memes donnees.
"""
import io
import json
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")  # pas d'affichage : ce process ne fait que generer des PNG
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.colors import ListedColormap
from pyproj import Transformer
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import config, registry
from .context import CommuneContext

RISK_COLORS = {1: "#2ca02c", 2: "#f1c40f", 3: "#e67e22", 4: "#c0392b"}
RISK_LABELS = {1: "Faible", 2: "Modere", 3: "Eleve", 4: "Tres eleve"}

_TO_L93 = Transformer.from_crs(config.CRS_WEB, config.CRS_COMPUTE, always_xy=True)


def _render_map_png(ctx: CommuneContext) -> bytes:
    """Carte statique (risque lisse + pastilles numerotees des zones prioritaires),
    dans l'esprit de ce qu'affiche la carte interactive."""
    with rasterio.open(ctx.processed("risk_classes_lisse.tif")) as src:
        arr = src.read(1).astype("float32")
        transform = src.transform
    arr[arr == 0] = np.nan

    fig, ax = plt.subplots(figsize=(6, 6), dpi=150)
    cmap = ListedColormap([RISK_COLORS[i] for i in (1, 2, 3, 4)])
    ax.imshow(arr, cmap=cmap, vmin=1, vmax=4, interpolation="nearest")
    ax.axis("off")

    prio_path = ctx.processed("priorites.geojson")
    if prio_path.exists():
        from shapely.geometry import shape

        feats = json.loads(prio_path.read_text(encoding="utf-8"))["features"]
        for f in feats:
            p = f["properties"]
            centroid = shape(f["geometry"]).centroid
            x, y = _TO_L93.transform(centroid.x, centroid.y)
            row, col = rasterio.transform.rowcol(transform, x, y)
            color = RISK_COLORS.get(p.get("classe_risque_niveau"), "#c0392b")
            ax.scatter([col], [row], s=160, color=color, edgecolors="white",
                       linewidths=1.5, zorder=5)
            ax.annotate(str(p["id"]), (col, row), color="white", fontsize=7,
                        fontweight="bold", ha="center", va="center", zorder=6)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)
    return buf.getvalue()


def _resume(ctx: CommuneContext) -> tuple[dict, int]:
    """Meme calcul que /api/communes/{insee}/risque/resume (cf. routes_layers.py)."""
    with rasterio.open(ctx.processed("risk_classes.tif")) as src:
        arr = src.read(1)
        pixel_area_ha = abs(src.res[0] * src.res[1]) / 10_000
    total = int((arr != 0).sum())
    classes = {}
    for c in (1, 2, 3, 4):
        n = int((arr == c).sum())
        classes[c] = {
            "pct": round(100 * n / total, 1) if total else 0.0,
            "surface_ha": round(n * pixel_area_ha, 1),
        }
    return classes, total


def build_report_pdf(insee: str) -> bytes:
    """Construit le PDF en memoire et retourne ses octets."""
    ctx = CommuneContext(insee)
    if not ctx.processed("risk_classes.tif").exists():
        raise FileNotFoundError(f"commune {insee} pas encore generee")

    meta = json.loads(ctx.metadata_path.read_text(encoding="utf-8")) if ctx.metadata_path.exists() else {}
    entry = registry.get(insee)
    nom = (entry.nom if entry else None) or meta.get("commune") or insee

    classes, _total = _resume(ctx)
    prio_path = ctx.processed("priorites.geojson")
    zones = json.loads(prio_path.read_text(encoding="utf-8"))["features"] if prio_path.exists() else []
    map_png = _render_map_png(ctx)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=16 * mm, bottomMargin=16 * mm,
                             leftMargin=18 * mm, rightMargin=18 * mm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("FMTitle", parent=styles["Title"], textColor=colors.HexColor("#c0392b"))
    h2 = ParagraphStyle("FMH2", parent=styles["Heading2"], textColor=colors.HexColor("#222c3a"))
    small = ParagraphStyle("FMSmall", parent=styles["Normal"], fontSize=8, textColor=colors.grey)
    cell = ParagraphStyle("FMCell", parent=styles["Normal"], fontSize=8)

    station_txt = ""
    if meta.get("fwi_station"):
        station_txt = f" (station {meta['fwi_station']}"
        if meta.get("fwi_station_distance_km") is not None:
            station_txt += f", {meta['fwi_station_distance_km']} km"
        station_txt += ")"

    elements = [
        Paragraph(f"SELVERT FIREMAP — {nom}", title_style),
        Paragraph(
            f"Genere le {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')} UTC · "
            f"Sentinel-2 : {meta.get('sentinel2_asof', '?')} · "
            f"FWI : {meta.get('fwi_date', '?')}{station_txt}",
            small,
        ),
        Spacer(1, 6 * mm),
        Image(io.BytesIO(map_png), width=170 * mm, height=170 * mm),
        Spacer(1, 6 * mm),
        Paragraph("Repartition du risque", h2),
    ]

    data = [["Classe", "Surface", "% de la commune"]]
    for c in (4, 3, 2, 1):
        d = classes.get(c, {"pct": 0, "surface_ha": 0})
        data.append([RISK_LABELS[c], f"{d['surface_ha']} ha", f"{d['pct']} %"])
    t = Table(data, colWidths=[56 * mm, 56 * mm, 56 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#222c3a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f7f9")]),
    ]))
    elements += [t, Spacer(1, 8 * mm), Paragraph(f"Zones prioritaires retardant ({len(zones)})", h2)]

    zdata = [["#", "Classe", "Surface", "Enjeu proche", "Action recommandee"]]
    for f in zones:
        p = f["properties"]
        zdata.append([
            str(p["id"]), p["classe_risque"], f"{p['surface_m2'] / 10000:.2f} ha",
            Paragraph(p.get("enjeu_proche") or p.get("categorie_enjeu") or "-", cell),
            Paragraph(p["action_recommandee"], cell),
        ])
    zt = Table(zdata, colWidths=[9 * mm, 22 * mm, 20 * mm, 43 * mm, 74 * mm], repeatRows=1)
    zt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#222c3a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f7f9")]),
    ]))
    elements.append(zt)

    doc.build(elements)
    return buf.getvalue()
