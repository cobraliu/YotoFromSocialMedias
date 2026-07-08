import os
import json
import uvicorn
import subprocess
from fastapi import FastAPI, HTTPException, BackgroundTasks, Response, Request, Form, Depends
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Optional, List
from datetime import datetime
from pydantic import BaseModel

from app.ytdownloader import YTDownloader
from app.douyin import DouyinScraper
from app.qqmusic import QQMusicScraper
from app.bilibili import BilibiliScraper
from app.schemas import DownloadRequest, TaskResponse, TaskStatus, ErrorResponse
from app.utils import generate_task_id, get_file_size, get_content_type
from app.taskmanager import DownloadStatus, taskManager

import asyncio
import app.accounts as accounts
import app.yoto_pipeline as yoto_pipeline
import app.yoto_icons as yoto_icons
import app.yoto_client as yoto_login          # PKCE login (authorize_url/exchange) lives here
import app.yoto_client as yconfig             # config helpers (load_client_id/load_token/env_path/…)
from app.yoto_client import authed_session
from app.yoto_client import list_playlists as yoto_list_playlists
from app.yoto_client import create_playlist as yoto_create_playlist
from app.yoto_client import fetch_card as yoto_fetch_card
from app.yoto_client import extract_playlist_tracks as yoto_extract_tracks
from app.yoto_jobs import jobManager as yotoJobManager

# 创建FastAPI应用
app = FastAPI(
    title="视频转音频下载器API",
    description="一个基于FastAPI的视频下载和音频提取服务",
    version="1.0.0"
)

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有来源，生产环境中应该限制
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 创建下载器实例
yt_downloader = YTDownloader()
douyin_scraper = DouyinScraper()
qqmusic_scraper = QQMusicScraper()
bilibili_scraper = BilibiliScraper()


# 创建模板引擎（相对仓库根定位，运行目录无关）。本文件位于 <repo>/app/，
# 模板与静态资源在仓库根目录下。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(_REPO_ROOT, "templates"))

# 挂载静态文件
try:
    os.makedirs("static", exist_ok=True)
    app.mount("/static", StaticFiles(directory="static"), name="static")
except Exception as e:
    print(f"无法挂载静态文件目录: {str(e)}")

# 创建模板目录
try:
    os.makedirs("templates", exist_ok=True)
except Exception as e:
    print(f"无法创建模板目录: {str(e)}")

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """返回主页"""
    return templates.TemplateResponse("scrape.html", {"request": request})


# ─── 用户认证 ─────────────────────────────────────────────────────────
COOKIE = "v2m_sid"


def require_user(request: Request) -> str:
    """Resolve the current user from the session cookie, or 401."""
    uid = accounts.session_user(request.cookies.get(COOKIE, ""))
    if not uid:
        raise HTTPException(status_code=401, detail="未登录")
    return uid


def require_admin(request: Request) -> str:
    """Resolve the current user and require admin, or 401/403."""
    uid = require_user(request)
    if not accounts.is_admin(uid):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return uid


class AuthRequest(BaseModel):
    username: str
    password: str


class NewUserRequest(BaseModel):
    username: str
    password: str
    is_admin: bool = False


class PasswordRequest(BaseModel):
    password: str


def _set_session_cookie(resp: Response, uid: str) -> None:
    tok = accounts.new_session(uid)
    resp.set_cookie(COOKIE, tok, httponly=True, samesite="lax",
                    max_age=30 * 24 * 3600, path="/")


@app.post("/api/auth/login")
async def auth_login(req: AuthRequest):
    uid = accounts.authenticate(req.username, req.password)
    if not uid:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    resp = JSONResponse({"uid": uid, "username": req.username.strip()})
    _set_session_cookie(resp, uid)
    return resp


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    accounts.end_session(request.cookies.get(COOKIE, ""))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


@app.get("/api/auth/me")
async def auth_me(request: Request):
    uid = require_user(request)
    return {**accounts.get_user(uid), "is_admin": accounts.is_admin(uid)}


# ─── 管理员：账号管理 ─────────────────────────────────────────────────
@app.get("/api/admin/users")
async def admin_list_users(admin: str = Depends(require_admin)):
    return {"users": accounts.list_users()}


@app.post("/api/admin/users")
async def admin_create_user(req: NewUserRequest, admin: str = Depends(require_admin)):
    try:
        uid = accounts.create_user(req.username, req.password, is_admin=req.is_admin)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"uid": uid, "username": req.username.strip(), "is_admin": req.is_admin}


