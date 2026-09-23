# -*- coding: utf-8 -*-
"""
edge_factors.py — 提高早盘/尾盘选股胜率的增强因子层 (v1, 2026-09-18)
====================================================================
适用前提: 用户只在【早盘(Auction)】与【尾盘(盘中/收盘前)】两个时点选股, 不做盘中实时监控.
因此本层只选用这两个时点能用数据算出来的因子, 不依赖盘中逐秒盯盘.

因子清单 (每个的胜率机理 + 适用时点):
--------------------------------------------------------------------
A 赚钱效应 / 情绪温度 (门控, 早盘与尾盘都先算):
   1) 昨日涨停今日红盘率  (赚钱效应: >60% 亢奋/可加仓, <40% 退潮/收缩)
   2) 连板晋级率           (昨日连板今日仍涨停比例: 高=接力有效)
   3) 最高连板高度         (><>高度越高, 情绪越高但顶背离风险越大)
   -> 决定当天是"进攻型"还是"收敛型", 避免情绪高位追板(胜率最大杀手)

B 首板预埋 (早盘专用): 从 昨日超跌+题材+缩量企稳 的低位票里预埋今日可能转强/首板的种子,
   弥补"只吃昨日涨停续板"而漏掉新首板的问题.

C 尾盘封板质量 (尾盘专用): 今日涨停是否开过板/炸板 + 封单稳 -> 次日溢价率与封板强度强相关.
D 尾盘抢筹 (尾盘专用): 收盘前放量拉升未涨停 -> 次日惯性.
E 风险过滤 (两时点):
   - 换手>30% 天量: 次日兑现风险高, 惩罚
   - 距日高大幅回落(破板/出货): 惩罚   (整日低吸回踩形态除外)
   - 高标(连板>=4)在弱情绪下: 回避
F 板块梯队强度: 只做当日/近3日最强板块前3, 不做冷门.
G 事件/错杀反转: 利空落地低开反而可买(如业绩/减持落地) —— 由公告层注入.
"""
import json, urllib.request as _u, time, re as _re
from collections import Counter

H = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'}
EM = 'https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=400&sort=zdp%3Adesc&date={d}&_={t}'
EMZB = 'https://push2ex.eastmoney.com/getTopicZBPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=200&sort=fund%3Aasc&date={d}&_={t}'


def _get(url):
    return _u.urlopen(_u.Request(url, headers=H), timeout=10).read().decode('utf-8', 'ignore')


def zt_pool(date):
    try:
        j = json.loads(_get(EM.format(d=date, t=int(time.time() * 1000))))
        return j.get('data', {}).get('pool') or []
    except Exception:
        return []


def zb_pool(date):
    try:
        j = json.loads(_get(EMZB.format(d=date, t=int(time.time() * 1000))))
        return j.get('data', {}).get('pool') or []
    except Exception:
        return []


def qt_bulk(codes):
    """腾讯批量实时: 返回 {code: {pct, pr, pc, hi, lo, turn, vol}}"""
    def pref(c):
        c = str(c)
        if c.startswith(('60', '68', '90', '51', '56', '58')): return 'sh' + c
        if c.startswith(('00', '30', '12', '15', '16', '18')): return 'sz' + c
        return 'bj' + c
    out = {}
    codes = list(dict.fromkeys(codes))
    for i in range(0, len(codes), 60):
        q = ','.join(pref(c) for c in codes[i:i + 60])
        try:
            t = _u.urlopen(_u.Request('https://qt.gtimg.cn/q=' + q, headers={'User-Agent': 'Mozilla/5.0'}), timeout=10).read().decode('gbk', 'ignore')
        except Exception:
            continue
        for line in t.strip().split('\n'):
            m = line.find('="')
            if m < 0: continue
            key = line[line.rfind('_', 0, m) + 1:m][2:]
            f = line[m + 2:-1].split('~')
            if len(f) < 38: continue
            try:
                pc = float(f[4]) or 0
                out[key] = {'pct': (float(f[3]) / pc - 1) * 100 if pc else 0,
                            'pt': float(f[3]), 'pc': pc, 'hi': float(f[33]), 'lo': float(f[34]),
                            'turn': float(f[38] or 0), 'vol': float(f[36] or 0)}
            except Exception:
                pass
    return out


