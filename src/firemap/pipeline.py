"""pipeline.py -- orchestrateur : genere (ou rafraichit) UNE commune de bout en bout.

Transforme les 6 scripts manuels (scripts/phase*.py) en une seule fonction
`run(insee)` :
  - parametree par commune (CommuneContext) -> ecrit dans data/communes/<INSEE>/ ;
  - IDEMPOTENTE / rejouable : une etape dont les fichiers de sortie existent
    deja est sautee (sauf force=True) -> reprise possible apres un echec ;
  - branchee sur le REGISTRE : running au debut, ready(dates) a la fin,
    error(trace) si ca casse.

La logique de calcul (formules FWI, ponderations, quantiles) n'est PAS touchee :
on reutilise telles quelles les fonctions de firemap.ingestion / firemap.risk.
Le pipeline ne produit que les .tif / .geojson (donnees) + metadata.json ;
le rendu visuel (PNG/tuiles) sera fait proprement en Phase 2 v2.
"""
import datetime as dt
import json
import os
import traceback
import warnings

import geopandas as gpd
import numpy as np
import rasterio
from rasterio import features
from rasterio.transform import array_bounds
from rio_cogeo.cogeo import cog_translate
from rio_cogeo.profiles import cog_profiles
from scipy.ndimage import distance_transform_edt

from . import config, registry
from .context import CommuneContext
from .departements import search_batches
from .grid import build_reference_grid, rasterize_commune_mask, save_gabarit
from .ingestion.commune import load_or_fetch_commune
from .ingestion.enjeux import fetch_enjeux_points
from .ingestion.fwi import compute_fwi_series, fetch_daily_weather, nearest_open_stations
from .ingestion.landcover import assign_fuel_weight, fetch_vegetation_zones
from .ingestion.mnt import compute_slope_aspect, fetch_elevation
from .ingestion.sentinel2 import fetch_ndvi_ndmi
from .risk.fusion import classify_risk, compute_risk, read_layer
from .risk.priorisation import compute_priorite, extract_priority_zones
from .storage import LAYERS as _LAYER_SPECS


def cog_name(tif_name: str) -> str:
    """ndvi.tif -> ndvi.cog.tif (convention des Cloud-Optimized GeoTIFF)."""
    return tif_name[:-4] + ".cog.tif" if tif_name.endswith(".tif") else tif_name + ".cog.tif"

# --- Reglages fenetres temporelles ("derniere donnee disponible", cf. cahier) ---
_S2_COVERAGE_TARGET = 90.0            # % de pixels sans nuage vise avant d'arreter d'elargir
_S2_LOOKBACKS_DAYS = (30, 60, 120)   # reculs successifs de la borne de debut S2
_FWI_LOOKBACK_DAYS = 120             # fenetre meteo (large -> spin-up du systeme canadien)


# ===========================================================================
# Petits utilitaires
# ===========================================================================
def _log(ctx: CommuneContext, msg: str) -> None:
    print(f"[{ctx.insee}] {msg}", flush=True)


def _present(*paths) -> bool:
    """True si TOUS les fichiers existent (une etape peut alors etre sautee)."""
    return all(p.exists() for p in paths)


def _outdated(output, *inputs) -> bool:
    """True si `output` doit etre (re)calcule : absent, ou plus ancien qu'au moins
    une de ses `inputs`. Permet un rafraichissement SANS supprimer le fichier
    servi -- il est ecrase seulement une fois le nouveau pret."""
    if not output.exists():
        return True
    out_mtime = output.stat().st_mtime
    return any(p.exists() and p.stat().st_mtime > out_mtime for p in inputs)


def _recent_windows(lookbacks=_S2_LOOKBACKS_DAYS):
    """Fenetres (debut, fin) ISO : fin = aujourd'hui, debut recule progressivement."""
    today = dt.date.today()
    return [((today - dt.timedelta(days=n)).isoformat(), today.isoformat()) for n in lookbacks]


