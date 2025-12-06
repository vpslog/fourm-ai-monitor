import json
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from send import NotificationSender
import os
from pymongo import MongoClient
import cfscrape
import shutil
from dotenv import load_dotenv
from msgparse import thread_message
from filter import Filter

from bs4 import MarkupResemblesLocatorWarning
import warnings

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

# Load variables from data/.env
load_dotenv('data/.env')

scraper = cfscrape.create_scraper()

class NSMonitor:
    def __init__(self, config_path='data/config.json'):
        self.config_path = config_path
        self.mongo_host = os.getenv("MONGO_HOST", 'mongodb://localhost:27017/')
        self.load_config()

        self.mongo_client = MongoClient(self.mongo_host)
        self.db = self.mongo_client['ns_monitor']
        self.threads = self.db['threads']

        self.threads.create_index('link', unique=True)

    # 简化版当前时间调用函数
    def current_time(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # 简化配置加载
    def load_config(self):
        # 如果配置文件不存在，复制示例文件
        if not os.path.exists(self.config_path):
            shutil.copy('example.json', self.config_path)
        with open(self.config_path, 'r') as f:
            self.config = json.load(f)['config']
        self.notifier = NotificationSender(self.config)
        self.filter = Filter(self.config)
        print("配置文件加载成功")


    # -------- RSS NodeSeek -----------
    def check_nodeseek(self):
        url = "https://rss.nodeseek.com/"
        print(f"[{self.current_time()}] 检查 NodeSeek RSS...")
        res = scraper.get(url)
        if res.status_code != 200:
            print("获取 NodeSeek 失败")
            return
        soup = BeautifulSoup(res.text, 'xml')
        for item in soup.find_all('item'):
            self.convert_ns_rss(item)

    # 将 NS RSS item 转成 thread_data
    def convert_ns_rss(self, item):
        title = item.find('title').text
        link = item.find('link').text
        desc = BeautifulSoup(item.find('description').text, 'lxml').text if item.find('description') else ""
        creator = item.find('dc:creator').text
        pub_date_str = item.find('pubDate').text
        try:
            pub_date = datetime.strptime(pub_date_str, "%a, %d %b %Y %H:%M:%S %Z")
        except ValueError:
            # 如果解析失败，使用当前时间
            pub_date = datetime.now(timezone.utc)

        thread_data = {
            'domain': 'nodeseek',
            'category': 'rss',
            'title': title,
            'link': link,
            'description': desc,
            'creator': creator,
            'pub_date': pub_date,
            'created_at': datetime.now(timezone.utc)
        }
        self.handle_ns_thread(thread_data)

    # -------- NS 线程存储 + 通知 --------
    def handle_ns_thread(self, thread):
        exists = self.threads.find_one({'link': thread['link']})
        if exists:
            return

        self.threads.insert_one(thread)
        # 发布时间 24h 内才推送
        if (datetime.now(timezone.utc) - thread['pub_date'].replace(tzinfo=timezone.utc)).total_seconds() <= 86400:
            # NS 关键词过滤
            if self.config.get('use_keywords_filter', False) and not self.filter.keywords_filter(thread['title']+' description: '+thread['description'], self.config.get('ns_keywords', '')):
                return
            if self.config.get('use_ai_filter', False):
                ai_description = self.filter.ai_filter(thread['title']+' description: '+thread['description'], self.config.get('ns_prompt', ''))
                if 'false' in ai_description.lower():
                    return
            else:
                ai_description = ""
            msg = thread_message(thread, ai_description)
            self.notifier.send_message(msg)


    # -------- 主循环 --------
    def start_monitoring(self):
        print("开始监控 NodeSeek...")
        freq = self.config.get('frequency', 600)

        while True:
            self.check_nodeseek()
            print(f"[{self.current_time()}] 遍历结束，休眠 {freq} 秒...")
            time.sleep(freq)

    # 外部重载配置方法
    def reload(self):
        print("重新加载配置...")
        self.load_config()

if __name__ == "__main__":
    monitor = NSMonitor()
    monitor.start_monitoring()