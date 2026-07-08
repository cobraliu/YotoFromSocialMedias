import os, sys, time, json
from datetime import datetime

from typing import Dict, Optional, Tuple, List, Any

class DownloadStatus:
    """下载状态跟踪类"""
    def __init__(self):
        self.progress = 0
        self.status = "初始化"
        self.is_downloading = False
        self.video_path = ""
        self.audio_path = ""
        self.log_messages = []
        self.success = False
        self.error_message = ""
        self.task_id = ""
        self.url = ""
        self.start_time = datetime.now()
        self.end_time = None

    def add_log(self, message: str):
        """添加日志消息"""
        self.log_messages.append(message)
        print(f"[{self.task_id}] {message}")

    def update_progress(self, progress: int):
        """更新进度"""
        self.progress = progress

    def update_status(self, status: str):
        """更新状态"""
        self.status = status

    def mark_finished(self, success: bool, message: str):
        """标记任务完成"""
        self.success = success
        if not success:
            self.error_message = message
        self.is_downloading = False
        self.status = message
        self.end_time = datetime.now()


class TaskManager:
    def __init__(self):
        self.tasks = {}
        self.data_dir = self.ensure_data_dir()

    def ensure_data_dir(self):
        """确保data目录存在。可用 V2M_DATA_DIR 环境变量覆盖默认位置（便于测试
        或将数据放到其它磁盘）。"""
        data_dir = os.environ.get("V2M_DATA_DIR")
        if not data_dir:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            data_dir = os.path.join(current_dir, 'data')
        os.makedirs(data_dir, exist_ok=True)
        return data_dir
    
    def get_file_path(self, task_id: str, file_type: str) -> Optional[str]:
        """获取文件路径"""
        task_dir = os.path.join(self.data_dir, task_id)
        
        if task_id in self.tasks:
            task = self.tasks[task_id]
            if file_type == "video" and task.video_path and os.path.exists(task.video_path):
                return task.video_path
            elif file_type == "audio" and task.audio_path and os.path.exists(task.audio_path):
                return task.audio_path
        else:
            # 尝试从data目录加载已完成的任务文件
            info_file = os.path.join(task_dir, 'task_info.json')
            if os.path.exists(info_file):
                try:
                    with open(info_file, 'r', encoding='utf-8') as f:
                        task_info = json.load(f)
                    
                    if file_type == "video" and task_info.get('video_path'):
                        video_path = os.path.join(task_dir, task_info['video_path'])
                        if os.path.exists(video_path):
                            return video_path
                    elif file_type == "audio" and task_info.get('audio_path'):
                        audio_path = os.path.join(task_dir, task_info['audio_path'])
                        if os.path.exists(audio_path):
                            return audio_path
                except:
                    pass
        return None

    def save_task_info(self, task_status: DownloadStatus):
        """原子写入任务 JSON：先写 .tmp 再 rename，避免半写状态被读到"""
        try:
            task_dir = os.path.join(self.data_dir, task_status.task_id)
            os.makedirs(task_dir, exist_ok=True)
            info_file = os.path.join(task_dir, 'task_info.json')
            tmp_file = info_file + '.tmp'

            task_info = {
                'task_id': task_status.task_id,
                'url': task_status.url,
                'start_time': task_status.start_time.strftime("%Y-%m-%d %H:%M:%S"),
                'end_time': task_status.end_time.strftime("%Y-%m-%d %H:%M:%S") if task_status.end_time else None,
                'status': task_status.status,
                'success': task_status.success,
                'error_message': task_status.error_message,
                'video_path': os.path.basename(task_status.video_path) if task_status.video_path else None,
                'audio_path': os.path.basename(task_status.audio_path) if task_status.audio_path else None,
                'log_messages': task_status.log_messages
            }

            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(task_info, f, ensure_ascii=False, indent=2)
            os.replace(tmp_file, info_file)

        except Exception as e:
            print(f"保存任务信息失败: {str(e)}")


    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务状态"""
        if task_id not in self.tasks:
            # 尝试从data目录加载已完成的任务
            task_dir = os.path.join(self.data_dir, task_id)
            info_file = os.path.join(task_dir, 'task_info.json')
            
            if os.path.exists(info_file):
                try:
                    with open(info_file, 'r', encoding='utf-8') as f:
                        task_info = json.load(f)
                    
                    # 创建任务状态对象
                    task_status = DownloadStatus()
                    task_status.task_id = task_id
                    task_status.url = task_info.get('url', '')
                    task_status.status = task_info.get('status', '')
                    task_status.success = task_info.get('success', False)
                    task_status.error_message = task_info.get('error_message', '')
                    task_status.log_messages = task_info.get('log_messages', [])
                    
                    # 设置文件路径
                    video_filename = task_info.get('video_path')
                    audio_filename = task_info.get('audio_path')
                    if video_filename:
                        task_status.video_path = os.path.join(task_dir, video_filename)
                    if audio_filename:
                        task_status.audio_path = os.path.join(task_dir, audio_filename)
                    
                    return {
                        "task_id": task_id,
                        "progress": 100 if task_info.get('success') else 0,
                        "status": task_info.get('status', ''),
                        "is_downloading": False,
                        "success": task_info.get('success'),
                        "error_message": task_info.get('error_message', ''),
                        "log_messages": task_info.get('log_messages', []),
                        "has_video": bool(task_status.video_path and os.path.exists(task_status.video_path)),
                        "has_audio": bool(task_status.audio_path and os.path.exists(task_status.audio_path)),
                        "video_path": task_status.video_path if task_status.video_path and os.path.exists(task_status.video_path) else "",
                        "audio_path": task_status.audio_path if task_status.audio_path and os.path.exists(task_status.audio_path) else "",
                        "video_filename": os.path.basename(task_status.video_path) if task_status.video_path else "",
                        "audio_filename": os.path.basename(task_status.audio_path) if task_status.audio_path else ""
                    }
                except Exception as e:
                    print(f"加载历史任务信息失败: {str(e)}")
            return None
            
        task = self.tasks[task_id]
        return {
            "task_id": task_id,
            "progress": task.progress,
            "status": task.status,
            "is_downloading": task.is_downloading,
            "success": task.success,
            "error_message": task.error_message,
            "log_messages": task.log_messages[-10:] if task.log_messages else [],  # 只返回最近的10条日志
            "has_video": bool(task.video_path and os.path.exists(task.video_path)),
            "has_audio": bool(task.audio_path and os.path.exists(task.audio_path)),
            "video_path": task.video_path if task.video_path and os.path.exists(task.video_path) else "",
            "audio_path": task.audio_path if task.audio_path and os.path.exists(task.audio_path) else "",
            "video_filename": os.path.basename(task.video_path) if task.video_path else "",
            "audio_filename": os.path.basename(task.audio_path) if task.audio_path else ""
        }
    def get_history_tasks(self) -> List[Dict[str, Any]]:
        """合并内存 + 磁盘的历史任务。

        内存优先：覆盖磁盘版本，确保刚完成但 task_info.json 尚未落盘 / 落盘
        过程中被读到的任务也能立即出现在列表里。
        """
        merged: Dict[str, Dict[str, Any]] = {}

        # ── 1. 磁盘 ──────────────────────────────────────────
        if os.path.exists(self.data_dir):
            for task_id in os.listdir(self.data_dir):
                task_dir = os.path.join(self.data_dir, task_id)
                info_file = os.path.join(task_dir, 'task_info.json')
                if not os.path.exists(info_file):
                    continue
                try:
                    with open(info_file, 'r', encoding='utf-8') as f:
                        task_info = json.load(f)
                except Exception:
                    continue
                merged[task_id] = {
                    'task_id': task_id,
                    'url': task_info.get('url', ''),
                    'start_time': task_info.get('start_time', ''),
                    'end_time': task_info.get('end_time', ''),
                    'status': task_info.get('status', ''),
                    'success': task_info.get('success', False),
                    'has_video': os.path.exists(os.path.join(task_dir, task_info.get('video_path', ''))) if task_info.get('video_path') else False,
                    'has_audio': os.path.exists(os.path.join(task_dir, task_info.get('audio_path', ''))) if task_info.get('audio_path') else False,
                    'video_path': task_info.get('video_path', ''),
                    'audio_path': task_info.get('audio_path', ''),
                    'audio_filename': os.path.basename(task_info.get('audio_path', '')) if task_info.get('audio_path') else ''
                }

        # ── 2. 内存中已完成的任务（覆盖/补充磁盘版本）─────
        for task_id, task in list(self.tasks.items()):
            if task.is_downloading:
                continue
            task_dir = os.path.join(self.data_dir, task_id)
            video_fn = os.path.basename(task.video_path) if task.video_path else ''
            audio_fn = os.path.basename(task.audio_path) if task.audio_path else ''
            merged[task_id] = {
                'task_id': task_id,
                'url': task.url,
                'start_time': task.start_time.strftime("%Y-%m-%d %H:%M:%S") if task.start_time else '',
                'end_time': task.end_time.strftime("%Y-%m-%d %H:%M:%S") if task.end_time else '',
                'status': task.status,
                'success': task.success,
                'has_video': bool(task.video_path) and os.path.exists(task.video_path),
                'has_audio': bool(task.audio_path) and os.path.exists(task.audio_path),
                'video_path': video_fn,
                'audio_path': audio_fn,
                'audio_filename': audio_fn,
            }

        history_tasks = list(merged.values())
        history_tasks.sort(key=lambda x: x.get('start_time', ''), reverse=True)
        return history_tasks
    

taskManager = TaskManager()