def _save_raster(path, array, grid, dtype="float32", nodata=None) -> None:
    """Ecrit un tableau numpy en GeoTIFF cale sur la grille gabarit (meme
    emprise / resolution / CRS que toutes les autres couches)."""
    profile = grid.profile
    profile.update(dtype=dtype, nodata=nodata)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array.astype(dtype), 1)


def _read_metadata(ctx: CommuneContext) -> dict:
    p = ctx.metadata_path
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _merge_metadata(ctx: CommuneContext, **fields) -> dict:
    """Fusionne des champs dans data/communes/<INSEE>/processed/metadata.json.
    Ecrit incrementalement (apres chaque etape porteuse de dates) : le fichier
    sert a la fois de fiche de provenance ET de source pour la reprise idempotente."""
    data = _read_metadata(ctx)
    data.update({k: v for k, v in fields.items() if v is not None})
    ctx.metadata_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def _write_points(path, gdf: gpd.GeoDataFrame) -> None:
    """GeoJSON des enjeux ponctuels ; tolere une couche vide (commune sans ICPE/ecole)."""
    if len(gdf):
        gdf.to_file(path, driver="GeoJSON")
    else:
        path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")


def _read_points(path) -> gpd.GeoDataFrame:
    try:
        gdf = gpd.read_file(path)
        return gdf.to_crs(config.CRS_COMPUTE) if len(gdf) else _empty_points()
    except Exception:
        return _empty_points()


def _empty_points() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"categorie": []}, geometry=[], crs=config.CRS_COMPUTE)


# ===========================================================================
# Etapes (chacune : saute si ses sorties existent deja, sauf force)
# ===========================================================================
def _step_commune(ctx, force):
    """Contour officiel de la commune (WGS84 + Lambert-93)."""
    return load_or_fetch_commune(force_refresh=force, ctx=ctx)


def _step_grid(ctx, gdf_l93, force):
    """Grille gabarit + masque commune. La grille est recalculee en memoire a
    chaque run (instantane) ; seul gabarit.tif est mis en cache."""
    grid = build_reference_grid(gdf_l93)
    mask = rasterize_commune_mask(gdf_l93, grid).astype(bool)
    if force or not _present(ctx.processed("gabarit.tif")):
        save_gabarit(ctx.processed("gabarit.tif"), grid, mask.astype("uint8"))
        _log(ctx, f"grille {grid.width}x{grid.height} @ {grid.resolution} m, "
                  f"{int(mask.sum())} px dans la commune")
    return grid, mask


def _step_indices(ctx, grid, mask, force) -> str | None:
    """NDVI / NDMI Sentinel-2 (CDSE). Retourne la date "as-of" (fin de la
    fenetre retenue), pour affichage de fraicheur cote frontend."""
    ndvi_p, ndmi_p = ctx.processed("ndvi.tif"), ctx.processed("ndmi.tif")
    if not force and _present(ndvi_p, ndmi_p):
        return _read_metadata(ctx).get("sentinel2_asof")

    best = None  # (coverage, window, data)
    for window in _recent_windows():
        data = fetch_ndvi_ndmi(grid, window)
        coverage = 100.0 * ((data[:, :, 2].astype(bool) & mask).sum() / mask.sum())
        _log(ctx, f"S2 {window} -> {coverage:.1f} % sans nuage")
        if best is None or coverage > best[0]:
            best = (coverage, window, data)
        if coverage >= _S2_COVERAGE_TARGET:
            break

    _, window, data = best
    valid = data[:, :, 2].astype(bool)
    _save_raster(ndvi_p, np.where(valid, data[:, :, 0], np.nan).astype("float32"), grid)
    _save_raster(ndmi_p, np.where(valid, data[:, :, 1], np.nan).astype("float32"), grid)
    _merge_metadata(ctx, sentinel2_asof=window[1],
                    sentinel2_window=f"{window[0]} -> {window[1]} (mosaique la moins nuageuse, CDSE)")
    return window[1]


