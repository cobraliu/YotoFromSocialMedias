import os, sys, time, json, traceback
import tempfile
import requests
import threading
from datetime import datetime
from typing import Dict, Optional, List, Any
from playwright.sync_api import sync_playwright

from app.utils import sanitize_filename, normalize_loudness
from app.taskmanager import DownloadStatus, taskManager

class QQMusicScraper:
    def __init__(self):
        self.data_dir = self.ensure_data_dir()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/69.0.3497.100 Safari/537.36'
        }
        self.session = requests.session()
        
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
        thread = threading.Thread(target=self._download_media_thread, args=(url, task_status, orgUrl))
        thread.daemon = True
        thread.start()
        
        return task_id

    def _download_media_thread(self, url: str, task_status: DownloadStatus, orgUrl: str = ""):
        """在线程中执行下载任务"""
        try:
            # 创建任务专用目录
            task_dir = os.path.join(self.data_dir, task_status.task_id)
            os.makedirs(task_dir, exist_ok=True)
            
            # 执行下载
            task_status.update_status("正在解析QQ音乐链接...")
            task_status.update_progress(10)
            
            success = self.scrape(task_status.task_id, task_dir, url, task_status, orgUrl)
            
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
        """获取可用格式（QQ音乐暂不支持格式选择）"""
        if task_status:
            task_status.add_log("QQ音乐暂不支持格式选择")
        return "QQ音乐暂不支持格式选择，将下载最佳质量音频"

    def download_song(self, guid, songmid, song_name, cookie_dict, task_status=None, dst_path=None):
        """下载歌曲"""
        if task_status:
            task_status.update_status("正在获取歌曲下载链接...")
            task_status.update_progress(30)
            task_status.add_log(f"开始下载歌曲: {song_name} ({songmid})")
        
        # 参数guid来自cookies的pgv_pvid
        url = 'https://u.y.qq.com/cgi-bin/musicu.fcg?-=getplaysongvkey11136773093082608&g_tk=5381&loginUin=0&hostUin=0&format=json&inCharset=utf8&outCharset=utf-8&notice=0&platform=yqq.json&needNewCode=0&data={"req":{"module":"CDN.SrfCdnDispatchServer","method":"GetCdnDispatch","param":{"guid":"'+guid+'","calltype":0,"userip":""}},"req_0":{"module":"vkey.GetVkeyServer","method":"CgiGetVkey","param":{"guid":"'+guid+'","songmid":["'+songmid+'"],"songtype":[0],"uin":"0","loginflag":1,"platform":"20"}},"comm":{"uin":0,"format":"json","ct":24,"cv":0}}'
        
        if task_status:
            task_status.add_log(f"请求URL: {url}")
            task_status.update_progress(40)
        
        try:
            r = self.session.get(url, headers=self.headers, cookies=cookie_dict)
            rJs = r.json()
            
            if task_status:
                task_status.add_log("获取到歌曲信息")
                task_status.update_progress(60)
            
            purl = rJs['req_0']['data']['midurlinfo'][0]['purl']
            
            # 下载歌曲
            if purl:
                download_url = 'http://isure.stream.qqmusic.qq.com/%s' % (purl)
                
                if task_status:
                    task_status.update_status("正在下载音频...")
                    task_status.update_progress(80)
                    task_status.add_log(f"下载链接: {download_url}")
                
                r = requests.get(download_url, headers=self.headers)
                
                safe_filename = sanitize_filename(song_name)
                
                # 确定保存路径
                if dst_path:
                    filename = os.path.join(dst_path, f'{safe_filename}.m4a')
                else:
                    filename = f'song/{safe_filename}.m4a'
                    os.makedirs('song', exist_ok=True)
                
                with open(filename, 'wb') as f:
                    f.write(r.content)
                
                if task_status:
                    task_status.audio_path = filename
                    task_status.add_log(f"音频下载完成: {filename}")
                    task_status.update_progress(90)

                    task_status.add_log("正在进行 EBU R128 响度归一化 (-16 LUFS)...")
                    volume_adjusted_path = filename + "_normalized.mp3"
                    normalize_loudness(filename, volume_adjusted_path)
                    os.replace(volume_adjusted_path, filename)
                    task_status.add_log(f"音频下载和响度归一化成功: {os.path.basename(filename)}")
                    task_status.update_progress(100)
                
                return True
            else:
                if task_status:
                    task_status.add_log("purl is None - 无法获取下载链接")
                return False
                
        except Exception as e:
            if task_status:
                task_status.add_log(f"下载失败: {str(e)}")
            return False

    def get_cookies(self, task_status=None):
        """获取Cookies"""
        if task_status:
            task_status.add_log("正在获取Cookies...")
            task_status.update_progress(20)
        
        # 某个歌手的歌曲信息，用于获取Cookies，因为不是全部请求地址都有Cookies
        url = 'https://c.y.qq.com/soso/fcgi-bin/client_search_cp?ct=24&qqmusic_ver=1298&new_json=1&remoteplace=txt.yqq.top&searchid=20194704345758042&t=0&aggr=1&cr=1&catZhida=1&lossless=0&flag_qc=0&p=1&n=20&w=%E5%86%AF%E6%8F%90%E8%8E%AB&g_tk=5381&loginUin=0&hostUin=0&format=json&inCharset=utf8&outCharset=utf-8&notice=0&platform=yqq.json&needNewCode=0'
        
        try:
            with sync_playwright() as p:
                # 启动浏览器，headless=True表示无头模式
                browser = p.chromium.launch(headless=True)
                context = browser.new_context()
                page = context.new_page()
                
                if task_status:
                    task_status.add_log("启动浏览器...")
                    task_status.update_progress(30)
                
                # 访问两个URL，QQ网站才能生成Cookies
                page.goto('https://y.qq.com/')
                page.wait_for_timeout(5000)  # 等待5秒
                
                if task_status:
                    task_status.add_log("访问QQ音乐主页...")
                    task_status.update_progress(50)
                
                page.goto(url)
                page.wait_for_timeout(5000)  # 等待5秒
                
                if task_status:
                    task_status.add_log("获取Cookies...")
                    task_status.update_progress(70)
                
                # 获取cookies
                cookies = context.cookies()
                browser.close()
                
                # Cookies格式化
                cookie_dict = {}
                for cookie in cookies:
                    cookie_dict[cookie['name']] = cookie['value']
                
                if task_status:
                    task_status.add_log(f"Cookies获取成功，pgv_pvid: {cookie_dict.get('pgv_pvid', '未找到')}")
                    task_status.update_progress(90)
                
                return cookie_dict
                
        except Exception as e:
            if task_status:
                task_status.add_log(f"获取Cookies失败: {str(e)}")
            return {}

    def get_song_info(self, shared_url, task_status=None, orgUrl: str = ""):
        """从分享链接获取歌曲ID和名称"""
        if task_status:
            task_status.add_log("正在解析分享链接...")
            task_status.update_progress(20)
        
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context()
                page = context.new_page()
                
                if task_status:
                    task_status.add_log("启动浏览器解析链接...")
                    task_status.update_progress(40)
                
                # 访问分享链接
                page.goto(shared_url)
                page.wait_for_timeout(2000)
                
                if task_status:
                    task_status.add_log("获取页面信息...")
                    task_status.update_progress(60)
                
                # 获取歌曲ID
                headHtml = page.evaluate('document.head.innerHTML')
                ogUrl = headHtml.split('<meta property="og:url" content="')[1].split('">')[0]
                songmid = ogUrl.split('/')[-1]
                
                # 获取歌曲名称
                try:
                    # 从输入URL中提取https前面的部分作为文件名
                    song_name = None
                    if 'https://' in orgUrl:
                        # 提取https前面的部分
                        prefix = orgUrl.split('https://')[0].strip()
                        if prefix:
                            song_name = prefix
                            if task_status:
                                task_status.add_log(f"从URL前缀获取歌曲名称: {song_name}")
                    
                    # 如果仍然没有获取到名称，使用songmid
                    if not song_name:
                        song_name = songmid
                        if task_status:
                            task_status.add_log(f"使用songmid作为文件名: {song_name}")
                    
                    # 清理歌曲名称，移除特殊字符
                    song_name = song_name.replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '').replace('<', '').replace('>', '').replace('|', '_')
                    
                    if task_status:
                        task_status.add_log(f"最终歌曲名称: {song_name}")
                        task_status.update_progress(80)
                    
                except Exception as e:
                    if task_status:
                        task_status.add_log(f"获取歌曲名称失败: {str(e)}")
                    song_name = songmid
                
                browser.close()
                return {"songmid": songmid, "song_name": song_name}
                
        except Exception as e:
            if task_status:
                task_status.add_log(f"解析分享链接失败: {str(e)}")
            return None

    def scrape(self, task_id, dst_path, url, task_status=None, orgUrl: str = ""):
        """主要的爬取方法，整合所有步骤"""
        try:
            if task_status:
                task_status.update_status("正在解析QQ音乐链接...")
                task_status.update_progress(10)
                task_status.add_log(f"开始处理QQ音乐链接: {url}")
            
            # 获取Cookies
            cookie_dict = self.get_cookies(task_status)
            if not cookie_dict:
                if task_status:
                    task_status.add_log("无法获取Cookies")
                return False
            
            if task_status:
                task_status.update_status("正在解析歌曲信息...")
                task_status.update_progress(30)
            
            
            # 获取歌曲信息（ID和名称）
            song_info = self.get_song_info(url, task_status, orgUrl)
            if not song_info:
                if task_status:
                    task_status.add_log("无法获取歌曲信息")
                return False
            
            songmid = song_info['songmid']
            song_name = song_info['song_name']
            
            if task_status:
                task_status.update_status("正在下载歌曲...")
                task_status.update_progress(50)
                task_status.add_log(f"歌曲名称: {song_name}")
            
            # 下载歌曲
            guid = cookie_dict.get('pgv_pvid', '')
            success = self.download_song(guid, songmid, song_name, cookie_dict, task_status, dst_path)
            
            if success:
                if task_status:
                    task_status.add_log("QQ音乐下载完成")
                return True
            else:
                if task_status:
                    task_status.add_log("QQ音乐下载失败")
                return False
                
        except Exception as e:
            if task_status:
                task_status.add_log(f"处理过程中出错: {str(e)}")
            return False

def test():
    """测试函数"""
    scraper = QQMusicScraper()
    shared_url = 'https://c6.y.qq.com/base/fcgi-bin/u?__=Gx85x26URoYm'
    task_id = scraper.download_media(shared_url, "test_task")
    print(f"任务ID: {task_id}")

if __name__ == '__main__':
    test()