@app.delete("/api/admin/users/{uid}")
async def admin_delete_user(uid: str, admin: str = Depends(require_admin)):
    try:
        accounts.delete_user(uid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@app.post("/api/admin/users/{uid}/password")
async def admin_set_password(uid: str, req: PasswordRequest,
                             admin: str = Depends(require_admin)):
    try:
        accounts.set_password(uid, req.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/yoto/playlists", response_class=HTMLResponse)
async def yoto_playlists_page(request: Request):
    """Playlist display page. Login + Yoto-auth are enforced client-side
    (redirect to /login if not logged in; a hint if Yoto isn't linked); the
    data APIs it calls are independently login- and auth-gated."""
    return templates.TemplateResponse("playlists.html", {"request": request})


def urlSplit(url):
    import re
    url = url.strip()
    match = re.search(r'https?://\S+', url)
    if match:
        return match.group(0)
    return url

def is_douyin_url(url):
    """检测是否为抖音URL"""
    douyin_domains = ['douyin.com', 'v.douyin.com']
    return any(domain in url for domain in douyin_domains)

def is_qqmusic_url(url):
    """检测是否为QQ音乐URL"""
    qqmusic_domains = ['y.qq.com', 'c.y.qq.com', 'c6.y.qq.com']
    return any(domain in url for domain in qqmusic_domains)

def is_bilibili_url(url):
    """检测是否为 B 站 URL"""
    bilibili_domains = ['bilibili.com', 'b23.tv']
    return any(domain in url for domain in bilibili_domains)

def get_downloader(url):
    """根据URL选择下载器"""
    if is_douyin_url(url):
        return douyin_scraper
    elif is_qqmusic_url(url):
        return qqmusic_scraper
    elif is_bilibili_url(url):
        return bilibili_scraper
    else:
        return yt_downloader

@app.post("/api/download", response_model=TaskResponse, responses={400: {"model": ErrorResponse}})
async def create_download_task(request: DownloadRequest):
    """创建下载任务"""
    orgUrl = request.url
    if not orgUrl:
        raise HTTPException(status_code=400, detail="URL不能为空")
    
    # 生成任务ID
    task_id = generate_task_id()
    
    # 启动下载任务
    url = urlSplit(orgUrl)
    downloader = get_downloader(url)
    downloader.download_media(url, task_id, orgUrl)
    
    return TaskResponse(task_id=task_id, message="任务已创建")


@app.get("/api/status/{task_id}", response_model=TaskStatus, responses={404: {"model": ErrorResponse}})
async def get_task_status(task_id: str):
    """获取任务状态"""
    # 先尝试从YT下载器获取状态
    status = taskManager.get_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    return TaskStatus(**status)


@app.get("/api/download/{task_id}/{file_type}", responses={404: {"model": ErrorResponse}})
async def download_file(task_id: str, file_type: str):
    """下载文件"""
    file_type = file_type.split('.')[0]  # strip e.g. "audio.mp3" → "audio"
    if file_type not in ["video", "audio"]:
        raise HTTPException(status_code=400, detail="无效的文件类型")
    
    # 先尝试从YT下载器获取文件路径
    file_path = taskManager.get_file_path(task_id, file_type)
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="文件不存在")
    
    # 获取文件名
    filename = os.path.basename(file_path)
    
    # 获取内容类型
    content_type = get_content_type(file_path)
    
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type=content_type
    )


@app.get("/api/play/{task_id}/{file_type}", responses={404: {"model": ErrorResponse}})
async def play_file(task_id: str, file_type: str):
    """播放文件"""
    if file_type not in ["video", "audio"]:
        raise HTTPException(status_code=400, detail="无效的文件类型")
    
    # 先尝试从YT下载器获取文件路径
    file_path = taskManager.get_file_path(task_id, file_type)
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="文件不存在")
    
    # 获取内容类型
    content_type = get_content_type(file_path)
    
    # 使用流式响应，支持播放器seek功能
    return FileResponse(
        path=file_path,
        media_type=content_type,
        headers={"Accept-Ranges": "bytes"}
    )


@app.get("/api/formats", responses={400: {"model": ErrorResponse}})
async def get_available_formats(url: str):
    """获取可用格式"""
    if not url:
        raise HTTPException(status_code=400, detail="URL不能为空")
    
    # 根据URL选择下载器
    downloader = get_downloader(url)
    
    # 创建临时任务状态
    task_id = generate_task_id()
    task_status = taskManager.tasks[task_id] = DownloadStatus()
    task_status.task_id = task_id
    
    formats = downloader.get_available_formats(url, task_status)
    if not formats:
        raise HTTPException(status_code=400, detail="无法获取格式信息")
    
    # 清理临时任务
    del taskManager.tasks[task_id]
    
    return {"formats": formats}