def _step_terrain(ctx, grid, force):
    """Pente / exposition depuis le MNT IGN RGE ALTI (WMS ouvert)."""
    slope_p, aspect_p = ctx.processed("slope.tif"), ctx.processed("aspect.tif")
    if not force and _present(slope_p, aspect_p):
        return
    elevation = fetch_elevation(grid)
    slope, aspect = compute_slope_aspect(elevation, grid.resolution)
    _save_raster(slope_p, slope, grid)
    _save_raster(aspect_p, aspect, grid)
    finite = slope[np.isfinite(slope)]
    _log(ctx, f"pente moy {finite.mean():.1f} deg, max {finite.max():.1f} deg")


def _step_fuel(ctx, grid, force):
    """Combustible = zones de vegetation IGN BD TOPO (WFS) ponderees par inflammabilite."""
    fuel_p = ctx.processed("fuel.tif")
    if not force and _present(fuel_p):
        return
    bounds = array_bounds(grid.height, grid.width, grid.transform)
    veg = assign_fuel_weight(fetch_vegetation_zones(bounds))
    # tri par poids croissant : en cas de recouvrement, le poids le plus fort gagne
    shapes = sorted(zip(veg.geometry, veg["poids_combustible"]), key=lambda it: it[1])
    fuel = features.rasterize(shapes, out_shape=(grid.height, grid.width),
                              transform=grid.transform, fill=0.0, dtype="float32")
    _save_raster(fuel_p, fuel, grid)
    _log(ctx, f"{len(veg)} zones de vegetation")


def _step_enjeux(ctx, grid, force) -> gpd.GeoDataFrame:
    """ICPE/SEVESO (Georisques) + ecoles (education.gouv.fr) -> distance au plus
    proche enjeu. Retourne les points en Lambert-93 (utilises par la priorisation)."""
    enjeux_p, pts_p = ctx.processed("enjeux.tif"), ctx.processed("enjeux_points.geojson")
    if not force and _present(enjeux_p, pts_p):
        return _read_points(pts_p)

    pts_l93 = fetch_enjeux_points(ctx.insee).to_crs(config.CRS_COMPUTE)
    _write_points(pts_p, pts_l93)

    if len(pts_l93):
        burned = features.rasterize([(g, 1) for g in pts_l93.geometry],
                                    out_shape=(grid.height, grid.width),
                                    transform=grid.transform, fill=0, dtype="uint8")
        distance = distance_transform_edt(
            burned == 0, sampling=(grid.resolution, grid.resolution)
        ).astype("float32")
    else:
        # aucun enjeu : distance "tres grande" uniforme -> proximite neutre en priorisation
        distance = np.full((grid.height, grid.width), 1e6, dtype="float32")

    _save_raster(enjeux_p, distance, grid)
    n_icpe = int((pts_l93.get("categorie") == "ICPE").sum()) if len(pts_l93) else 0
    _log(ctx, f"{len(pts_l93)} enjeux ({n_icpe} ICPE, {len(pts_l93) - n_icpe} etab. scolaires)")
    return pts_l93


