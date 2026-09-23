#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科技板块修复优选：针对 AI算力/CPO/光通信/半导体/存储 板块成分股，
   用与主引擎同源的技术逻辑筛选"修复候选"，出独立于防守池的科技推荐。"""
import sys, os, json, time
from concurrent.futures import ThreadPoolExecutor, as_completed

SYS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SYS)
sys.path.insert(0, os.path.join(SYS, '..'))

from scan_v1224 import get_kl, get_realtime_quote, load_sector_mapping, match_sector

# 目标科技板块 (映射表实际名称)
TECH_SECTORS = [
    'AI应用', 'AIGC概念', 'CPO概念', '光通信模块', '光缆概念', '存储芯片',
    '国产芯片', '半导体', '半导体材料', '半导体设备', '先进封装', '数字芯片设计',
    '模拟芯片设计', 'PCB', '云计算', '信创', 'AI芯片', 'HBM概念', 'GPU概念',
]

TECH_ALIAS = {
    'AI算力': ['AI应用', 'AIGC概念', '云计算', 'AI智能体'],
    'CPO光通信': ['CPO概念', '光通信模块', '光缆概念'],
    '存储芯片': ['存储芯片', 'HBM概念'],
    '半导体': ['国产芯片', '半导体', '半导体材料', '半导体设备', '先进封装',
              '数字芯片设计', '模拟芯片设计', 'AI芯片', 'GPU概念'],
}

LIMIT = 9.8
# 近 days 内涨停次数 = 基因
def calc_gene(c, days=20):
    g = 0
    n = len(c)
    for i in range(max(1, n - days), n):
        if c[i - 1] > 0 and (c[i] / c[i - 1] - 1) * 100 >= LIMIT:
            g += 1
    return g

def analyze(code, kl):
    c, v = kl['c'], kl['v']
    n = len(c)
    ti = n - 1
    cur = c[ti]
    prev = c[ti - 1]
    today_pct = (cur / prev - 1) * 100 if prev else 0
    gene = calc_gene(c)
    # MA
    ma10 = sum(c[ti - 9:ti + 1]) / 10 if n >= 10 else cur
    ma20 = sum(c[ti - 19:ti + 1]) / 20 if n >= 20 else cur
    dev10 = (cur / ma10 - 1) * 100 if ma10 else 0
    dev20 = (cur / ma20 - 1) * 100 if ma20 else 0
    # 量比 今日/前20日
    vol20 = sum(v[max(0, ti - 20):ti]) / max(1, min(20, ti))
    volr = v[ti] / vol20 if vol20 else 0
    # 近60日最低/最高
    hi60 = max(c[max(0, n - 60):n]) if n >= 60 else max(c)
    lo60 = min(c[max(0, n - 60):n]) if n >= 60 else min(c)
    # 距60日高点回撤
    drawdown = (cur / hi60 - 1) * 100 if hi60 else 0
    return {
        'code': code, 'mv': None, 'gene': gene, 'today_pct': round(today_pct, 2),
        'dev_ma10': round(dev10, 2), 'dev_ma20': round(dev20, 2),
        'vol_ratio': round(volr, 2), 'drawdown60': round(drawdown, 2),
        'ma5': round(sum(c[ti - 4:ti + 1]) / 5, 2) if n >= 5 else cur,
        'ma10': round(ma10, 2), 'ma20': round(ma20, 2),
    }

def main():
    load_sector_mapping()
    # 由于映射表在 scan_v1224 中经 load_sector_mapping 加载:
    # 直接从 JSON 读取
    mapping = json.load(open(os.path.join(SYS, 'sector_mapping.json')))
    code2s = mapping.get('code_to_sectors', {})
    sectors_flat = set(TECH_SECTORS)
    for alias_list in TECH_ALIAS.values():
        sectors_flat.update(alias_list)

    target_codes = set()
    for code, secs in code2s.items():
        if any(s in sectors_flat for s in secs):
            target_codes.add(code)
    # 剔除创业/科创(30/301/688)
    target_codes = {code for code in target_codes if not code.startswith('30')
                    and not code.startswith('301') and not code.startswith('688')
                    and not code.startswith('920')}
    print(f'科技板块成分 (主/创) : {len(target_codes)}只', flush=True)

    # 拉K线
    kls = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(get_kl, c): c for c in target_codes}
        for fu in as_completed(futs):
            c = futs[fu]
            try:
                kl = fu.result()
                if kl:
                    kls[c] = kl
            except Exception:
                pass
    print(f'K线获取成功: {len(kls)}只', flush=True)

    # 分析
    rows = []
    for code, kl in kls.items():
        try:
            rows.append(analyze(code, kl))
        except Exception:
            pass

    # 补充市值与名称 (腾讯qt.gtimg.cn直接抓: 名称=f[1], 流通市值=f[44])
    import requests as _req
    codes = [r['code'] for r in rows]
    for i in range(0, len(codes), 80):
        b = codes[i:i + 80]
        syms = [('sh' if c.startswith('6') else 'sz') + c for c in b]
        try:
            resp = _req.get('https://qt.gtimg.cn/q=' + ','.join(syms), timeout=8)
            for line in resp.text.strip().split(';'):
                if line.strip() and '=' in line:
                    try:
                        eq = line.index('=')
                        f = line[eq + 1:].strip().rstrip(';').strip('"').split('~')
                        if len(f) > 44:
                            info = {'name': f[1], 'circ_mv': float(f[44]) if f[44] else None}
                            rows_by = {r['code']: r for r in rows}
                            if f[2] in rows_by:
                                rows_by[f[2]]['mv'] = info['circ_mv']
                                rows_by[f[2]]['name'] = info['name']
                    except Exception:
                        pass
        except Exception:
            pass
    for r in rows:
        if not r.get('name'):
            r['name'] = r['code']
        r['sectors'] = match_sector(r.get('name') or '', r['code'])[:3]

    # 分类
    def classify(r):
        g = r['gene']; t = r['today_pct']
        dd = r['drawdown60']; volr = r['vol_ratio']
        # 修复候选标准:
        # A 回踩蓄势/企稳反包: 有基因 + 高位回撤后缩量贴MA10 + 今日不深跌
        if g >= 2 and dd <= -8 and -6 <= r['dev_ma10'] <= 2 and volr <= 2.0 and t >= -5:
            bs = g * 8 + (8 if -8 <= dd <= -20 else 4) + (6 if volr <= 1.2 else 2) - max(0, -t) * 2
            return ('回踩反包', bs)
        # B 超跌修复: 回撤深, 今日企稳/止跌
        if dd <= -18 and t >= -3 and volr <= 2.0:
            bs = g * 6 + 6 + (6 if t > 0 else 0) - max(0, -t) * 2
            return ('超跌修复', bs)
        # C 强势抗跌: 基因强且今日红盘/抗跌
        if g >= 3 and t >= 0:
            bs = g * 10 + 8
            return ('强势抗跌', bs)
        return None

    cands = []
    for r in rows:
        res = classify(r)
        if res:
            cat, bs = res
            cands.append({**r, 'cat': cat, 'score': int(bs)})

    cands.sort(key=lambda x: (-x['score'], x['gene']))
    # 去重: 评分>=X 且 基因>=2
    keep = [c for c in cands if c['gene'] >= 2]

    out = {'meta': {'ts': time.strftime('%Y-%m-%d %H:%M'), 'sectors': sorted(sectors_flat)},
           'count': len(keep), 'candidates': keep[:40]}
    os.makedirs('/data/user/work/tech_repair', exist_ok=True)
    json.dump(out, open('/data/user/work/tech_repair/tech_candidates.json', 'w'),
              ensure_ascii=False, indent=1)
    print(f'\n=== 科技修复候选 {len(keep)}只 (取前20) ===')
    for c in keep[:20]:
        print(f"  [{c['cat']}] {c['name']}({c['code']}) 评分{c['score']} "
              f"基因{c['gene']} 今{c['today_pct']:+.1f}% 回撤{c['drawdown60']:.0f}% "
              f"量比{c['vol_ratio']} devMA10={c['dev_ma10']:+.1f} "
              f"板块={'/'.join(c['sectors'])}")

if __name__ == '__main__':
    main()