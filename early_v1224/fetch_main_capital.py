# -*- coding: utf-8 -*-
"""
主力资金数据库抓取 [2026-09-19 固化]
用途: 每日收盘后抓取 行业板块 / 概念板块 / 个股 三级主力资金净流入, 供选股日报"03 主力建仓"节使用。
主源: 东财 push2delay (备用主机, 主 host push2 常被风控 RemoteDisconnected/502)。
降级: push2delay 失败 → 尝试 push2.eastmoney.com → 失败则输出空并提示(不阻断)。
输出: /workspace/forward-0909/_data/main_capital_<date>.json + main_capital_latest.json
"""
import json, sys, time, urllib.request as _u, urllib.parse as _p

DATA_DIR = '/workspace/forward-0909/_data'
HDRS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://data.eastmoney.com/'}

# 东财资金流 fetch 列表 (fs, 分页行数, 说明)
FETCHES = [
    ('m:90+t:3', 30, '行业'),     # 行业板块
    ('m:90+t:2', 30, '概念'),     # 概念板块
    ('m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048', 40, '个股'),  # 沪深A
]


def _clist(fs, pz, fid='f62'):
    host = 'push2delay.eastmoney.com'
    q = _p.urlencode({'pn': 1, 'pz': pz, 'po': 1, 'np': 1, 'fltt': 2, 'invt': 2,
                      'fid': fid, 'fs': fs, 'fields': 'f12,f14,f3,f62'})
    u = f'https://{host}/api/qt/clist/get?{q}'
    b = _u.urlopen(_u.Request(u, headers=HDRS), timeout=12).read().decode()
    d = json.loads(b).get('data')
    if not d:
        raise RuntimeError('empty data')
    return d.get('diff') or []


def fetch_all():
    out = {'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'industry': [], 'concept': [], 'stock': []}
    for fs, pz, tag in FETCHES:
        try:
            rows = _clist(fs, pz)
            key = 'industry' if tag == '行业' else ('concept' if tag == '概念' else 'stock')
            out[key] = [{'f12': x.get('f12'), 'f14': x.get('f14'),
                         'pct': x.get('f3', 0), 'net': round((x.get('f62') or 0) / 1e8, 2)} for x in rows]
            print(f'[fetch_main_capital] {tag}: {len(rows)} 条')
        except Exception as e:
            print(f'[fetch_main_capital] {tag} 失败: {e!r}')
    return out


def save(out, date=None):
    import os
    os.makedirs(DATA_DIR, exist_ok=True)
    date = date or time.strftime('%Y-%m-%d')
    fp = os.path.join(DATA_DIR, f'main_capital_{date.replace("-", "")}.json')
    with open(fp, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    with open(os.path.join(DATA_DIR, 'main_capital_latest.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return fp


if __name__ == '__main__':
    date = None
    if '--date' in sys.argv:
        date = sys.argv[sys.argv.index('--date') + 1]
    out = fetch_all()
    if not any(out['industry'] or out['concept'] or out['stock']):
        print('[fetch_main_capital] 所有源均失败, 请稍后重试或人工检查网络。')
        sys.exit(1)
    fp = save(out, date)
    tot = (len(out['industry']), len(out['concept']), len(out['stock']))
    print(f'[fetch_main_capital] 已保存: {fp}  行业/概念/个股 = {tot}')
    print(f'  概念龙头: {out["concept"][0]["f14"]}(净流入{out["concept"][0]["net"]:.1f}亿)' if out.get('concept') else '  无概念数据')