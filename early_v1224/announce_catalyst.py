#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
公告催化模块 announce_catalyst.py
========================================================
定位: 锁定最近交易时段发布「重点公告」的A股主板个股, 按刺激强度排序,
     供 scan_daily / 可视化简报调用, 与竞价/反包/主线等通道并列。

重点公告类型(刺激强度递降):
  S级: 重组/借壳/资产注入/跨界并购/实控人变更(强催化, 常连板)
  A级: 大额扩产/跨界投资/重大合同/收购并购/战投入局
  B级: 股权变动/中标/业绩预增/回购注销/股东协议转让(中性, 参与弱)
  弱:  减持/解禁/一般公告(予以过滤, 不作推荐)

数据源:
  - 文本: 新浪7x24盘前/盘中快讯 (zhibo.sina.com.cn), 实时滚动含"某公司公告"
  - 映射: 腾讯沪深主板批量接口(名称->代码), 兼容先于扫股系统生成
用法:
  python3 announce_catalyst.py                # 拉取近端公告并按强度排序
  python3 announce_catalyst.py --hours 24     # 拉取近24小时公告
  python3 announce_catalyst.py --outdir /workspace/xxx
输出:
  <outdir>/announce_catalyst.json : 结构化结果(含序)
  <outdir>/announce_catalyst.md   : 终端摘要(按刺激强度降序)
