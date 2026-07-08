from app.yoto_jobs import JobManager


def test_job_lifecycle():
    mgr = JobManager()
    job = mgr.create()
    assert mgr.get(job.job_id)["progress"] == 0
    job.update(40, "上传中")
    job.add_log("hi")
    assert mgr.get(job.job_id)["progress"] == 40
    assert mgr.get(job.job_id)["status"] == "上传中"
    assert "hi" in mgr.get(job.job_id)["log"]
    job.finish(True, "完成")
    d = mgr.get(job.job_id)
    assert d["success"] is True and d["done"] is True
    assert d["progress"] == 100


def test_finish_failure_keeps_progress_and_sets_error():
    mgr = JobManager()
    job = mgr.create()
    job.update(55, "上传中")
    job.finish(False, "boom")
    d = mgr.get(job.job_id)
    assert d["success"] is False
    assert d["progress"] == 55
    assert d["error_message"] == "boom"


def test_get_unknown():
    assert JobManager().get("nope") is None