@app.get("/api/history/data")
async def get_history_data(page: int = 1, page_size: int = 20, q: str = ""):
    """获取历史任务数据，支持分页和搜索"""
    all_history = taskManager.get_history_tasks()
    q = (q or "").strip().lower()
    if q:
        def _hit(t):
            return any(
                q in (t.get(k) or "").lower()
                for k in ("url", "audio_filename", "video_filename", "task_id")
            )
        all_history = [t for t in all_history if _hit(t)]
    all_history.sort(key=lambda x: x.get('start_time', ''), reverse=True)
    total = len(all_history)
    page = max(1, page)
    start = (page - 1) * page_size
    return {
        "items": all_history[start: start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "q": q,
    }


@app.on_event("startup")
async def startup_event():
    """应用启动时执行"""
    print("视频转音频下载器API已启动")


# 音频截取请求模型
class AudioTrimRequest(BaseModel):
    task_id: str
    start_time: float
    end_time: float
    output_filename: str

@app.post("/api/trim-audio")
async def trim_audio(request: AudioTrimRequest):
    """截取音频文件"""
    task_id = request.task_id
    start_time = request.start_time
    end_time = request.end_time
    output_filename = request.output_filename
    
    # 获取源音频文件路径
    audio_path = taskManager.get_file_path(task_id, "audio")
    if not audio_path or not os.path.exists(audio_path):
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": "源音频文件不存在"}
        )
    
    # 创建任务特定的trims目录
    task_trim_dir = os.path.join("data", task_id, "trims")
    os.makedirs(task_trim_dir, exist_ok=True)
    
    # 生成截取ID
    trim_id = generate_task_id()
    
    # 确保文件名有.mp3扩展名
    if not output_filename.lower().endswith('.mp3'):
        output_filename += '.mp3'
    
    # 截取后的文件路径
    output_path = os.path.join(task_trim_dir, f"{trim_id}_{output_filename}")
    
    try:
        # 使用ffmpeg截取音频
        cmd = [
            "ffmpeg",
            "-i", audio_path,
            "-ss", str(start_time),
            "-to", str(end_time),
            "-c:a", "libmp3lame",
            "-q:a", "2",
            output_path
        ]
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            print(f"FFmpeg错误: {stderr.decode('utf-8', errors='ignore')}")
            return JSONResponse(
                content={"success": False, "error": "音频截取失败"}
            )
        
        # 保存截取信息到任务状态中
        trim_info = {
            "trim_id": trim_id,
            "task_id": task_id,
            "start_time": start_time,
            "end_time": end_time,
            "output_filename": output_filename,
            "output_path": output_path,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        # 将trim信息添加到任务状态中
        if task_id in taskManager.tasks:
            if not hasattr(taskManager.tasks[task_id], 'trims'):
                taskManager.tasks[task_id].trims = []
            taskManager.tasks[task_id].trims.append(trim_info)
        
        # 同时保存截取信息到文件(为了持久化)
        info_path = os.path.join(task_trim_dir, f"{trim_id}_info.json")
        with open(info_path, 'w', encoding='utf-8') as f:
            json.dump(trim_info, f, ensure_ascii=False, indent=2)
        
        return JSONResponse(
            content={"success": True, "trim_id": trim_id, "filename": output_filename}
        )
    except Exception as e:
        print(f"截取音频错误: {str(e)}")
        return JSONResponse(
            content={"success": False, "error": str(e)}
        )

def _find_trim_info(trim_id: str) -> Optional[dict]:
    """Locate a trim's persisted info by scanning task dirs for
    <trim_id>_info.json. Returns the info dict, or None if not found."""
    trim_id = trim_id.removesuffix('.mp3')
    data_dir = "data"
    if not os.path.exists(data_dir):
        return None
    for task_id in os.listdir(data_dir):
        info_path = os.path.join(data_dir, task_id, "trims", f"{trim_id}_info.json")
        if os.path.exists(info_path):
            try:
                with open(info_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return None
    return None


@app.get("/api/download-trim/{trim_id}")
async def download_trimmed_audio(trim_id: str):
    """下载截取后的音频文件"""
    trim_info = _find_trim_info(trim_id)
    if trim_info:
        output_path = trim_info.get("output_path")
        output_filename = trim_info.get("output_filename")
        if output_path and os.path.exists(output_path):
            return FileResponse(
                path=output_path,
                filename=output_filename,
                media_type="audio/mpeg",
            )
    raise HTTPException(status_code=404, detail="截取文件不存在")

@app.get("/api/task-details/{task_id}", response_model=Dict)
async def get_task_details(task_id: str):
    """获取任务详情包括截取列表"""
    # 获取任务状态
    status = taskManager.get_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    # 获取任务的截取列表
    trims = []
    if task_id in taskManager.tasks and hasattr(taskManager.tasks[task_id], 'trims'):
        trims = taskManager.tasks[task_id].trims
    
    # 或者从文件系统中读取所有截取信息
    task_trim_dir = os.path.join("data", task_id, "trims")
    if os.path.exists(task_trim_dir):
        for file in os.listdir(task_trim_dir):
            if file.endswith("_info.json"):
                info_path = os.path.join(task_trim_dir, file)
                try:
                    with open(info_path, 'r', encoding='utf-8') as f:
                        trim_info = json.load(f)
                        # 避免重复添加已经在内存中的项
                        if not any(t['trim_id'] == trim_info['trim_id'] for t in trims):
                            trims.append(trim_info)
                except Exception as e:
                    print(f"读取截取信息错误: {e}")
    return {
        "task_status": status,
        "trims": trims
    }

@app.get("/task/{task_id}", response_class=HTMLResponse)
async def task_details_page(request: Request, task_id: str):
    """任务详情页面"""
    # Check if task exists
    status = taskManager.get_task_status(task_id)
    
    if not status:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    return templates.TemplateResponse("task_details.html", {
        "request": request,
        "task_id": task_id
    })

@app.get("/history/{task_id}", response_class=HTMLResponse)
async def history_task_page(request: Request, task_id: str):
    """历史任务页面（重定向到任务详情页面）"""
    # 重定向到任务详情页面
    return RedirectResponse(url=f"/task/{task_id}")

@app.delete("/api/delete-task/{task_id}")
async def delete_task(task_id: str):
    """删除任务及其相关文件"""
    try:
        # 检查任务是否存在
        task_dir = os.path.join("data", task_id)
        if not os.path.exists(task_dir):
            return JSONResponse(
                status_code=404,
                content={"success": False, "error": "任务不存在"}
            )
        
        # 从内存中移除任务（如果存在）
        if task_id in taskManager.tasks:
            del taskManager.tasks[task_id]
        
        # 删除任务目录及其所有文件
        import shutil
        try:
            shutil.rmtree(task_dir)
            return JSONResponse(
                content={"success": True, "message": f"任务 {task_id} 已成功删除"}
            )
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": f"删除文件失败: {str(e)}"}
            )
            
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"删除任务失败: {str(e)}"}
        )

