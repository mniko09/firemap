"""Test de charge FIREMAP -- ordre de grandeur pour le dimensionnement (Phase 4).

Envoie un melange realiste de requetes sur les chemins chauds d'une instance
servant des communes DEJA en cache, en parallele pendant --duration secondes,
et affiche debit + latences p50/p90/p99 par endpoint.

    python scripts/loadtest.py --insee 83130 --concurrency 20 --duration 15
    python scripts/loadtest.py --base https://firemap.exemple.fr --insee 83130

Ne teste PAS la generation d'une commune (lourde, asynchrone) ni /search
(depend de geo.api.gouv.fr, externe).
"""
import argparse
import asyncio
import random
import statistics
import time
from collections import defaultdict

import httpx

# Melange approximatif d'une session utilisateur : surtout des tuiles, puis le
# polling de statut, quelques clics "valeur au point", de temps en temps la page.
MIX = [
    ("tuile", 0.62),
    ("status", 0.24),
    ("value", 0.10),
    ("index", 0.04),
]

# Quelques tuiles couvrant Solliès-Pont aux zooms usuels (z / x / y).
TILES = [
    (13, 4233, 3003), (13, 4234, 3003), (13, 4233, 3004), (13, 4234, 3004),
    (12, 2116, 1501), (12, 2117, 1502), (14, 8467, 6008), (11, 1058, 750),
]
LAYERS = ["risk_classes", "risk", "secheresse_ndmi", "pente", "fwi"]


def _pick() -> str:
    r, cum = random.random(), 0.0
    for name, w in MIX:
        cum += w
        if r <= cum:
            return name
    return MIX[-1][0]


def _request(kind: str, insee: str):
    """(methode, url) pour un type de requete."""
    if kind == "tuile":
        z, x, y = random.choice(TILES)
        return f"/api/communes/{insee}/layers/{random.choice(LAYERS)}/{z}/{x}/{y}.png"
    if kind == "status":
        return f"/api/communes/{insee}/status"
    if kind == "value":
        return f"/api/communes/{insee}/value?lat={43.17 + random.random() * 0.05}&lon={6.03 + random.random() * 0.06}"
    return "/"


async def _worker(client, base, insee, deadline, lat, errors):
    while time.perf_counter() < deadline:
        kind = _pick()
        url = _request(kind, insee)
        t0 = time.perf_counter()
        try:
            resp = await client.get(base + url)
            dt = time.perf_counter() - t0
            lat[kind].append(dt)
            if resp.status_code >= 500:
                errors[kind] += 1
        except Exception:
            errors[kind] += 1
            lat[kind].append(time.perf_counter() - t0)


def _pct(values, p):
    if not values:
        return 0.0
    values = sorted(values)
    k = min(len(values) - 1, int(round((p / 100) * (len(values) - 1))))
    return values[k] * 1000  # ms


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--insee", default="83130")
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--duration", type=float, default=15.0)
    args = ap.parse_args()

    # Pre-check : la commune doit etre 'ready'
    async with httpx.AsyncClient(timeout=10) as c:
        s = (await c.get(f"{args.base}/api/communes/{args.insee}/status")).json()
        if not s.get("pret"):
            raise SystemExit(f"commune {args.insee} pas prete (statut={s.get('statut')}) -- generez-la d'abord")

    lat: dict[str, list[float]] = defaultdict(list)
    errors: dict[str, int] = defaultdict(int)
    deadline = time.perf_counter() + args.duration

    print(f"cible {args.base}  commune {args.insee}  "
          f"{args.concurrency} clients paralleles  {args.duration:.0f}s\n")

    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)
    async with httpx.AsyncClient(base_url="", timeout=30, limits=limits) as client:
        t0 = time.perf_counter()
        await asyncio.gather(*[
            _worker(client, args.base, args.insee, deadline, lat, errors)
            for _ in range(args.concurrency)
        ])
        elapsed = time.perf_counter() - t0

    total = sum(len(v) for v in lat.values())
    total_err = sum(errors.values())
    print(f"{'endpoint':<10} {'n':>7} {'req/s':>8} {'p50 ms':>9} {'p90 ms':>9} {'p99 ms':>9} {'err':>5}")
    print("-" * 62)
    for kind, _ in MIX:
        v = lat[kind]
        print(f"{kind:<10} {len(v):>7} {len(v) / elapsed:>8.1f} "
              f"{_pct(v, 50):>9.0f} {_pct(v, 90):>9.0f} {_pct(v, 99):>9.0f} {errors[kind]:>5}")
    print("-" * 62)
    allv = [x for v in lat.values() for x in v]
    print(f"{'TOTAL':<10} {total:>7} {total / elapsed:>8.1f} "
          f"{_pct(allv, 50):>9.0f} {_pct(allv, 90):>9.0f} {_pct(allv, 99):>9.0f} {total_err:>5}")
    print(f"\n{total / elapsed:.0f} req/s soutenues, {100 * total_err / max(total, 1):.1f} % d'erreurs "
          f"(sur {elapsed:.1f}s).")


if __name__ == "__main__":
    asyncio.run(main())
