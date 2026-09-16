"""Le job de rafraichissement automatique doit etre ACTIF des son ajout, pas en
pause. Regression : add_job(..., next_run_time=None) l'ajoute en pause et
APScheduler ne le relance jamais tout seul -- le rafraichissement 12h ne
partait alors jamais sans un declenchement manuel de /api/refresh/scan."""
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from firemap import scheduler


def test_refresh_job_has_a_real_next_run_time_not_paused():
    sched = BackgroundScheduler()
    scheduler._add_refresh_job(sched)

    job = sched.get_job("refresh_scan")
    assert job is not None
    assert job.next_run_time is not None
    assert job.next_run_time > datetime.now(job.next_run_time.tzinfo)