class YotoUploadRequest(BaseModel):
    task_id: str = ""
    filename: str = ""
    playlist_id: str
    icon_media_id: str | None = None
    trim_id: str | None = None   # upload a trimmed segment instead of the full audio


# ─── Yoto 账号绑定 ────────────────────────────────────────────────────
class BindRequest(BaseModel):
    client_id: str


class ExchangeRequest(BaseModel):
    code: str


@app.get("/api/yoto/status")
async def yoto_status(uid: str = Depends(require_user)):
    try:
        bound = bool(yconfig.load_client_id(uid))
    except Exception:
        bound = False
    tok = yconfig.load_token(uid)
    authed = bool(tok and tok.refresh_token)
    scopes = (tok.scope or "").split() if authed and tok.scope else []
    return {"bound": bound, "authed": authed, "scopes": scopes}


@app.post("/api/yoto/bind")
async def yoto_bind(req: BindRequest, uid: str = Depends(require_user)):
    cid = (req.client_id or "").strip()
    if not cid:
        raise HTTPException(status_code=400, detail="client_id 不能为空")
    p = yconfig.env_path(uid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"client_id: {cid}\n")
    return {"ok": True}


@app.get("/api/yoto/auth/url")
async def yoto_auth_url(uid: str = Depends(require_user)):
    try:
        return {"url": yoto_login.authorize_url(uid)}
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})


@app.post("/api/yoto/auth/exchange")
async def yoto_auth_exchange(req: ExchangeRequest, uid: str = Depends(require_user)):
    try:
        tok = await yoto_login.exchange(uid, req.code)
        return {"scopes": (tok.scope or "").split()}
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})


@app.get("/api/yoto/playlists")
async def yoto_playlists(uid: str = Depends(require_user)):
    """List the user's Yoto playlists for the upload dropdown."""
    try:
        async with authed_session(uid) as (session, get_token):
            items = await yoto_list_playlists(session, get_token)
        return {"playlists": items}
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


class PlaylistCreateRequest(BaseModel):
    title: str