def sentiment_temperature(pre_date, cur_date):
    """A 赚钱效应/情绪温度。返回 dict。"""
    yes = zt_pool(pre_date)   # 昨日涨停
    cur = zt_pool(cur_date)   # 今日涨停
    cur_zt = {x['n'] for x in cur}
    codes = [x['c'] for x in yes]
    qt = qt_bulk(codes)
    redn = upn = 0
    for x in yes:
        q = qt.get(str(x['c']))
        if not q: continue
        if q['pct'] >= 0: redn += 1
        if q['pct'] > 0: upn += 1
    red_rate = redn / len(yes) if yes else 0
    # 连板晋级率: 昨日 lbc>=2 今日仍涨停
    acc = [x for x in yes if int(x.get('lbc') or 1) >= 2]
    jin = [x for x in acc if x['n'] in cur_zt]
    acc_rate = len(jin) / len(acc) if acc else 0.5
    max_lbc = max((int(x.get('lbc') or 1) for x in cur), default=1)
    # 温度刻度
    if red_rate >= 0.6:
        temp = '亢奋进攻' if acc_rate >= 0.4 and max_lbc <= 6 else '高位偏热'
    elif red_rate >= 0.4:
        temp = '中性偏暖'
    elif red_rate >= 0.25:
        temp = '退潮偏冷'
    else:
        temp = '冰点收缩'
    return {'pre_zt': len(yes), 'cur_zt': len(cur), 'red_rate': round(red_rate, 2),
            'acc_rate': round(acc_rate, 2), 'max_lbc': max_lbc, 'temp': temp}


def tail_score(date, pre_date):
    """尾盘增强: 封板质量 + 未涨停强势 + 抢筹 + 风险过滤。返回筛选后的候选与新因子。"""
    cur = zt_pool(date)
    zb = {x['n'] for x in zb_pool(date)}       # 今日炸板(naming 集合已实现)
    cur_zt_name = {x['n'] for x in cur}
    sectors = Counter(x.get('hybk', '?') for x in cur)
    top3 = [k for k, _ in sectors.most_common(3)]
    # 从昨日涨停池 + 今日涨停池取候选, 再叠加模型池
    pool_json = '/workspace/v1224_picks.json'
    cand = {}
    try:
        for p in json.load(open(pool_json, encoding='utf-8'))['top_picks']:
            cand[str(p['code'])] = p['name']
    except Exception:
        pass
    for x in cur:
        cand[str(x['c'])] = x['n']
    qt = qt_bulk(list(cand.keys()))
    rows = []
    for c, nm in cand.items():
        q = qt.get(c)
        if not q or q['pc'] <= 0: continue
        pct, hi, turn = q['pct'], q['hi'] / q['pc'] * 100 - 100, q['turn']
        pull = pct - hi  # <=0 表示从日高回落
        lz = nm in cur_zt_name       # 今日是否涨停(封板)
        zbk = nm in zb               # 是否已炸板
        # 封板质量: 今日涨停且未炸板
        board_ok = lz and not zbk
        row = {'c': c, 'n': nm, 'pct': pct, 'pull': pull, 'turn': turn,
               'board_ok': board_ok, 'zbk': zbk}
        rows.append(row)
    # 评分: 尾盘可买 = 未追高 + 放量承接 + 板块强
    out = []
    for r in rows:
        secs = [k for k in top3 if k]  # 板块强度(占位, 由调用方回填更准)
        score = 0.0
        # 换手健康: 5~22 理想, <2 无量, >30 天量惩罚
        if 5 <= r['turn'] <= 22: score += 2
        elif r['turn'] > 30: score -= 2
        elif r['turn'] < 2: score -= 1
        # 未涨停强势且回落不深 (尾盘承接): 3~8%, 回落 -0.5~-4 => 次日惯性候选
        if not r['board_ok'] and not r['zbk'] and 2 <= r['pct'] <= 8 and -5 <= r['pull'] <= -0.3:
            score += 2
        # 已炸板惩罚
        if r['zbk']: score -= 3
        r['score'] = score
        out.append(r)
    out.sort(key=lambda x: -x['score'])
    return out, top3, len(cur)


def pre_open_pick(pre_date):
    """B 首板预埋(早盘): 从 昨日未涨停 + 主线题材 + 温和量价 的低位票里, 预埋今日可能转强/首板的种子。
    数据: 复用 v12.24 引擎 top 池(已含题材/模式/评分) + 昨日涨停池(排除已涨停)。
    条件: 昨日非涨停 + 非高天量 + 主线题材 + 低位或回踩(用引擎 mode 作质地代理)。
    返回: 预埋候选列表。
    """
    cand = []
    try:
        picks = json.load(open('/workspace/v1224_picks.json', encoding='utf-8'))['top_picks']
    except Exception:
        return cand
    try:
        yes = zt_pool(pre_date)
        yes_name = {x['n'] for x in yes}
    except Exception:
        yes_name = set()
    for p in picks:
        nm = p.get('name', '')
        if not nm or nm in yes_name:
            continue
        mode = p.get('mode', '')
        # 低吸质地: CORRECTION(回踩) / LOCAL_BULL 且非极高位; 题材来自引擎 sectors
        secs = p.get('sectors') or []
        if mode in ('CORRECTION',) or (mode in ('LOCAL_BULL', 'BULL') and p.get('score', 0) < 135):
            cand.append({
                'c': p.get('code'), 'n': nm, 'score': p.get('score'),
                'mode': mode, 'secs': ','.join(secs[:3]),
            })
    cand.sort(key=lambda x: -x['score'])
    return cand


