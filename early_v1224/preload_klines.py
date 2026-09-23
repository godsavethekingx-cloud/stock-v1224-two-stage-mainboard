#!/usr/bin/env python3
"""
盘前K线预加载脚本 — 8:30自动运行
将全量主板K线数据缓存到本地，供9:15极速扫描使用
"""
import json, time, os, sys
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# 导入扫描系统的股票列表和K线函数
sys.path.insert(0, '/workspace/stock-scan-package')
from scan_stocks_v9 import get_mainboard_stocks, get_sina_kline

CACHE_DIR = '/workspace/stock-scan-package'
CACHE_FILE = os.path.join(CACHE_DIR, 'kline_cache.json')

def preload_all_klines():
    t0 = time.time()
    print(f"[预加载] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 开始预加载K线数据")
    
    stocks = get_mainboard_stocks()
    print(f"[预加载] 主板非ST: {len(stocks)}只")
    
    codes = [s['code'] for s in stocks]
    results = {}
    total = len(codes)
    done = 0
    failed = 0
    
    def fetch_one(code):
        return code, get_sina_kline(code, 150)
    
    with ThreadPoolExecutor(max_workers=25) as executor:
        futures = {executor.submit(fetch_one, code): code for code in codes}
        for future in as_completed(futures):
            done += 1
            if done % 500 == 0:
                print(f"  [预加载] 进度: {done}/{total} 成功{len(results)}只 ({time.time()-t0:.0f}秒)")
            try:
                code, data = future.result(timeout=20)
                if data and len(data.get('closes', [])) >= 5:
                    results[code] = data
                else:
                    failed += 1
            except Exception:
                failed += 1
    
    elapsed = time.time() - t0
    print(f"  [预加载] 完成: {len(results)}/{total}只, 失败{failed}只, 耗时{elapsed:.0f}秒")
    
    # 保存缓存（带时间戳）
    cache_data = {
        'cache_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total': len(results),
        'klines': results,
    }
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(CACHE_FILE, 'w') as f:
        json.dump(cache_data, f, ensure_ascii=False)
    
    file_size = os.path.getsize(CACHE_FILE) / 1024 / 1024
    print(f"  [预加载] 缓存已保存: {CACHE_FILE} ({file_size:.1f}MB)")
    print(f"  [预加载] 完成! 9:15极速扫描将直接使用此缓存")
    
    return len(results)

if __name__ == '__main__':
    preload_all_klines()
