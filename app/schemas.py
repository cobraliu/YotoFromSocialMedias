from pydantic import BaseModel, HttpUrl
from typing import List, Optional, Dict, Any
from datetime import datetime


class DownloadRequest(BaseModel):
    """下载请求模型"""
    url: str
    
    class Config:
        schema_extra = {
            "example": {
                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            }
        }


class TaskResponse(BaseModel):
    """任务响应模型"""
    task_id: str
    message: str
    
    class Config:
        schema_extra = {
            "example": {
                "task_id": "550e8400-e29b-41d4-a716-446655440000",
                "message": "任务已创建"
            }
        }


class TaskStatus(BaseModel):
    """任务状态模型"""
    task_id: str
    progress: int
    status: str
    is_downloading: bool
    success: Optional[bool] = None
    error_message: Optional[str] = None
    log_messages: List[str]
    has_video: bool
    has_audio: bool
    video_filename: Optional[str] = None
    audio_filename: Optional[str] = None
    
    class Config:
        schema_extra = {
            "example": {
                "task_id": "550e8400-e29b-41d4-a716-446655440000",
                "progress": 75,
                "status": "正在下载视频...",
                "is_downloading": True,
                "success": None,
                "error_message": None,
                "log_messages": ["开始下载", "获取视频信息成功", "正在下载视频..."],
                "has_video": False,
                "has_audio": False,
                "video_filename": None,
                "audio_filename": None
            }
        }


class HistoryTask(BaseModel):
    """历史任务模型"""
    task_id: str
    url: str
    start_time: str
    end_time: str
    status: str
    success: bool
    error_message: Optional[str] = None
    has_video: bool
    has_audio: bool
    video_filename: Optional[str] = None
    audio_filename: Optional[str] = None
    
    class Config:
        schema_extra = {
            "example": {
                "task_id": "550e8400-e29b-41d4-a716-446655440000",
                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "start_time": "2023-01-01 12:00:00",
                "end_time": "2023-01-01 12:05:30",
                "status": "完成",
                "success": True,
                "error_message": None,
                "has_video": True,
                "has_audio": True,
                "video_filename": "video.mp4",
                "audio_filename": "audio.mp3"
            }
        }


class ErrorResponse(BaseModel):
    """错误响应模型"""
    detail: str
    
    class Config:
        schema_extra = {
            "example": {
                "detail": "任务不存在"
            }
        }