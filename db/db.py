import os
from typing import Any, Dict, List

from pymongo import MongoClient, UpdateOne


MONGO_URI = "mongodb+srv://heetopt_db_user:e89OVd6l63vfRgo6@cluster0.osmtldd.mongodb.net/?appName=Cluster0"
DB_NAME = "codeverse_backend"


_client: MongoClient | None = None


def get_db():
    """Return a reference to the MongoDB database."""
    global _client
    if _client is None:
        _client = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=5000,
            tlsAllowInvalidCertificates=True
        )
    return _client[DB_NAME]


# Leaderboard contains small amount of data for frontend display
def upsert_leaderboard(leaderboard: List[Dict[str, Any]]) -> None:
    """
    Update or insert leaderboard data.
    - Each team is stored as a document keyed by `team_id`.
    - If leaderboard is empty, clears the collection.
    """
    db = get_db()
    coll = db["leaderboard"]

    # If leaderboard is empty, clear the collection
    if not leaderboard:
        coll.delete_many({})
        return

    ops, team_ids = [], set()
    for entry in leaderboard:
        team_id = entry.get("team_id") or entry.get("team") or entry.get("id")
        if not team_id:
            # Skip invalid entries silently
            continue
        team_ids.add(team_id)
        ops.append(
            UpdateOne(
                {"team_id": team_id},
                {"$set": {**entry, "team_id": team_id}},
                upsert=True,
            )
        )

    # Execute updates and remove missing teams
    if ops:
        coll.bulk_write(ops, ordered=False)
    coll.delete_many({"team_id": {"$nin": list(team_ids)}})


# Team progress contains detailed progress data for each team
# Single Source of Truth for progress data
def upsert_team_progress(team_id: str, progress_document: Dict[str, Any]) -> None:
    """Upsert (insert/update) a team's progress document."""
    db = get_db()
    db["team_progress"].update_one(
        {"team_id": team_id},
        {"$set": {**progress_document, "team_id": team_id}},
        upsert=True,
    )