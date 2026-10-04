"""Create (or update) the planned workouts of a plan file in Intervals.icu.

Run by the "Push training plan" GitHub Action. Reads ATHLETE_ID / INTERVALS_KEY
from the repository secrets. Never deletes anything: events are matched on
external_id, so re-running updates the same sessions instead of duplicating them.
"""
import json
import os
import sys

import requests

API = "https://intervals.icu/api/v1"


def main() -> int:
    athlete = os.environ["ATHLETE_ID"]
    key = os.environ["INTERVALS_KEY"]
    plan_file = os.environ.get("PLAN_FILE") or "plan-automne-2026.json"
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    with open(plan_file, encoding="utf-8") as f:
        events = json.load(f)
    if not events:
        print("Plan vide, rien à faire.")
        return 0

    first = events[0]["start_date_local"][:10]
    last = events[-1]["start_date_local"][:10]
    print(f"{len(events)} séances du {first} au {last} ({plan_file})")

    if dry_run:
        for e in events:
            print(f"  {e['start_date_local'][:10]}  {e['type']:<14} {e['name']}")
        print("Simulation : rien n'a été envoyé.")
        return 0

    auth = ("API_KEY", key)
    base = f"{API}/athlete/{athlete}"

    r = requests.post(f"{base}/events/bulk", params={"upsert": "true"},
                      json=events, auth=auth, timeout=120)
    if r.status_code in (404, 405):
        # Fallback: one event at a time, skipping sessions already created.
        print("Envoi groupé indisponible, envoi séance par séance.")
        existing = requests.get(f"{base}/events", params={"oldest": first, "newest": last},
                                auth=auth, timeout=60)
        existing.raise_for_status()
        known = {e.get("external_id") for e in existing.json()}
        created = []
        for e in events:
            if e["external_id"] in known:
                continue
            one = requests.post(f"{base}/events", json=e, auth=auth, timeout=60)
            one.raise_for_status()
            created.append(one.json())
    else:
        if not r.ok:
            print(f"Erreur Intervals.icu {r.status_code} : {r.text[:500]}")
            return 1
        created = r.json()

    # Verification: a ride whose text Intervals could not parse has no duration.
    problems = 0
    for e in sorted(created, key=lambda x: x.get("start_date_local", "")):
        minutes = round((e.get("moving_time") or 0) / 60)
        load = e.get("icu_training_load")
        flag = ""
        if e.get("type") in ("Ride", "VirtualRide") and minutes == 0:
            flag = "  <-- texte non reconnu"
            problems += 1
        print(f"  {e.get('start_date_local', '')[:10]}  {e.get('name', '')[:40]:<40} "
              f"{minutes:>4} min  charge {load}{flag}")
    print(f"{len(created)} séances créées ou mises à jour, {problems} à vérifier.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