def _step_fwi(ctx, grid, gdf_wgs84, force) -> str | None:
    """FWI : station Meteo-France DPClim la plus proche -> calcul du systeme
    canadien Foret-Meteo -> raster uniforme. Retourne la date du dernier jour dispo."""
    fwi_p = ctx.processed("fwi.tif")
    if not force and _present(fwi_p):
        return _read_metadata(ctx).get("fwi_date")

    centroid = gdf_wgs84.geometry.iloc[0].centroid
    today = dt.date.today()
    debut = (today - dt.timedelta(days=_FWI_LOOKBACK_DAYS)).isoformat() + "T00:00:00Z"
    fin = today.isoformat() + "T00:00:00Z"

    # Toutes les stations ne mesurent pas humidite + vent (necessaires au FWI).
    # On elargit la recherche par cercles concentriques -- departement local,
    # puis reste de la region, puis regions limitrophes -- et on prend la
    # premiere station qui fournit une serie FWI non vide.
    station = last = None
    for batch in search_batches(ctx.departement):
        for st in nearest_open_stations(centroid.y, centroid.x, batch)[:8]:
            try:
                serie = compute_fwi_series(fetch_daily_weather(st["id"], debut, fin)).dropna()
            except Exception as exc:   # DPClim lent / HS pour CETTE station -> suivante
                _log(ctx, f"station {st['nom']} ignoree ({type(exc).__name__})")
                continue
            if not serie.empty:
                station, last = st, serie.iloc[-1]
                break
        if station is not None:
            break
    if station is None:
        raise RuntimeError(
            f"FWI indisponible pour {ctx.insee} : aucune station Meteo-France proche ne "
            "fournit temperature + humidite + vent + pluie (ou DPClim est indisponible). "
            "Reessayer plus tard."
        )
    if (today - last["DATE"].date()).days > 4:
        _log(ctx, f"ATTENTION FWI : donnee la plus recente = {last['DATE'].date()} "
                  f"(station {station['nom']} en retard de publication)")

    current_fwi = float(last["FWI"])
    _save_raster(fwi_p, np.full((grid.height, grid.width), current_fwi, dtype="float32"), grid)

    fwi_date = last["DATE"].date().isoformat()
    _merge_metadata(ctx, fwi_date=fwi_date, fwi_value=round(current_fwi, 1),
                    fwi_station=f"{station['nom']} ({station['id']})")
    _log(ctx, f"FWI {current_fwi:.1f} (station {station['nom']}, {fwi_date})")
    return fwi_date


def _step_risk(ctx, grid, mask, pts_l93, force):
    """Fusion ponderee -> risk.tif ; quantiles -> risk_classes.tif ;
    risk x proximite enjeux -> priorites.geojson (zones a traiter en priorite)."""
    risk_p = ctx.processed("risk.tif")
    classes_p = ctx.processed("risk_classes.tif")
    prio_p = ctx.processed("priorites.geojson")
    inputs = [ctx.processed(n) for n in
              ("ndvi.tif", "ndmi.tif", "fwi.tif", "slope.tif", "aspect.tif", "fuel.tif", "enjeux.tif")]
    # (re)calcule si un resultat manque OU si une couche source est plus recente
    # (cas du rafraichissement : fwi.tif vient d'etre regenere)
    if not force and _present(classes_p, prio_p) and not _outdated(risk_p, *inputs):
        return

    layers = compute_risk(mask, processed_dir=ctx.processed_dir)
    risk, valid_mask = layers["risk"], layers["valid_mask"]
    _save_raster(risk_p, np.nan_to_num(risk, nan=0.0), grid, nodata=0.0)

    risk_classes, (q1, q2, q3) = classify_risk(risk, valid_mask)
    _save_raster(classes_p, risk_classes, grid, dtype="uint8", nodata=0)

    priorite = compute_priorite(risk, read_layer("enjeux.tif", ctx.processed_dir), valid_mask)
    zones = extract_priority_zones(priorite, risk_classes, valid_mask, grid, pts_l93)
    zones.to_crs(config.CRS_WEB).to_file(prio_p, driver="GeoJSON")
    _log(ctx, f"quantiles Q1/Q2/Q3 = {q1:.3f}/{q2:.3f}/{q3:.3f} ; {len(zones)} zones prioritaires")