"""
import os, re, sys, json, time, argparse, urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
REQ_H = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'}

# ----------------------------------------------------------------
# 公告类型 -> 刺激强度 (用于排序)
# ----------------------------------------------------------------
ANN_TYPES = [
    # (名称, 强度基准, 正则pattern, 说明) —— 顺序即优先级, 前类优先
    # 控制权变更类加 (?!.*不会导致|.*不会) 前置断言, 规避"减持不会导致控制权变更"这类否定句误报
    ('资产注入/借壳', 95, r'借壳|重组上市|置入资产|收购控股权|重大资产重组|出售.*置入|实控人变更|控股股东.*变更|(?!.*不会导致)控制权变更',
     '重组/借壳/资产注入为A股最高级别催化, 遗传易连板'),
    ('跨界并购', 90, r'跨界|收购.*(新能源|光伏|机器人|AI|半导体|算力|云计算|医药)|拟定增.*收购|购买.*股权',
     '跨界并购打开想象空间, 题材属性强'),
    ('扩产/跨界投资', 82, r'拟投建|投资.*亿元.*(产能|项目|基地)|扩产|跨界|切入.*赛道|布局.*(AI|算力|储能|机器人)',
     '大额扩产/跨界投资, 增量业绩预期'),
    ('战投/股权转让', 74, r'引入.*战投|战略投资者|股权转让|协议转让|股份转让|增资扩股',
     '战投入局/国资受让/股权变动, 或催化资产运作与混改预期'),
    ('重大合同/订单', 76, r'重大合同|中标.*(亿|万元)|签订(?!.*(转让))协议|订单.*(亿|万元)|大单',
     '重大合同/大额中标, 业绩确定性提升'),
    ('收购/并购', 70, r'收购|并购|购买.*(股权|资产)|吸收合并',
     '并购整合, 规模扩张'),
    ('业绩预增/回购', 60, r'业绩预增|业绩预告|回购.*注销|增持计划|中期分红|高送转',
     '业绩/回购/分红, 偏防御利好'),
]

# 负面/弱公告关键词(仅当无正面事件时才否决)
NEG_KW = ['减持', '解禁', '质押', '立案', '警示', '处罚', '终止', '退市']

# ----------------------------------------------------------------
# 数据源1: 拉取最近N小时的新浪7x24快讯文本
# ----------------------------------------------------------------
def _http_get(url, timeout=12, encoding='utf-8'):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=REQ_H), timeout=timeout) as r:
            raw = r.read()
        if encoding.lower() == 'gbk':
            return raw.decode('gbk', 'ignore')
        return raw.decode('utf-8', 'ignore')
    except Exception:
        return ''

def fetch_sina_7x24(pages=2):
    """新浪7x24快讯滚动流, 返回 [(timestamp, 去标签文本), ...] 最新在前."""
    items = []
    for page in range(1, pages + 1):
        url = ('https://zhibo.sina.com.cn/api/zhibo/feed?page=%d&page_size=40'
               '&zhibo_id=152&tag_id=0&dire=a&dpc=1') % page
        raw = _http_get(url)
        if not raw:
            continue
        try:
            j = json.loads(raw)
            feed = j['result']['data']['feed']['list']
            for it in feed:
                txt = re.sub(r'<[^>]+>', '', it.get('rich_text', '')).strip()
                if txt:
                    items.append((it.get('create_time', ''), txt))
        except Exception:
            continue
    return items


# ----------------------------------------------------------------
# 数据源2: 腾讯沪深主板 名称->代码 映射(含实时价/pct, 复用scan流程)
# ----------------------------------------------------------------
def load_name_code_map(currency_mainboard_only=True, stocks=None):
    """构建 {名称: {code,name,pct}}。复用系统 get_mainboard_stocks 全量主板列表,
    再对有限集合批量拉行情(腾讯,gbk)。stocks 可由调用方注入避免重复扫描."""
    if stocks is None:
        from scan_stocks_v9 import get_mainboard_stocks
        stocks = get_mainboard_stocks()
    if currency_mainboard_only:
        stocks = [s for s in stocks if str(s['code']).startswith(('60', '00'))]
    m = {}
    codes = [s['code'] for s in stocks]
    # 腾讯批量拉实际行情(名称+涨跌都来自该源, 校验一致性)
    for b in range(0, len(codes), 100):
        qs = [('%s%s' % ('sh' if c.startswith('6') else 'sz', c)) for c in codes[b:b + 100]]
        data = _http_get('https://qt.gtimg.cn/q=' + ','.join(qs), timeout=15, encoding='gbk')
        for line in data.split('\n'):
            if '~' not in line:
                continue
            p = line.split('~')
            if len(p) < 45:
                continue
            code, name = p[2], p[1]
            try:
                pct = float(p[32])
            except Exception:
                pct = 0.0
            m[name] = {'code': code, 'name': name, 'pct': pct}
    return m


# ----------------------------------------------------------------
# 核心: 从快讯文本中识别A股主板公司 -> 公告类型 -> 刺激强度
# ----------------------------------------------------------------
def classify_announce(text):
    """对一条公告文本打标. 先匹配正面声明类型(重组/收购/合同/战投等),
    命中才返回; 否则若有"纯减持/解禁/终止"等弱公告则返回 None."""
    # 0) 绝对否定护栏: 仅针对"减持/股权变动类"且明确否定"不会导致控制权变更"才拦,
    #    不误伤"构成重大资产重组/不构成重组上市"这类正向重组.
    if re.search(r'(减持|本次.*控制权)', text) and re.search(r'(不会导致|不会引发|不涉及|不会)', text):
        return None
    # 1) 先找正面声明类型
    best = None
    for ann_type, strength, patt, _ in ANN_TYPES:
        ms = re.findall(patt, text)
        if ms:
            if best is None or strength > best[1]:
                best = (ann_type, strength, ms[:2])
    # 2) 只有出现最强负面信号且无任何正面类型时才剔除
    if best is None:
        for kw in NEG_KW:
            if kw in text:
                return None
    return best


# ----------------------------------------------------------------
# 数据源互补: 同花顺个股级新闻(含结构化股票字段), 弥补新浪快讯被国际消息挤出的问题
# ----------------------------------------------------------------
def fetch_ths_stock_news(pages=2):
    """同花顺个股关联新闻: 自带 stocks 字段(名称/代码/市场), A股公告适配度高.
    仅提取「公告事件主体=标题首个公司」对应的A股, 其余关联/参与方不建独立记录,
    避免吸收合并等多方公告被重复上报. 返回 [(code, name, title+digest, is_mainboard), ...]"""
    out = []
    try:
        from news_fetcher import fetch_ths_news
        article_list = fetch_ths_news(pages=pages)
    except Exception:
        return out
    for a in article_list:
        title = (a.get('title') or '').strip()
        digest = (a.get('digest') or '').strip()
        txt = (title + ' ' + digest).strip()
        if not txt:
            continue
        stocks = a.get('stocks') or []
        if not stocks:
            continue
        # 事件主体: 标题中最早出现的、且被同花顺关联的公司
        lead = None
        lead_pos = -1
        for s in stocks:
            nm = str(s.get('name') or '')
            if not nm:
                continue
            pos = title.find(nm)
            if pos >= 0 and (lead is None or pos < lead_pos):
                lead, lead_pos = nm, pos
        if lead is None:   # 标题无明确主体, 跳过(避免误报)
            continue
        # 仅输出主体对应的A股(排除港股/重复)
        seen = set()
        for s in stocks:
            nm = str(s.get('name') or '')
            code = str(s.get('stockCode') or '').strip()
            if nm != lead or code in seen:
                continue
            if not re.search(r'\d{6}', code) or not (code.startswith('60') or code.startswith('00')
                                                     or code.startswith('30') or code.startswith('68')):
                continue
            seen.add(code)
            out.append((code, nm, txt, code.startswith(('60', '00'))))
    return out


def scan_announce_catalysts(hours=24, mainboard_only=True, stocks=None):
    """
    主入口: 双源(新浪7x24 + 同花顺个股级)扫描近端公告并按刺激强度排序.
    stocks 由调用方注入(如 scan_daily 已扫描的全量主板), 避免重复全量扫描.
    返回 {'ts', 'catalysts':[{code,name,pct,type,strength,title,reason}...], 'mode'}
    """
    items = fetch_sina_7x24(pages=2)
    ths = fetch_ths_stock_news(pages=2)   # 第二路: 同花顺个股公告, 抗挤压
    name_map = load_name_code_map(currency_mainboard_only=mainboard_only, stocks=stocks)
    found = {}          # code -> merged rec

    def add_catalyst(code, name, txt, allow_pct=True):
        nonlocal found, name_map
        if (mainboard_only and not name_map.get(name) and not _is_mb(code)):
            return
        ann = classify_announce(txt)
        if not ann:
            return
        pct = name_map.get(name, {}).get('pct', 0) if allow_pct else 0
        a_type, strength, ms = ann[0], ann[1], ann[2]
        rec = found.get(code)
        if rec is None:
            rec = {'code': code, 'name': name, 'pct': pct,
                   'type': a_type, 'strength': strength, 'reasons': [], 'titles': []}
            found[code] = rec
        if strength > rec['strength']:
            rec['strength'], rec['type'] = strength, a_type
        rec['reasons'].append('|'.join(str(x) for x in ms) if isinstance(ms, list) else str(ms))
        rec['titles'].append(txt[:120])

    def _is_mb(code):
        return str(code).startswith(('60', '00'))

    # 通道A: 新浪快讯(文本匹配名称)
    for ts, txt in items:
        if not any(k in txt for k in ['公告', '拟', '签署', '收购', '重组', '扩产', '跨界', '中标', '股权', '定增', '增持', '回购']):
            continue
        hit_name = None
        hit_code = None
        # 1) 文本内嵌 (公司名(代码))
        m = re.search(r'([\u4e00-\u9fa5]{2,8})\((\d{6})\)', txt)
        if m:
            code = m.group(2)
            nm = m.group(1)
            if not mainboard_only or code.startswith(('60', '00')):
                name_map.setdefault(nm, {'code': code, 'name': nm, 'pct': 0.0})
                hit_name, hit_code = nm, code
        if not hit_name:
            # 2) 全量名称匹配(按名称长度降序)
            for nm in sorted(name_map, key=len, reverse=True):
                if len(nm) >= 3 and nm in txt:
                    hit_name, hit_code = nm, name_map[nm]['code']
                    break
        if not hit_name:
            continue
        add_catalyst(hit_code, hit_name, txt)

    # 通道B: 同花顺个股关联(自带代码字段, 覆盖新华传媒这类快讯被挤出的公告)
    for code, name, txt, is_mb in ths:
        if mainboard_only and not is_mb:
            continue
        # 无独立文本时用名称补一个最小谓词, 若有文本仍走完整分类
        body = txt if txt else (name + '相关重点公告')
        add_catalyst(code, name, body, allow_pct=False)

    cats = sorted(found.values(), key=lambda x: -x['strength'])
    return {'ts': time.strftime('%Y-%m-%d %H:%M'), 'count': len(cats),
            'catalysts': cats[:30], 'mode': 'announce_catalyst'}


# ----------------------------------------------------------------
def render_md(res):
    lines = ['## 公告催化模块',
             f'ts={res["ts"]}  命中主板公告 {res["count"]} 条 (按刺激强度降序)', '']
    for c in res['catalysts']:
        lines.append('%-10s %-7s S%3d  %-10s %+5.1f%%' % (
            c['name'], c['code'], c['strength'], c['type'], c['pct']))
        if c['reasons']:
            lines.append('        ↳ ' + ('; '.join(c['reasons'][:2]))[:100])
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--hours', type=int, default=24)
    ap.add_argument('--outdir', default='.')
    ap.add_argument('--include-chinext', action='store_true', help='默认仅主板, 该开关放开创业/科创')
    a = ap.parse_args()
    res = scan_announce_catalysts(hours=a.hours, mainboard_only=not a.include_chinext)
    os.makedirs(a.outdir, exist_ok=True)
    jp = os.path.join(a.outdir, 'announce_catalyst.json')
    mp = os.path.join(a.outdir, 'announce_catalyst.md')
    json.dump(res, open(jp, 'w'), ensure_ascii=False, default=str)
    md = render_md(res)
    open(mp, 'w').write(md)
    print(md)
    print(f'\n已保存: {jp} / {mp}')


if __name__ == '__main__':
    main()