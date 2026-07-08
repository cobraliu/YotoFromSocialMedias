"""In-memory Yoto upload job tracking (short-lived; no disk persistence)."""
from __future__ import annotations

import uuid
from typing import Optional


class UploadJob:
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.progress = 0
        self.status = "初始化"
        self.success: Optional[bool] = None
        self.error_message = ""
        self.log: list[str] = []
        self.done = False

    def update(self, progress: int, status: str):
        self.progress = progress
        self.status = status

    def add_log(self, msg: str):
        self.log.append(msg)
        print(f"[yoto:{self.job_id}] {msg}")

    def finish(self, success: bool, message: str):
        self.success = success
        self.done = True
        self.progress = 100 if success else self.progress
        self.status = message
        if not success:
            self.error_message = message

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "progress": self.progress,
            "status": self.status,
            "success": self.success,
            "error_message": self.error_message,
            "log": self.log[-15:],
            "done": self.done,
        }


class JobManager:
    def __init__(self):
        self.jobs: dict[str, UploadJob] = {}

    def create(self) -> UploadJob:
        job = UploadJob(uuid.uuid4().hex[:12])
        self.jobs[job.job_id] = job
        return job

    def get(self, job_id: str):
        job = self.jobs.get(job_id)
        return job.to_dict() if job else None


jobManager = JobManager()