def alloc_by_temp(temp):
    """动态配比: 依据情绪温度分配"打板接力% vs 均线回踩%"仓位。
    机理(来自15日回测): 回踩低吸是退潮企稳工具, 在收敛/冰点日反包胜率高;
    亢奋/上涨中继日强势直接拉走, 回踩跑输 -> 禁/缩。打板则反之, 强势日占优。
    """
    t = str(temp)
    if '亢奋' in t or '高位' in t or '进攻' in t or '热' in t:
        return {'lab': '进攻', 'zt_buy': 75, 'dip_buy': 0, 'base': 5,
                'note': '亢奋: 只做强主线/打板接力, 回踩禁用(强势直接拉走)'}
    if '退潮' in t or '冷' in t:
        return {'lab': '收敛', 'zt_buy': 25, 'dip_buy': 45, 'base': 2,
                'note': '收敛: 回踩低吸为主(反包), 打板收紧'}
    if '冰' in t or '缩' in t or '防御' in t:
        return {'lab': '防御', 'zt_buy': 10, 'dip_buy': 40, 'base': 1,
                'note': '防御: 轻仓回踩最强主线, 不追板'}      # 冰点收缩/防御
    if '中性' in t or '偏暖' in t or '暖' in t:
        return {'lab': '偏暖', 'zt_buy': 60, 'dip_buy': 10, 'base': 4,
                'note': '偏暖: 打板接力为主, 回踩极轻仓'}
    return {'lab': '中性', 'zt_buy': 50, 'dip_buy': 20, 'base': 3, 'note': '中性: 均衡'}


def live_pullback(topk=6):
    """实时主线票池 均线回踩 候选(温度在收敛/冰点时才启用).
    扫描系统板块映射成分股, 找"回踩MA5/MA10不破MA20+多头+缩量"的低吸票.
    返回: {'day':当前交易日,'rows':[{c,n,pct,chg,vr,sec,score}], 'count_check':愿检数}
    """
    try:
        from backtest_v1224_1month import get_all_stocks, match_sector
    except Exception:
        return {'day': '', 'rows': [], 'count_check': 0}
    EV_MAIN = ['PCB', '光模块', '光通信', '覆铜板', '铜箔', '算力', 'AI服务器', '存储', '先进封装', '半导体',
               '汽车零部', '整车', '智能驾驶', '激光雷达', '充电桩', '机器人', '新能源', '光伏', '风电', '储能',
               '电力', '电网', '特高压', '绿电', '医药', '创新药', '军工', '商业航天', '通信', '煤炭', '贵金属',
               '白酒', '食品', '家电']
    today = time.strftime('%Y%m%d', time.localtime())
    rows = []
    try:
        stocks = get_all_stocks()
    except Exception:
        return {'day': today, 'rows': [], 'count_check': 0}
    ncheck = 0
    for s in stocks:
        nm = s['name']; code = s['code']
        if not (match_sector(nm) or any(kw in nm for kw in EV_MAIN)):
            continue
        ncheck += 1
        sym = ('sh' if code.startswith(('60', '51', '58', '9')) else 'sz') + code
        try:
            u = f'https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_{sym}=/CN_MarketDataService.getKLineData?symbol={sym}&scale=240&ma=no&datalen=80'
            t = _u.urlopen(_u.Request(u, headers=H), timeout=12).read().decode('utf-8', 'ignore')
            j = json.loads(_re.search(r'\((\[.*\])\)', t).group(1))
            ds = [k['day'].replace('-', '') for k in j]
            cc = [float(k['close']) for k in j]; vv = [float(k['volume'] or 0) for k in j]
            if len(ds) < 21 or ds[-1] != today: continue
            t1 = cc[-1]; t1v = vv[-1]
            if t1v <= 0: continue
            ma5 = sum(cc[-5:]) / 5; ma10 = sum(cc[-10:]) / 10; ma20 = sum(cc[-20:]) / 20
            if not (t1 > ma20 and t1 <= max(ma5, ma10) * 1.01): continue
            chg = (t1 / cc[-2] - 1) * 100
            if not (-6.0 <= chg <= 3.5): continue
            v5 = sum(vv[-6:-1]) / 5
            vr = t1v / v5 if v5 else 99
            if not (0.4 <= vr <= 2.2): continue
            bull = 1 if (ma5 >= ma10 >= ma20) else 0
            sec = ','.join(match_sector(nm))
            score = (3.0 if sec else 0.0) + bull + (1.0 if vr < 1.2 else 0.0) - abs(t1 - ma10) / ma10 * 10
            rows.append({'c': code, 'n': nm, 'pct': chg, 'vr': vr, 'sec': sec[:12], 'score': round(score, 2)})
        except Exception:
            continue
    rows.sort(key=lambda r: -r['score'])
    return {'day': today, 'rows': rows[:topk], 'count_check': ncheck}


if __name__ == '__main__':
    print("=== A 情绪温度 (昨日9/17 -> 今日9/18) ===")
    temp = sentiment_temperature('20260917', '20260918')
    print(temp)