@app.post("/api/yoto/playlists")
async def yoto_create_playlist_route(req: PlaylistCreateRequest,
                                     uid: str = Depends(require_user)):
    """Create a new empty playlist on the user's Yoto account."""
    if not (req.title or "").strip():
        raise HTTPException(status_code=400, detail="playlist 名称不能为空")
    try:
        async with authed_session(uid) as (session, get_token):
            item = await yoto_create_playlist(session, get_token, req.title)
        return item
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


@app.get("/api/yoto/playlists/{card_id}")
async def yoto_playlist_detail(card_id: str, uid: str = Depends(require_user)):
    """Return one playlist's chapters/tracks for the display page."""
    try:
        async with authed_session(uid) as (session, get_token):
            detail = await yoto_fetch_card(session, get_token, card_id)
        return yoto_extract_tracks(detail)
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


@app.post("/api/yoto/upload")
async def yoto_upload(req: YotoUploadRequest, uid: str = Depends(require_user)):
    """Start an optimize+upload+append job; return its job_id. Uploads either
    the task's full audio, or a trimmed segment when trim_id is given."""
    if req.trim_id:
        info = _find_trim_info(req.trim_id)
        audio_path = (info or {}).get("output_path")
        if not audio_path or not os.path.exists(audio_path):
            raise HTTPException(status_code=404, detail="截取文件不存在")
        # Default name for a segment is "<原名>_<片段名>", not just the segment.
        seg_stem = os.path.splitext(info.get("output_filename", "") or "")[0]
        orig_path = taskManager.get_file_path(info.get("task_id", "") or "", "audio")
        orig_stem = os.path.splitext(os.path.basename(orig_path))[0] if orig_path else ""
        default_name = f"{orig_stem}_{seg_stem}" if orig_stem else seg_stem
    else:
        audio_path = taskManager.get_file_path(req.task_id, "audio")
        if not audio_path or not os.path.exists(audio_path):
            raise HTTPException(status_code=404, detail="音频文件不存在")
        default_name = os.path.splitext(os.path.basename(audio_path))[0]
    filename = (req.filename or "").strip() or default_name
    if not req.playlist_id:
        raise HTTPException(status_code=400, detail="未选择 playlist")
    job = yotoJobManager.create()
    asyncio.create_task(
        yoto_pipeline.run_upload(audio_path, filename, req.playlist_id, job,
                                 icon_media_id=req.icon_media_id, uid=uid)
    )
    return {"job_id": job.job_id}


@app.get("/api/yoto/upload-status/{job_id}")
async def yoto_upload_status(job_id: str, uid: str = Depends(require_user)):
    status = yotoJobManager.get(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return status


class IconImportRequest(BaseModel):
    icon_id: str


@app.get("/api/yoto/icons")
async def yoto_icons_local(q: str = "", source: str = "all",
                           uid: str = Depends(require_user)):
    """Search/browse the local icon catalog (no network)."""
    out = []
    if source in ("all", "yoto"):
        out += yoto_icons.search_yoto(yoto_icons.load_yoto(uid), q)
    if source in ("all", "me"):
        out += yoto_icons.list_me(yoto_icons.load_me(uid))
    return {"icons": out}


@app.get("/api/yoto/icons/search-external")
async def yoto_icons_external(q: str, uid: str = Depends(require_user)):
    """Search yotoicons.com for icons to import."""
    try:
        async with authed_session(uid) as (session, get_token):
            items = await yoto_icons.search_yotoicons(session, q)
        return {"icons": items}
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


@app.post("/api/yoto/icons/import")
async def yoto_icons_import(req: IconImportRequest, uid: str = Depends(require_user)):
    """Download a yotoicons icon, upload it to Yoto, record it locally."""
    try:
        async with authed_session(uid) as (session, get_token):
            entry = await yoto_icons.import_yotoicon(session, get_token,
                                                     req.icon_id, uid)
        return entry
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


@app.get("/api/yoto/icons/thumb")
async def yoto_icons_thumb(media_id: str = "", url: str = "",
                           uid: str = Depends(require_user)):
    """Auth'd proxy that streams a Yoto/me icon image for the picker grid,
    caching the PNG under data/yoto_icons/ so repeats are served from disk."""
    try:
        async with authed_session(uid) as (session, get_token):
            data, ctype = await yoto_icons.cached_icon_bytes(
                session, get_token, media_id, url)
        return Response(content=data, media_type=ctype)
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


@app.post("/api/yoto/icons/refresh")
async def yoto_icons_refresh(uid: str = Depends(require_user)):
    """Re-sync both local icon catalogs from the Yoto API."""
    try:
        async with authed_session(uid) as (session, get_token):
            counts = await yoto_icons.refresh_from_api(session, get_token, uid)
        return counts
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时执行"""
    print("正在清理临时文件...")
    # 可以在这里添加清理临时文件的代码