def _step_cog(ctx, force):
    """Convertit chaque couche affichable en Cloud-Optimized GeoTIFF web-optimise
    (reprojection Web Mercator + tuilage interne aligne sur la grille XYZ), lu
    ensuite par la route de tuiles (rio-tiler).

    Les couches sont d'abord DETOUREES sur le contour communal (le gabarit) :
    pente/exposition/combustible/FWI/NDVI/NDMI sont calculees sur tout le
    rectangle englobant, il ne faut pas qu'elles debordent du contour sur la carte.
    """
    profile = cog_profiles.get("deflate")  # sans perte : adapte aux valeurs continues
    with rasterio.open(ctx.processed("gabarit.tif")) as g:
        commune = g.read(1).astype(bool)  # 1 = pixel dans la commune

    faits = []
    for spec in _LAYER_SPECS:
        src_p = ctx.processed(spec.filename)
        dst_p = ctx.processed(cog_name(spec.filename))
        if not src_p.exists():
            continue
        # rebuild si le COG manque OU est plus ancien que sa source (rafraichissement)
        if not force and not _outdated(dst_p, src_p):
            continue

        with rasterio.open(src_p) as src:
            data = src.read(1)
            meta = src.meta.copy()

        if spec.categorical:                       # risk_classes : 0 = hors commune
            data = np.where(commune, data, 0).astype(meta["dtype"])
            meta["nodata"] = 0
        else:                                      # continu : hors commune -> nodata (nan)
            data = np.where(commune, data, np.nan).astype("float32")
            meta.update(dtype="float32", nodata=float("nan"))

        # plus proche voisin partout : pas de melange de classes, pas de halo de
        # nan en bord de commune (a 10 m natif, l'ecart visuel est negligeable).
        # Ecriture dans un fichier temporaire puis os.replace (atomique) : lors
        # d'un rafraichissement, l'ancien COG continue d'etre servi jusqu'ici.
        tmp_out = dst_p.parent / (dst_p.name + ".tmp")
        with rasterio.io.MemoryFile() as mem:
            with mem.open(**meta) as tmp:
                tmp.write(data, 1)
            with mem.open() as tmp_r, warnings.catch_warnings():
                # rio-cogeo emet un RuntimeWarning benin en castant le nodata nan
                warnings.simplefilter("ignore", RuntimeWarning)
                cog_translate(tmp_r, tmp_out, profile, web_optimized=True, quiet=True,
                              in_memory=False, resampling="nearest",
                              overview_resampling="nearest")
        os.replace(tmp_out, dst_p)
        faits.append(spec.id)

    if faits:
        _log(ctx, f"COG : {len(faits)} couche(s) -> {', '.join(faits)}")


# ===========================================================================
# Point d'entree
# ===========================================================================
def run(insee: str, *, nom: str | None = None, force: bool = False) -> registry.RegistryEntry:
    """Genere (ou rafraichit) la commune `insee` de bout en bout.

    force=True  : refait toutes les etapes, meme si leurs fichiers existent.
    force=False : idempotent -- les etapes deja faites sont sautees (reprise
                  apres un echec en cours de route).

    Met le registre a jour (queued -> running -> ready / error) et renvoie
    l'entree finale. Re-leve l'exception apres l'avoir enregistree.
    """
    ctx = CommuneContext(insee, nom=nom).ensure_dirs()
    registry.mark_queued(insee, nom)   # upsert : garantit qu'une ligne existe
    registry.mark_running(insee)
    _log(ctx, f"generation demarree (force={force})")

    etape = "init"
    try:
        etape = "commune";     gdf, gdf_l93 = _step_commune(ctx, force)
        vrai_nom = str(gdf.iloc[0]["nom"])
        etape = "grille";      grid, mask = _step_grid(ctx, gdf_l93, force)
        etape = "indices";     s2_asof = _step_indices(ctx, grid, mask, force)
        etape = "terrain";     _step_terrain(ctx, grid, force)
        etape = "combustible"; _step_fuel(ctx, grid, force)
        etape = "enjeux";      pts_l93 = _step_enjeux(ctx, grid, force)
        etape = "fwi";         fwi_date = _step_fwi(ctx, grid, gdf, force)
        etape = "risque";      _step_risk(ctx, grid, mask, pts_l93, force)
        etape = "cog";         _step_cog(ctx, force)

        etape = "metadata"
        _merge_metadata(
            ctx,
            commune=f"{vrai_nom} ({insee})",
            sources_fetched=dt.date.today().isoformat(),
            mode="v2 - pipeline a la demande + cache",
        )
        registry.mark_ready(insee, date_sentinel2=s2_asof, date_fwi=fwi_date)
        _log(ctx, f"OK -> ready (S2 {s2_asof}, FWI {fwi_date})")
        return registry.get(insee)

    except Exception as exc:  # on veut ATTRAPER pour enregistrer, puis re-lever
        message = f"[etape {etape}] {type(exc).__name__}: {exc}"
        registry.mark_error(insee, message + "\n\n" + traceback.format_exc())
        _log(ctx, f"ECHEC {message}")
        raise
