"""Create (or update) the planned workouts of a plan file in Intervals.icu.

Run by the "Push training plan" GitHub Action. Reads ATHLETE_ID / INTERVALS_KEY
from the repository secrets. Events are matched on external_id, so re-running
updates the same sessions instead of duplicating them.

PRUNE=true also removes sessions that this plan created earlier but that are no
longer in the plan file (a session moved to another day, for example). It only
ever touches events whose external_id carries this plan's prefix, dated today or
later: past sessions and anything you created yourself are never deleted.
"""
import datetime as dt
import json
import os
import re
import sys

import requests

API = "https://intervals.icu/api/v1"


def plan_prefix(events):
    m = re.match(r"^(.*?-)\d{4}-\d{2}-\d{2}-", events[0]["external_id"])
    if not m:
        raise SystemExit("external_id inattendu, suppression impossible : " + events[0]["external_id"])
    prefix = m.group(1)
    if not all(e["external_id"].startswith(prefix) for e in events):
        raise SystemExit("Toutes les séances du fichier doivent partager le préfixe " + prefix)
    return prefix


def stale_events(base, auth, events):
    """Plan-owned future events that are no longer in the plan file."""
    prefix = plan_prefix(events)
    wanted = {e["external_id"] for e in events}
    today = dt.date.today()
    r = requests.get(f"{base}/events",
                     params={"oldest": today.isoformat(),
                             "newest": (today + dt.timedelta(days=400)).isoformat()},
                     auth=auth, timeout=60)
    r.raise_for_status()
    out = []
    for e in r.json():
        ext = e.get("external_id") or ""
        day = (e.get("start_date_local") or "")[:10]
        if ext.startswith(prefix) and ext not in wanted and day >= today.isoformat():
            out.append(e)
    return out


def main() -> int:
    athlete = os.environ["ATHLETE_ID"]
    key = os.environ["INTERVALS_KEY"]
    plan_file = os.environ.get("PLAN_FILE") or "plan-automne-2026.json"
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    prune = os.environ.get("PRUNE", "false").lower() == "true"

    with open(plan_file, encoding="utf-8") as f:
        events = json.load(f)
    if not events:
        print("Plan vide, rien à faire.")
        return 0

    first = events[0]["start_date_local"][:10]
    last = events[-1]["start_date_local"][:10]
    print(f"{len(events)} séances du {first} au {last} ({plan_file})")

    auth = ("API_KEY", key)
    base = f"{API}/athlete/{athlete}"

    if dry_run:
        for e in events:
            print(f"  {e['start_date_local'][:10]}  {e['type']:<14} {e['name']}")
        if prune:
            for e in stale_events(base, auth, events):
                print(f"  À SUPPRIMER  {e['start_date_local'][:10]}  {e.get('name', '')}")
        print("Simulation : rien n'a été envoyé ni supprimé.")
        return 0

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

    deleted = 0
    if prune:
        for e in stale_events(base, auth, events):
            d = requests.delete(f"{base}/events/{e['id']}", auth=auth, timeout=60)
            if d.ok:
                deleted += 1
                print(f"  supprimée  {e['start_date_local'][:10]}  {e.get('name', '')}")
            else:
                problems += 1
                print(f"  ÉCHEC suppression {e['start_date_local'][:10]} {e.get('name', '')} "
                      f"({d.status_code})")
        print(f"{deleted} séances retirées du calendrier (déplacées ou supprimées du plan).")

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
