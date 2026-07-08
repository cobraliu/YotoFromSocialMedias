'''
根据douyin链接爬取douyin的信息

支持的格式：
    2.38 复制打开抖音，看看【xxx】... https://v.douyin.com/H8OzdO3Mf5E/ 10/03 w@f.OK mQX:/
    6.69 n@d.aA Tyt:/ 06/25 # 明明渴求温柔偏偏覆水难收  https://v.douyin.com/USvLH647BJ4/ 复制此链接，打开Dou音搜索，直接观看视频！
    https://www.douyin.com/user/MS4wLjABAAAAI-qvNx_QEms-pXX6b6xinkLn0sQ1tWLQIbAT7Zjrc7c?from_tab_name=main&modal_id=7543684996550593818&relation=0&vid=7422587478625815846
    https://www.douyin.com/jingxuan?modal_id=7543973777944661291
    https://www.douyin.com/video/7543973777944661291
'''

import os, sys, time, json, traceback
import re
import tempfile
import subprocess
import requests
from json_repair import repair_json
import wget
import threading
from datetime import datetime
from typing import Dict, Optional, List, Any

from app.taskmanager import DownloadStatus, taskManager
from app.utils import sanitize_filename, normalize_loudness

class DouyinScraper:
    def __init__(self):
        self.data_dir = self.ensure_data_dir()
        
    def ensure_data_dir(self):
        """确保data目录存在（可用 V2M_DATA_DIR 环境变量覆盖默认位置）"""
        data_dir = os.environ.get("V2M_DATA_DIR")
        if not data_dir:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            data_dir = os.path.join(current_dir, 'data')
        os.makedirs(data_dir, exist_ok=True)
        return data_dir

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
            
            # 执行下载
            task_status.update_status("正在解析抖音链接...")
            task_status.update_progress(10)
            
            success = self.scrape(task_status.task_id, task_dir, url, task_status)
            
            if success:
                task_status.update_status("下载完成")
                task_status.update_progress(100)
                task_status.mark_finished(True, "下载完成")
            else:
                task_status.mark_finished(False, "下载失败")
            
            # 保存任务信息到JSON文件
            taskManager.save_task_info(task_status)
            
        except Exception as e:
            error_msg = f"错误: {str(e)}"
            task_status.update_status(error_msg)
            task_status.mark_finished(False, error_msg)
            # 即使失败也保存任务信息
            taskManager.save_task_info(task_status)

    def get_available_formats(self, url: str, task_status: DownloadStatus) -> Optional[str]:
        """获取可用格式（抖音暂不支持格式选择）"""
        if task_status:
            task_status.add_log("抖音视频暂不支持格式选择")
        return "抖音视频暂不支持格式选择，将下载最佳质量音频"

    def extractRealUrl(self, url):
        try:
            url = url.strip()
            match = re.search(r'https?://\S+', url)
            if match:
                url = match.group(0)

            headers = {
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36'
            }
            resp = requests.get(url, headers=headers, allow_redirects=True, timeout=15, stream=True)
            resp.close()
            real_url = resp.url
            print(f'extractRealUrl: {url} -> {real_url}')
            return real_url
        except Exception as e:
            print(f'extractRealUrl {url} fail: {e}')
            return url

    def decode_escaped_string(self, s):
        """递归解码转义字符串"""
        previous = s
        current = s.encode().decode('unicode_escape')

        # 如果解码后没有变化或不再包含转义序列，则返回
        if current == previous or '\\' not in current:
            return current
        return self.decode_escaped_string(current)
    
    def xxx(self, s):
        while '\\"' in s:
            s = s.replace('\\"', '\"')
        return s


    def extractVideoId(self, url):
        if 'modal_id' in url:
            videoId = url.split('modal_id=')[1].split('&')[0]
            return videoId
        if '/video/' in url:
            videoId = url.split('/video/')[1].split('/')[0].split('?')[0]
            return videoId
        return ""

    def parseVideoInfo(self, videoId, videoHtml):
        vidKey = f'\\"awemeId\\":\\"{videoId}\\"'
        hitPos = videoHtml.find(vidKey)
        if hitPos > -1:
            # 向两边找到包括这个key的dict范围
            # left 找到地一个空闲的{
            lefti = hitPos - 1
            leftCnt = 0 # 需要等于-1
            while lefti > -1:
                if videoHtml[lefti] == '{':
                    leftCnt -= 1
                    if leftCnt < 0:
                        break
                if videoHtml[lefti] == '}':
                    leftCnt += 1
                lefti -= 1
            
            righti = hitPos + 1
            rightCount = 0
            htmlLen = len(videoHtml)
            while righti < htmlLen:
                if videoHtml[righti] == '}':
                    rightCount -= 1
                    if rightCount < 0:
                        break
                if videoHtml[righti] == '{':
                    rightCount += 1
                righti += 1
            
            videoInfoStr = videoHtml[lefti:righti + 1]
            # urldecode
            # videoInfoStr = self.decode_escaped_string(videoInfoStr)
            videoInfoStr = videoInfoStr.replace('\\\"', '\"')
            print(videoInfoStr)
            with open('xxx.js', 'w') as f:
                f.write(videoInfoStr)
            vodeoInfo = json.loads(repair_json(videoInfoStr))
            return vodeoInfo
        return None
    
    def scrape(self, taskId, dstPath, url, task_status=None):
        try:
            if task_status:
                task_status.update_status("正在解析抖音链接...")
                task_status.update_progress(20)
                task_status.add_log(f"开始处理抖音链接: {url}")
            
            url = self.extractRealUrl(url)
            videoId = self.extractVideoId(url)
            jingxuanUrl = f'https://www.douyin.com/jingxuan?modal_id={videoId}'

            if task_status:
                task_status.update_status("正在获取视频信息...")
                task_status.update_progress(40)
                task_status.add_log(f"视频ID: {videoId}")

            with tempfile.TemporaryDirectory() as tmpDir:
                pageCmd = f"""curl '{jingxuanUrl}' \
      -H 'accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7' \
      -H 'upgrade-insecure-requests: 1' \
      -H 'user-agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36' """
                os.system(f'{pageCmd} > {tmpDir}/page.html')
                print(pageCmd)
                
                if task_status:
                    task_status.add_log("正在获取页面内容...")
                    task_status.update_progress(60)
                
                with open(f'{tmpDir}/page.html') as f:
                    pageHtml = f.read()
                    print(f'{jingxuanUrl} get {len(pageHtml)}')
                    
                    if task_status:
                        task_status.add_log(f"页面内容长度: {len(pageHtml)}")
                        task_status.update_status("正在解析视频信息...")
                        task_status.update_progress(70)
                    
                    videoInfo = self.parseVideoInfo(videoId, pageHtml)
                    if not videoInfo:
                        if task_status:
                            task_status.add_log("无法解析视频信息")
                        return False
                    if isinstance(videoInfo, list):
                        videoInfo = videoInfo[0]
                    elif isinstance(videoInfo, dict):
                        pass
                    else:
                        if task_status:
                            task_status.add_log("无法解析视频信息")
                        return False
                
                    title = sanitize_filename(videoInfo["desc"])
                    videoInfo = videoInfo["video"]
                    # print(videoInfo)
                    bitRateAudioList = videoInfo['bitRateAudioList']
                    bitRateList = videoInfo['bitRateList']
                    if len(bitRateAudioList) > 0:
                        bitRateAudioList = sorted(bitRateAudioList, key=lambda x:x["size"])
                        urlList = bitRateAudioList[0]["urlList"]
                        url = urlList[0]['src']
                        downloadUrl = self.decode_escaped_string(url)
                        dstFile = os.path.join(dstPath, f'{title}.mp3')
                    elif len(bitRateList) > 0:
                        bitRateList = sorted(bitRateList, key=lambda x:x["dataSize"])
                        urlList = bitRateList[0]["playAddr"]
                        url = urlList[0]['src']
                        # mp4
                        dstFile = os.path.join(dstPath, f'{title}.mp4')
                    downloadUrl = self.decode_escaped_string(url)
                    
                    if task_status:
                        task_status.update_status("正在下载音频...")
                        task_status.update_progress(80)
                        task_status.add_log(f"开始下载音频: {title}")
                        task_status.audio_path = dstFile
                    
                    # 使用wget下载，并显示进度
                    temp_file = dstFile + ".tmp"
                    try:
                        wget.download(downloadUrl, temp_file)
                        
                        if os.path.exists(temp_file):
                            os.rename(temp_file, dstFile)
                        else:
                            # 如果wget创建了其他临时文件，尝试找到它们
                            import glob
                            temp_files = glob.glob(dstFile + ".*")
                            for temp_f in temp_files:
                                if os.path.exists(temp_f) and temp_f != dstFile:
                                    os.rename(temp_f, dstFile)
                                    break
                        if dstFile.endswith('.mp4'):
                            mp4File = dstFile
                            mp3File = dstFile[:-4] + '.mp3'
                            r = subprocess.run(
                                ['ffmpeg', '-y', '-i', mp4File, '-vn',
                                 '-acodec', 'libmp3lame', '-ab', '192k',
                                 '-ar', '44100', mp3File],
                                capture_output=True
                            )
                            if r.returncode != 0 or not os.path.exists(mp3File):
                                if task_status:
                                    task_status.add_log(f"mp4→mp3 转换失败: {r.stderr.decode('utf-8', errors='ignore')[-300:]}")
                                return False
                            try:
                                os.remove(mp4File)
                            except OSError:
                                pass
                            dstFile = mp3File
                            if task_status:
                                task_status.audio_path = dstFile
                        
                        if task_status:
                            task_status.update_status("下载完成")
                            task_status.update_progress(100)
                            task_status.add_log(f"音频下载完成: {title}.mp3")
                            
                            task_status.add_log("正在进行 EBU R128 响度归一化 (-16 LUFS)...")
                            volume_adjusted_path = dstFile + "_normalized.mp3"
                            normalize_loudness(dstFile, volume_adjusted_path)
                            os.replace(volume_adjusted_path, dstFile)
                            task_status.add_log(f"音频下载和响度归一化成功: {os.path.basename(dstFile)}")
                        
                        return True
                    except Exception as e:
                        if task_status:
                            task_status.add_log(f"下载失败: {e}")
                        return False
        except Exception as e:
            traceback.print_exc()
            if task_status:
                task_status.add_log(f"下载过程中出错: {e}")
            print(f"scrape error: {e}")
            return False
                


            

def test():
    x = DouyinScraper()
    x.scrape("https://www.douyin.com/user/MS4wLjABAAAAvLKCXxWEf4GwWaZgZ3BnQ73f9Mf4Yt-Ui4_Zulq4_BhaxyUqVMusiYvPMWSyRq9Q?from_tab_name=main&modal_id=7558851191900540198")

if __name__ == '__main__':
    test()
    

