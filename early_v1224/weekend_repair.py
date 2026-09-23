#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""周末消息面主线修复优选 (2026-09-06)
针对本周末最强催化板块(仅主板可买: 剔除30/301/688/920)：
  1. AI电力/800V/液冷: 液冷概念, 数据中心, 算力概念, 智能电网, 储能概念
  2. 存储: 存储芯片
  3. 光通信: 光通信模块
  4. 人形机器人: 人形机器人, 机器人概念
  5. 油气: 油气资源, 油气设服
  6. 黄金/军工/商业航天(地缘+bottom)
技术逻辑与主引擎同源: 缩量贴MA10/回踩反包/超跌修复/强势企稳, 不追已涨停快票。
"""
import sys, os, json, time
from concurrent.futures import ThreadPoolExecutor, as_completed

SYS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SYS)
sys.path.insert(0, os.path.join(SYS, '..'))

from scan_v1224 import get_kl, get_realtime_quote, load_sector_mapping, match_sector

# 周末主题 -> 概念板块(映射表实际名称)
THEMES = [
    ('AI电力/液冷/算电', ['液冷概念', '数据中心', '算力概念', '智能电网', '储能概念']),
    ('存储芯片', ['存储芯片']),
    ('光通信/CPO', ['光通信模块']),
    ('人形机器人', ['人形机器人', '机器人概念']),
    ('油气', ['油气资源', '油气设服']),
    ('黄金', ['黄金概念']),
    ('军工/商业航天', ['军工', '商业航天', '国防军工']),
]

LIMIT = 9.8

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
    ma10 = sum(c[ti - 9:ti + 1]) / 10 if n >= 10 else cur
    ma20 = sum(c[ti - 19:ti + 1]) / 20 if n >= 20 else cur
    dev10 = (cur / ma10 - 1) * 100 if ma10 else 0
    dev20 = (cur / ma20 - 1) * 100 if ma20 else 0
    vol20 = sum(v[max(0, ti - 20):ti]) / max(1, min(20, ti))
    volr = v[ti] / vol20 if vol20 else 0
    hi60 = max(c[max(0, n - 60):n]) if n >= 60 else max(c)
    drawdown = (cur / hi60 - 1) * 100 if hi60 else 0
    return {
        'code': code, 'mv': None, 'gene': gene, 'today_pct': round(today_pct, 2),
        'dev_ma10': round(dev10, 2), 'dev_ma20': round(dev20, 2),
        'vol_ratio': round(volr, 2), 'drawdown60': round(drawdown, 2),
        'ma5': round(sum(c[ti - 4:ti + 1]) / 5, 2) if n >= 5 else cur,
        'ma10': round(ma10, 2), 'ma20': round(ma20, 2),
    }

def classify(r):
    g = r['gene']; t = r['today_pct']
    dd = r['drawdown60']; volr = r['vol_ratio']

    def dd_bonus():
        # 深回撤反包校准: 封板密集区约-20%~-35%, 上移加分权重
        if -35 <= dd <= -20:
            return 10
        if -8 <= dd <= -20:
            return 5
        return 3

    if t > 5:
        return ('已涨追高', -99)          # 不追高位快票
    if g >= 2 and dd <= -6 and -6 <= r['dev_ma10'] <= 2.5 and volr <= 1.8 and t >= -5:
        # 回踩反包: 深回撤(上调至-35%)重加分 + 放量温和(volr 0.9~1.4最佳)加分
        vol_bonus = 6 if 0.9 <= volr <= 1.4 else (4 if volr <= 1.8 else 0)
        bs = g * 8 + dd_bonus() + vol_bonus - max(0, -t) * 2
        return ('回踩反包', bs)
    if -35 <= dd <= -18 and t >= -4:
        # 超跌修复: 深回撤(扩容至-35%) + 放量(volr>=1.1)优先, 缩量不加分
        vol_bonus = 6 if volr >= 1.1 else 0
        bs = g * 6 + 6 + vol_bonus + (5 if t > 0 else 0) - max(0, -t) * 2
        return ('超跌修复', bs)
    if -4 <= t <= 4 and -6 <= r['dev_ma10'] <= 8 and volr <= 1.3:
        bs = 10 - max(0, -t) + (6 if volr <= 0.8 else 2)
        return ('贴MA10企稳', bs)
    if g >= 3 and t >= 0:
        bs = g * 8 + 6
        return ('强势抗跌', bs)
    return None

def main():
    load_sector_mapping()
    mapping = json.load(open(os.path.join(SYS, 'sector_mapping.json')))
    sts = mapping.get('sector_to_stocks', {})
    code2n = mapping.get('code_to_name', {})
    code2s = mapping.get('code_to_sectors', {})

    target = {}
    for theme, secs in THEMES:
        codes = set()
        for sec in secs:
            for item in (sts.get(sec, []) or []):
                if isinstance(item, dict):
                    codes.add(item.get('code'))
                else:
                    codes.add(item)
        # 主板可买: 剔除创业/科创/北交
        codes = {c for c in codes if c and not c.startswith('30') and not c.startswith('301')
                 and not c.startswith('688') and not c.startswith('920')}
        target[theme] = codes

    all_codes = set()
    for v in target.values():
        all_codes.update(v)
    print(f'周末主题成分(主板可买): {len(all_codes)}只', flush=True)

    # 拉K线
    kls = {}
    with ThreadPoolExecutor(max_workers=14) as ex:
        futs = {ex.submit(get_kl, c): c for c in all_codes}
        for fu in as_completed(futs):
            c = futs[fu]
            try:
                kl = fu.result()
                if kl:
                    kls[c] = kl
            except Exception:
                pass
    print(f'K线获取成功: {len(kls)}只', flush=True)

    rows = []
    for code, kl in kls.items():
        try:
            r = analyze(code, kl)
            r['name'] = code2n.get(code, code)
            rows.append(r)
        except Exception:
            pass

    # 分类
    cands = []
    for r in rows:
        res = classify(r)
        if not res or res[0] == '已涨追高':
            continue
        cat, bs = res
        r['cat'] = cat
        r['score'] = int(bs)
        r['themes'] = [th for th, codes in target.items() if r['code'] in codes]
        cands.append(r)

    cands.sort(key=lambda x: (-x['score'], x['gene']))
    keep = cands[:40]

    out = {'meta': {'ts': time.strftime('%Y-%m-%d %H:%M'),
                    'themes': {th: len(c) for th, c in target.items()}},
           'count': len(keep), 'candidates': keep}
    os.makedirs('/data/user/work/weekend_repair', exist_ok=True)
    json.dump(out, open('/data/user/work/weekend_repair/weekend_candidates.json', 'w'),
              ensure_ascii=False, indent=1)

    print(f'\n=== 周末消息面主线修复候选 {len(keep)}只 ===')
    for c in keep:
        print(f"  [{c['cat']}] {c['name']}({c['code']}) 评分{c['score']} 基因{c['gene']} "
              f"今{c['today_pct']:+.1f}% 回撤{c['drawdown60']:.0f}% 量比{c['vol_ratio']} "
              f"devMA10={c['dev_ma10']:+.1f} 主题={'/'.join(c['themes'])}")
    print(f'\n已保存: /data/user/work/weekend_repair/weekend_candidates.json')

if __name__ == '__main__':
    main()