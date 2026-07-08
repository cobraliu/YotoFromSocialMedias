#!/usr/bin/env python3
"""
测试集成功能：验证URL检测和下载器选择
"""

import sys
import os

# 添加当前目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_url_detection():
    """测试URL检测功能"""
    try:
        from app.routers import is_douyin_url, get_downloader
        
        # 测试抖音URL检测
        douyin_urls = [
            "https://www.douyin.com/video/123456789",
            "https://v.douyin.com/abc123/",
            "https://www.douyin.com/user/123?modal_id=456"
        ]
        
        other_urls = [
            "https://www.youtube.com/watch?v=123",
            "https://www.bilibili.com/video/123",
            "https://example.com"
        ]
        
        print("测试抖音URL检测:")
        for url in douyin_urls:
            is_douyin = is_douyin_url(url)
            print(f"  {url} -> {'抖音' if is_douyin else '其他'}")
            assert is_douyin, f"应该识别为抖音URL: {url}"
        
        print("\n测试其他URL检测:")
        for url in other_urls:
            is_douyin = is_douyin_url(url)
            print(f"  {url} -> {'抖音' if is_douyin else '其他'}")
            assert not is_douyin, f"不应该识别为抖音URL: {url}"
        
        print("\n测试下载器选择:")
        for url in douyin_urls:
            downloader = get_downloader(url)
            print(f"  {url} -> {type(downloader).__name__}")
            assert "DouyinScraper" in str(type(downloader)), f"应该选择DouyinScraper: {url}"
        
        for url in other_urls:
            downloader = get_downloader(url)
            print(f"  {url} -> {type(downloader).__name__}")
            assert "YTDownloader" in str(type(downloader)), f"应该选择YTDownloader: {url}"
        
        print("\n✅ URL检测和下载器选择测试通过!")
        return True
        
    except Exception as e:
        print(f"❌ 测试失败: {str(e)}")
        return False

def test_downloader_initialization():
    """测试下载器初始化"""
    try:
        from app.douyin import DouyinScraper
        from app.ytdownloader import YTDownloader
        
        # 测试DouyinScraper初始化
        douyin_scraper = DouyinScraper()
        print(f"✅ DouyinScraper初始化成功: {type(douyin_scraper)}")
        
        # 测试YTDownloader初始化
        yt_downloader = YTDownloader()
        print(f"✅ YTDownloader初始化成功: {type(yt_downloader)}")
        
        # 测试基本方法存在
        assert hasattr(douyin_scraper, 'download_media'), "DouyinScraper应该有download_media方法"
        assert hasattr(douyin_scraper, 'get_task_status'), "DouyinScraper应该有get_task_status方法"
        assert hasattr(douyin_scraper, 'get_file_path'), "DouyinScraper应该有get_file_path方法"
        assert hasattr(douyin_scraper, 'get_history_tasks'), "DouyinScraper应该有get_history_tasks方法"
        assert hasattr(douyin_scraper, 'get_available_formats'), "DouyinScraper应该有get_available_formats方法"
        
        print("✅ 下载器方法检查通过!")
        return True
        
    except Exception as e:
        print(f"❌ 下载器初始化测试失败: {str(e)}")
        return False

def main():
    """运行所有测试"""
    print("开始集成测试...\n")
    
    tests = [
        ("URL检测和下载器选择", test_url_detection),
        ("下载器初始化", test_downloader_initialization),
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        print(f"运行测试: {test_name}")
        if test_func():
            passed += 1
        print()
    
    print(f"测试结果: {passed}/{total} 通过")
    
    if passed == total:
        print("🎉 所有测试通过! 集成功能正常工作。")
        return True
    else:
        print("❌ 部分测试失败，请检查代码。")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
