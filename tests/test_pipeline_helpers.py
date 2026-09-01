"""Utilitaires du pipeline : idempotence par date de modif, fenetres, noms COG."""
import datetime as dt
import os
import time

from firemap import pipeline


def test_cog_name():
    assert pipeline.cog_name("ndvi.tif") == "ndvi.cog.tif"
    assert pipeline.cog_name("risk_classes.tif") == "risk_classes.cog.tif"


def test_recent_windows_finit_aujourdhui():
    wins = pipeline._recent_windows()
    today = dt.date.today().isoformat()
    assert all(fin == today for _, fin in wins)
    assert wins[0][0] > wins[-1][0]           # 1re fenetre = la plus etroite : debut le plus tardif
    # borne de debut = today - lookback
    d0 = dt.date.fromisoformat(wins[0][0])
    assert (dt.date.today() - d0).days == pipeline._S2_LOOKBACKS_DAYS[0]


def test_outdated(tmp_path):
    out = tmp_path / "risk.tif"
    src = tmp_path / "fwi.tif"

    assert pipeline._outdated(out, src) is True          # sortie absente

    out.write_text("x")
    time.sleep(0.01)
    src.write_text("x")                                  # source plus recente
    assert pipeline._outdated(out, src) is True

    now = time.time()
    os.utime(out, (now + 10, now + 10))                  # sortie re-datee dans le futur
    assert pipeline._outdated(out, src) is False

    assert pipeline._outdated(out, tmp_path / "absent.tif") is False  # input inexistant ignore
