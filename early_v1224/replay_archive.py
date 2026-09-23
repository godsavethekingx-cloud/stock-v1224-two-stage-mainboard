#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日推荐归档 replay_archive.py
========================================================
用途: 把每一日已生成的推荐(竞价池/主线布局/早盘manual/尾盘weekend)结构化落地,
     以便按日统计「早盘抓当日 vs 尾盘抓次日」的胜率/封板率。
用法:
  python3 replay_archive.py --seed 20260906      # 补录历史(0906/0907)
  python3 replay_archive.py --date 20260908      # 追加某日(需各源文件已生成)
  python3 replay_archive.py --verify 20260907    # 校验并打印该日摘要
输出: /data/user/work/replay/YYYYMMDD.json
"""
import os, re, sys, json, argparse

REPLAY = '/data/user/work/replay'
os.makedirs(REPLAY, exist_ok=True)
args = None  # 供 save 内引用 --force

# ---------- 历史手工样本(从已交付的HTML/文本提取) ----------
SEED = {
 '20260906': {
   'meta': {'tag': '周末消息面weekend-repair + V18反包池(次日盘前介入)'},
   'groups': {
     '算电/AI电力修复': [  # weekend-repair 主线候选
        ('002028','思源电气'),('002169','智光电气'),('603887','城地香江'),('601700','风范股份'),
        ('002579','中京电子'),('600406','国电南瑞'),('600312','平高电气'),('600517','国网英大'),
        ('002364','中恒电气'),('002335','科华数据'),('002837','英维克'),
     ],
     '光通信/油气/黄金修复': [
        ('603083','剑桥科技'),('002792','通宇通讯'),('002353','杰瑞股份'),('601899','紫金矿业'),
        ('600330','天通股份'),
     ],
     'V18反包池(主板)': [
        ('603758','秦安股份'),('603330','天洋新材'),('002172','澳洋健康'),('603958','哈森股份'),
        ('000505','京粮控股'),('600272','开开实业'),('002589','瑞康医药'),('002081','金螳螂'),
        ('603079','圣达生物'),('600103','青山纸业'),
     ],
   }
 },
 '20260907': {
   'meta': {'tag': '竞价强度早盘选股 + 上攻主线布局 + 早盘manual(当日介入)'},
   'groups': {
     '竞价池·早盘低吸': [
        ('603679','华体科技'),('000887','中鼎股份'),('002892','科力尔'),('002026','山东威达'),
        ('603286','日盈电子'),('603926','铁流股份'),('002815','崇达技术'),('002436','兴森科技'),
        ('002938','鹏鼎控股'),('002975','博杰股份'),('601231','环旭电子'),('002281','光迅科技'),
        ('000833','粤桂股份'),
     ],
     '上攻主线布局': [
        ('601138','工业富联'),('002371','北方华创'),('603019','中科曙光'),('002747','埃斯顿'),
     ],
     '券商(观察)': [('600030','中信证券'),('601788','光大证券')],
     '早盘manual': [
        ('603728','鸣志电器'),('600479','千金药业'),('600969','郴电国际'),('603989','艾华集团'),
        ('600611','大众交通'),
     ],
   }
 },
}

def _norm(code):
    return re.sub(r'\D', '', str(code))[-6:] if code else ''

def flatten(groups):
    rows = []
    for g, lst in groups.items():
        for item in lst:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                rows.append({'code': _norm(item[0]), 'name': item[1], 'group': g})
            elif isinstance(item, dict):
                rows.append({'code': _norm(item.get('code')), 'name': item.get('name',''),
                             'group': g, **{k: v for k, v in item.items() if k not in ('code','name')}})
    # 去重
    seen, uniq = set(), []
    for r in rows:
        if r['code'] not in seen and r['code']:
            seen.add(r['code']); uniq.append(r)
    return uniq

def load_index():
    p = os.path.join(REPLAY, 'index.json')
    return json.load(open(p)) if os.path.exists(p) else { 'schema': 1 }

def save(date, groups, meta, name_src='manual'):
    rows = flatten(groups)
    fn = f'{date}.json'
    fp = os.path.join(REPLAY, fn)
    if os.path.exists(fp) and not (args and args.force):
        print(f'{fn} 已存在(--force 可覆盖),跳过'); return
    doc = {'date': date, 'name_src': name_src, 'meta': meta,
           'picks': rows, 'count': len(rows)}
    idx = load_index()
    idx[date] = fn
    json.dump(idx, open(os.path.join(REPLAY, 'index.json'), 'w'), ensure_ascii=False, indent=1)
    print(f'归档完成 {fn} | {len(rows)} 只 | 分组: {sorted(set(r["group"] for r in rows))}')

def seed(date):
    d = SEED.get(date)
    if not d:
        print(f'未知历史样本 {date},可选: {sorted(SEED)}'); return
    save(date, d['groups'], d['meta'])

def verify(date):
    fn = os.path.join(REPLAY, f'{date}.json')
    if not os.path.exists(fn):
        print(f'{date}.json 不存在,先 --seed 或 --date 生成')
        return
    doc = json.load(open(fn))
    bad = [r for r in doc['picks'] if not re.fullmatch(r'\d{6}', r['code'])]
    dup = len(doc['picks']) - len({r['code'] for r in doc['picks']})
    print(f'=== {date} 归档校验 ===')
    print(f'总股票数: {doc["count"]} | 非法代码: {len(bad)} | 重复: {dup}')
    for g in sorted({r['group'] for r in doc['picks']}):
        l = [r['code'] for r in doc['picks'] if r['group'] == g]
        print(f'  {g}: {len(l)}只 {l}')

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', help='补录内置历史样本,如 20260906')
    ap.add_argument('--date', help='序号某日(需保底源文件),如 20260908')
    ap.add_argument('--verify', help='校验某日,如 20260907')
    a = ap.parse_args()
    if a.seed: seed(a.seed)
    elif a.date:
        # 自动从 daily JSON / HTML 抽取(占位: 我会每日在此校准后调用)
        print('自定义追加: 请用 --seed 内置或扩展 SEED。')
    elif a.verify: verify(a.verify)
    else:
        for d in sorted(SEED): seed(d)
    if not a.verify and not a.date:
        for d in sorted(SEED): verify(d)