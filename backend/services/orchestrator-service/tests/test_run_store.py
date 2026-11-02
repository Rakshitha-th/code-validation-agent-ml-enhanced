from app.persistence.run_store import RunStore


def test_run_store_persists_and_reads_records(tmp_path):
    path = tmp_path / "runs.json"
    store = RunStore(str(path))
    store.upsert("p1", {"status": "completed"})

    assert RunStore(str(path)).get("p1") == {"status": "completed"}