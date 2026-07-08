"""B 站下载器：使用 bilibili-api-python 解析 DASH 流，下载音频后转 mp3。

支持的 URL 形式：
    https://www.bilibili.com/video/BV1xx411c7mD/
    https://www.bilibili.com/video/BV1xx411c7mD/?p=2     (分 P)
    https://www.bilibili.com/video/av170001/
    https://b23.tv/xxxxx                                   (短链)
"""

import os
import re
import subprocess
import threading
from typing import Optional

import requests
import bilibili_api
from bilibili_api import video, sync

# bilibili-api-python 17.x 需要显式选一个 HTTP 客户端
# 优先 httpx（同步友好，依赖少），退回 aiohttp / curl_cffi
for _name in ('httpx', 'aiohttp', 'curl_cffi'):
    if _name in bilibili_api.get_registered_clients():
        bilibili_api.select_client(_name)
        break

from app.taskmanager import DownloadStatus, taskManager
from app.utils import sanitize_filename, normalize_loudness


class BilibiliScraper:
    def __init__(self):
        self.data_dir = self._ensure_data_dir()
        # B 站 CDN 强制要求 Referer，UA 也要看起来像浏览器
        self.headers = {
            'User-Agent': ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                           '(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36'),
            'Referer': 'https://www.bilibili.com/',
        }

    def _ensure_data_dir(self):
        d = os.environ.get("V2M_DATA_DIR") or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'data')
        os.makedirs(d, exist_ok=True)
        return d

    def download_media(self, url: str, task_id: str, orgUrl: str = "") -> str:
        ts = DownloadStatus()
        ts.is_downloading = True
        ts.task_id = task_id
        ts.url = url
        taskManager.tasks[task_id] = ts
        threading.Thread(target=self._run, args=(url, ts), daemon=True).start()
        return task_id

    def get_available_formats(self, url: str, task_status: DownloadStatus) -> Optional[str]:
        if task_status:
            task_status.add_log("B 站暂不支持格式选择")
        return "B 站暂不支持格式选择，将下载最高质量音频"

    # ── internals ───────────────────────────────────────────────
    def _run(self, url: str, ts: DownloadStatus):
        try:
            ts.update_status("正在解析 B 站链接...")
            ts.update_progress(10)
            task_dir = os.path.join(self.data_dir, ts.task_id)
            os.makedirs(task_dir, exist_ok=True)

            ok = self._scrape(url, task_dir, ts)
            if ok:
                ts.update_status("下载完成")
                ts.update_progress(100)
                ts.mark_finished(True, "下载完成")
            else:
                ts.mark_finished(False, "下载失败")
            taskManager.save_task_info(ts)
        except Exception as e:
            ts.update_status(f"错误: {e}")
            ts.mark_finished(False, str(e))
            taskManager.save_task_info(ts)

    def _resolve_short(self, url: str) -> str:
        if 'b23.tv' not in url:
            return url
        try:
            r = requests.get(url, headers=self.headers,
                             allow_redirects=True, timeout=15, stream=True)
            r.close()
            return r.url
        except Exception:
            return url

    def _parse_id(self, url: str):
        """返回 (bvid, aid, page_idx)，page_idx 是 0-indexed。"""
        url = self._resolve_short(url)
        bvid = aid = None
        m = re.search(r'(BV[0-9A-Za-z]+)', url)
        if m:
            bvid = m.group(1)
        m2 = re.search(r'/av(\d+)', url, re.I)
        if m2:
            aid = int(m2.group(1))
        m3 = re.search(r'[?&]p=(\d+)', url)
        page_idx = (int(m3.group(1)) - 1) if m3 else 0
        return bvid, aid, page_idx

    def _scrape(self, url: str, dst_path: str, ts: DownloadStatus) -> bool:
        bvid, aid, page_idx = self._parse_id(url)
        if not bvid and not aid:
            ts.add_log(f"无法从 URL 提取视频 ID: {url}")
            return False

        v = video.Video(bvid=bvid) if bvid else video.Video(aid=aid)

        ts.update_status("正在获取视频信息...")
        ts.update_progress(20)
        info = sync(v.get_info())
        title_raw = info.get('title') or 'unknown'
        pages = info.get('pages') or []
        if len(pages) > 1 and 0 <= page_idx < len(pages):
            part = pages[page_idx].get('part') or ''
            if part:
                title_raw = f"{title_raw}_{part}"
        title = sanitize_filename(title_raw)
        ts.add_log(f"标题: {title_raw}")

        ts.update_status("正在获取下载地址...")
        ts.update_progress(40)
        url_data = sync(v.get_download_url(page_idx))
        dash = url_data.get('dash')
        if not dash:
            ts.add_log("接口未返回 DASH 流（可能为大会员限定或老视频）")
            return False
        audios = dash.get('audio') or []
        if not audios:
            ts.add_log("无音频流")
            return False
        # 取码率最高的音频
        audios.sort(key=lambda a: a.get('bandwidth', 0), reverse=True)
        a0 = audios[0]
        audio_url = a0['baseUrl']
        ts.add_log(f"音频码率: {a0.get('bandwidth')} bps")

        m4a_path = os.path.join(dst_path, f'{title}.m4a')
        mp3_path = os.path.join(dst_path, f'{title}.mp3')

        ts.update_status("正在下载音频...")
        ts.update_progress(60)
        with requests.get(audio_url, headers=self.headers, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(m4a_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)
        ts.add_log(f"音频下载完成: {os.path.basename(m4a_path)}")

        ts.update_status("正在转码 mp3...")
        ts.update_progress(80)
        r2 = subprocess.run(
            ['ffmpeg', '-y', '-i', m4a_path, '-vn',
             '-acodec', 'libmp3lame', '-ab', '192k', '-ar', '44100', mp3_path],
            capture_output=True,
        )
        if r2.returncode != 0 or not os.path.exists(mp3_path):
            ts.add_log(f"m4a→mp3 转换失败: {r2.stderr.decode('utf-8', errors='ignore')[-300:]}")
            return False
        try:
            os.remove(m4a_path)
        except OSError:
            pass

        ts.audio_path = mp3_path

        ts.add_log("正在进行 EBU R128 响度归一化 (-16 LUFS)...")
        norm_path = mp3_path + '_normalized.mp3'
        normalize_loudness(mp3_path, norm_path)
        os.replace(norm_path, mp3_path)
        ts.add_log(f"音频下载和响度归一化成功: {os.path.basename(mp3_path)}")
        ts.update_progress(100)
        return True


def _test():
    s = BilibiliScraper()
    s.download_media('https://www.bilibili.com/video/BV1GJ411x7h7/', 'bili-test')


if __name__ == '__main__':
    _test()
