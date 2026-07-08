import os
import sys
import subprocess
import threading
import tempfile
import shutil
import requests
import json
import uuid
import io
from pathlib import Path
from typing import Dict, Optional, Tuple, List, Any
from datetime import datetime
from app.taskmanager import DownloadStatus, taskManager
from app.utils import sanitize_filename, normalize_loudness

class YTDownloader:
    def __init__(self):
        self.data_dir = self.ensure_data_dir()
        self.yt_dlp_path = self.get_yt_dlp_path()
        self.ffmpeg_path = self.get_ffmpeg_path()

    def ensure_data_dir(self):
        """确保data目录存在（可用 V2M_DATA_DIR 环境变量覆盖默认位置）"""
        data_dir = os.environ.get("V2M_DATA_DIR")
        if not data_dir:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            data_dir = os.path.join(current_dir, 'data')
        os.makedirs(data_dir, exist_ok=True)
        return data_dir

    def get_yt_dlp_path(self):
        """获取 yt-dlp 可执行文件路径"""
        return 'yt-dlp'

    def get_ffmpeg_path(self):
        """获取 ffmpeg 可执行文件路径"""
        return 'ffmpeg'

    def get_video_info(self, url: str, task_status: DownloadStatus) -> Optional[Dict]:
        """获取视频详细信息"""
        try:
            cmd = [
                self.yt_dlp_path,
                '--dump-json',
                '--no-warnings',
                url
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, 
                                  timeout=30)
            
            if result.returncode != 0:
                task_status.add_log(f"获取视频信息失败: {result.stderr}")
                return None
            
            return json.loads(result.stdout)
            
        except Exception as e:
            task_status.add_log(f"解析视频信息错误: {str(e)}")
            return None

    def get_available_formats(self, url: str, task_status: DownloadStatus) -> Optional[str]:
        """获取可用格式"""
        try:
            cmd = [
                self.yt_dlp_path,
                '--list-formats',
                '--no-warnings',
                url
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, 
                                  timeout=30)
            
            if result.returncode == 0:
                return result.stdout
            return None
            
        except Exception as e:
            task_status.add_log(f"获取格式列表错误: {str(e)}")
            return None

    def run_command_with_progress(self, cmd: List[str], task_status: DownloadStatus, progress_type: str = "download") -> bool:
        """运行命令并捕获进度"""
        try:
            task_status.add_log(f"执行命令: {' '.join(cmd)}")
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr= subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1,
            )
            
            for line in process.stdout:
                if line.strip():
                    task_status.add_log(line.strip())
                    
                    # 处理下载进度
                    if progress_type == "download" and "[download]" in line and "%" in line:
                        try:
                            percent_str = line.split("%")[0].split()[-1]
                            percent = int(float(percent_str))
                            task_status.update_progress(percent)
                        except:
                            pass
                    # 处理ffmpeg进度
                    elif progress_type == "ffmpeg" and "time=" in line:
                        try:
                            # 从ffmpeg输出中提取时间信息
                            time_str = line.split("time=")[1].split()[0]
                            # 将时间转换为秒
                            h, m, s = map(float, time_str.split(':'))
                            current_seconds = h * 3600 + m * 60 + s
                            
                            # 如果我们知道总时长，可以计算百分比
                            if hasattr(task_status, 'total_duration') and task_status.total_duration > 0:
                                percent = min(int(current_seconds / task_status.total_duration * 100), 100)
                                task_status.update_progress(percent)
                        except:
                            pass
            
            process.wait()
            return process.returncode == 0
            
        except Exception as e:
            task_status.add_log(f"命令执行错误: {str(e)}")
            return False

    def convert_to_mp3_with_ffmpeg(self, input_file: str, output_file: str, task_status: DownloadStatus) -> bool:
        """使用ffmpeg将音频文件转换为MP3格式"""
        try:
            if not self.ffmpeg_path:
                task_status.add_log("ffmpeg未找到，无法转换音频")
                return False
                
            task_status.add_log(f"使用ffmpeg转换音频: {os.path.basename(input_file)} -> {os.path.basename(output_file)}")
            
            # 获取输入文件的总时长，用于计算进度
            duration_cmd = [
                self.ffmpeg_path,
                '-i', input_file,
                '-hide_banner'
            ]
            
            try:
                result = subprocess.run(duration_cmd, capture_output=True, text=True, timeout=10)
                # 从ffmpeg输出中提取时长信息
                for line in result.stderr.split('\n'):
                    if "Duration:" in line:
                        time_str = line.split("Duration:")[1].split(",")[0].strip()
                        h, m, s = map(float, time_str.split(':'))
                        task_status.total_duration = h * 3600 + m * 60 + s
                        break
            except:
                task_status.total_duration = 0
            
            # 转换命令
            cmd = [
                self.ffmpeg_path,
                '-i', input_file,
                '-vn',  # 不包含视频
                '-acodec', 'libmp3lame',  # 使用MP3编码器
                '-ab', '192k',  # 比特率
                '-ar', '44100',  # 采样率
                '-y',  # 覆盖输出文件
                output_file
            ]
            
            return self.run_command_with_progress(cmd, task_status, progress_type="ffmpeg")
            
        except Exception as e:
            task_status.add_log(f"音频转换错误: {str(e)}")
            return False

    def download_media(self, url: str, task_id: str, orgUrl: str = "") -> str:
        """下载媒体文件，返回任务ID"""
        # 创建新的任务状态
        task_status = DownloadStatus()
        task_status.is_downloading = True
        task_status.task_id = task_id
        task_status.url = url
        taskManager.tasks[task_id] = task_status
        
        # 在新线程中执行下载
        thread = threading.Thread(target=self._download_media_thread, args=(url, task_status))
        thread.daemon = True
        thread.start()
        
        return task_id

    def _download_media_thread(self, url: str, task_status: DownloadStatus):
        """在线程中执行下载任务"""
        try:
            # 创建任务专用目录
            task_dir = os.path.join(self.data_dir, task_status.task_id)
            os.makedirs(task_dir, exist_ok=True)
            
            # 获取视频信息
            task_status.update_status("正在获取视频信息...")
            video_info = self.get_video_info(url, task_status)
            if not video_info:
                raise Exception("无法获取视频信息")
            
            title = sanitize_filename(video_info.get('title', 'unknown'))
            
            # 下载最佳视频
            task_status.update_status("正在下载视频...")
            video_cmd = [
                self.yt_dlp_path,
                '-f', 'bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best',
                '-o', os.path.join(task_dir, f'{title}.%(ext)s'),
                '--no-warnings',
                '--newline',
                url
            ]
            
            if not self.run_command_with_progress(video_cmd, task_status):
                raise Exception("视频下载失败")
            
            # 查找下载的视频文件
            for file in os.listdir(task_dir):
                if file.startswith(title) and (file.endswith('.mp4') or file.endswith('.mkv') or file.endswith('.webm')):
                    task_status.video_path = os.path.join(task_dir, file)
                    break
            
            if not os.path.exists(task_status.video_path):
                raise Exception(f"视频文件 {task_status.video_path} 未找到")
            
            # 提取并转换音频
            task_status.update_status("正在提取音频...")
            task_status.update_progress(0)
            
            # 首先尝试下载原始音频格式
            audio_download_cmd = [
                self.yt_dlp_path,
                '-f', 'bestaudio',
                '-o', os.path.join(task_dir, f'{title}_audio.%(ext)s'),
                '--no-warnings',
                '--newline',
                url
            ]
            
            original_audio_path = None
            if self.run_command_with_progress(audio_download_cmd, task_status):
                # 查找下载的音频文件
                for file in os.listdir(task_dir):
                    if file.startswith(f"{title}_audio"):
                        original_audio_path = os.path.join(task_dir, file)
                        task_status.add_log(f"下载原始音频成功: {file}")
                        break
            
            # 如果没有找到音频文件，使用视频文件作为音频源
            if not original_audio_path:
                task_status.add_log("未找到独立音频，将从视频中提取")
                original_audio_path = task_status.video_path
            
            # 使用ffmpeg转换为MP3
            mp3_output = os.path.join(task_dir, f"{title}.mp3")
            task_status.update_status("正在使用ffmpeg转换为MP3...")
            
            if self.ffmpeg_path and self.convert_to_mp3_with_ffmpeg(original_audio_path, mp3_output, task_status):
                # 音频转换成功后，立即进行音量调节
                task_status.add_log("正在进行 EBU R128 响度归一化 (-16 LUFS)...")
                volume_adjusted_path = os.path.join(task_dir, f"{title}_normalized.mp3")
                normalize_loudness(mp3_output, volume_adjusted_path)
                os.replace(volume_adjusted_path, mp3_output)
                task_status.audio_path = mp3_output
                task_status.add_log(f"音频转换和响度归一化成功: {os.path.basename(mp3_output)}")
            else:
                # 如果ffmpeg转换失败，尝试使用yt-dlp的内置功能
                task_status.add_log("ffmpeg转换失败，尝试使用yt-dlp内置功能...")
                
                audio_cmd = [
                    self.yt_dlp_path,
                    '-x',  # 提取音频
                    '--audio-format', 'mp3',  # 转换为 MP3
                    '--audio-quality', '0',  # 最高质量
                    '-o', os.path.join(task_dir, f'{title}_ytdlp.%(ext)s'),
                    '--no-warnings',
                    '--newline',
                    url
                ]
                
                if self.run_command_with_progress(audio_cmd, task_status):
                    # 检查生成的音频文件
                    for file in os.listdir(task_dir):
                        if file.startswith(f"{title}_ytdlp") and file.endswith('.mp3'):
                            original_audio_path = os.path.join(task_dir, file)
                            
                            task_status.add_log("正在进行 EBU R128 响度归一化 (-16 LUFS)...")
                            volume_adjusted_path = os.path.join(task_dir, f"{title}_normalized.mp3")
                            normalize_loudness(original_audio_path, volume_adjusted_path)
                            os.replace(volume_adjusted_path, original_audio_path)
                            task_status.audio_path = original_audio_path
                            task_status.add_log(f"使用yt-dlp转换和响度归一化成功: {file}")
                            break
            
            # 如果仍然没有音频文件，使用原始音频格式
            if not task_status.audio_path or not os.path.exists(task_status.audio_path):
                if original_audio_path and original_audio_path != task_status.video_path:
                    task_status.audio_path = original_audio_path
                    task_status.add_log(f"使用原始音频格式: {os.path.basename(original_audio_path)}")
                else:
                    # 最后的备选方案：复制视频文件并重命名为MP3
                    fallback_audio = os.path.join(task_dir, f"{title}_fallback.mp3")
                    try:
                        shutil.copy2(task_status.video_path, fallback_audio)
                        task_status.audio_path = fallback_audio
                        task_status.add_log("使用视频文件作为备选音频")
                    except Exception as e:
                        task_status.add_log(f"文件复制失败: {str(e)}")
            
            if not task_status.audio_path or not os.path.exists(task_status.audio_path):
                raise Exception("音频文件处理失败")
            
            task_status.update_status("转换完成")
            task_status.mark_finished(True, "转换完成")
            
            # 保存任务信息到JSON文件
            taskManager.save_task_info(task_status)
            
        except Exception as e:
            error_msg = f"错误: {str(e)}"
            task_status.update_status(error_msg)
            task_status.mark_finished(False, error_msg)
            # 即使失败也保存任务信息
            taskManager.save_task_info(task_status)

    def cleanup_old_tasks(self, max_age_hours: int = 24):
        """清理旧任务"""
        # 实际应用中可以添加定期清理旧任务的逻辑
        pass
