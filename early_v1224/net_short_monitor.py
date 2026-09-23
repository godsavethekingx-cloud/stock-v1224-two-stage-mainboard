# -*- coding: utf-8 -*-
"""
net_short_monitor.py — 机构股指期货净空单监控 (短线选手信号层)

数据口径: 中金所每日公布的股指期货(IF/IC/IM)前20名会员 空单-多单 持仓(单位:手)。
  net_short > 0  ==> 机构净空单(空头主导);   net_short < 0 ==> 机构净多单(多头主导)。

4 条规则 (用户在研信号):
  R1 机构连续空单:   连续≥3 天净空单主导(空>多)          => 警惕大盘"退潮"走势
  R2 机构连续多单:   连续≥3 天净多单主导(多>空)          => 期待大盘"修复"行情
  R3 北向流入后大空单: 北向持续流入(近5日)后 机构单日突现大空单 => 大盘将现较大分歧
  R4 大跌后大多单:    大盘连续下跌多日后 机构单日突现大多单  => 大盘即将反转

数据来源优先级: ①中金所官网XML(尽力而为) -> ②本地缓存 net_short_cache.json -> ③盘后手动录入
用法:
  import:   from net_short_monitor import build_net_short_signal
  CLI录数:  python3 net_short_monitor.py --set "2026-08-26:31800" --set "2026-08-27:29600"
            (数值=当日 IF 前20净空单=空-多, 手; 正=空头主导)
  CLI演示:  python3 net_short_monitor.py --demo
备注: 2024-08 起北向资金仅按季度披露、盘中不可得; R3 的"北向持续流入"可用
      主力资金连续净流入做代理, 或盘后按季披露人工填北向序列。
"""

import os
import re
import sys
import json
import ssl
import urllib.request
from datetime import datetime, timedelta

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'net_short_cache.json')


def _norm_date(d):
    """把 '2026-08-17' / '20260817' 统一为 'YYYY-MM-DD'。"""
    m = re.search(r'(\d{4})-?(\d{2})-?(\d{2})', str(d))
    if m:
        return f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
    return str(d)[:10]


def now_bj():
    return datetime.utcnow() + timedelta(hours=8)


# ============================================================
# 数据获取: ①中金所XML (尽力而为)  ②缓存  ③手动
# ============================================================
def _fetch_cffex(date_str, timeout=7):
    """尝试拉取中金所某交易日净空单。返回 net_short(float) 或 None。"""
    for host in ('https://www.cffex.com.cn', 'http://www.cffex.com.cn'):
        ym = date_str[:6]
        for fname in (f'{date_str}_IF.xml', f'{date_str}.xml'):
            url = f'{host}/sj/ccpm/{ym}/{fname}'
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
                    txt = r.read().decode('utf-8', 'ignore')
                if txt and 'IF' in txt and '<' in txt:
                    net = _parse_xml(txt)
                    if net is not None:
                        return net
            except Exception:
                continue
    return None


def _parse_xml(txt):
    """解析中金所持仓XML: 前20会员 空单合计 - 多单合计。返回 net 或 None。"""
    # 兼容多种字段命名
    long_pat = r'<LongPosition(?:Value)?[^>]*>([\d.]+)</LongPosition(?:Value)?>'
    short_pat = r'<ShortPosition(?:Value)?[^>]*>([\d.]+)</ShortPosition(?:Value)?>'
    longs = [float(x) for x in re.findall(long_pat, txt)]
    shorts = [float(x) for x in re.findall(short_pat, txt)]
    # 只取前20家(按XML顺序通常已是排名)
    if longs and shorts:
        return sum(shorts[:20]) - sum(longs[:20])
    return None


def _load_cache():
    try:
        with open(CACHE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f).get('IF', {})
    except Exception:
        return {}


def _save_cache(data):
    cache = {}
    try:
        with open(CACHE_PATH, 'r', encoding='utf-8') as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    cache['IF'] = data
    cache['updated'] = now_bj().strftime('%Y-%m-%d %H:%M')
    with open(CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _set_manual(date_str, net_short):
    data = _load_cache()
    data[date_str] = float(net_short)
    _save_cache(data)
    return data


def load_position_series(max_days=8, fetch=None):
    """返回按日期升序的 [(date, net_short), ...]。默认读取本地缓存/手动录入;
    仅当环境变量 NETSHORT_FETCH=1 或 fetch=True 时联网尝试中金所, 以免拖慢主流程。"""
    if fetch is None:
        fetch = os.environ.get('NETSHORT_FETCH') == '1'
    series = {}
    cache = _load_cache()
    # 1) 缓存兜底
    for d in cache:
        try:
            series[d] = float(cache[d])
        except Exception:
            continue
    # 2) 仅显式开启时才联网补最近 N 个交易日(只试缺失的、最近的3天)
    if fetch:
        today = now_bj().date()
        tried = 0
        for i in range(0, 10):
            d = (today - timedelta(days=i)).strftime('%Y%m%d')
            if d in series:
                continue
            net = _fetch_cffex(d)
            if net is not None:
                series[d] = net
                tried += 1
                if tried >= 3:
                    break
    # 排序+去非交易日
    ordered = sorted((d, v) for d, v in series.items())
    if ordered:
        _save_cache({_norm_date(d): v for d, v in ordered})
    return [(_norm_date(d), v) for d, v in ordered]


# ============================================================
# 规则引擎
# ============================================================
def _consecutive(series, across_short):
    """从最近一天往回数连续空头主导(across_short=True)或多头主导的天数。"""
    n = len(series)
    if n == 0:
        return 0
    count = 0
    for i in range(n - 1, -1, -1):
        net = series[i][1]
        is_short = net > 0        # 空头主导
        is_long = net < 0         # 多头主导
        hit = is_short if across_short else is_long
        if hit:
            count += 1
        else:
            break
    return count


def _spike(series, direction):
    """最近一日相对前 min(n-1,3) 日均值的突变幅度 direction=+1空增 / -1多增。返回倍率或None。"""
    n = len(series)
    if n < 2:
        return None
    today = series[-1][1]
    prev = [v for _, v in series[-min(n - 1, 4):-1]]
    base = sum(prev) / len(prev) if prev else 0
    if abs(base) < 1e-6:
        return None
    if direction > 0:      # 空单突增: today 明显 大于 前均 (今日空头增强)
        return (today - base) / abs(base)
    else:                  # 多单突增: today 明显 小于 前均 (今日空头骤降=转多)
        return (base - today) / abs(base)


def analyze_net_short(position_series, index_pct_series=None, north_flow_series=None,
                      main_inflow_series=None, r3_use_main=True):
    """核心规则判定。
    position_series: [(date, net_short)] 升序
    index_pct_series: [pct, ...] 近N日大盘日涨跌幅(升序), 用于R4
    north_flow_series / main_inflow_series: 近N日累计流入方向, 用于R3(北向不可得时用主力)
    """
    signals = {'r1': False, 'r2': False, 'r3': False, 'r4': False}
    reasons = []
    if not position_series:
        return {'available': False, 'signals': signals, 'reasons': ['机构净空单数据未获取'], 'verdict': 'NO_DATA'}

    consec_a = _consecutive(position_series, across_short=True)
    consec_m = _consecutive(position_series, across_short=False)

    # R1
    if consec_a >= 3:
        signals['r1'] = True
        reasons.append(f'R1触发: 机构连续{consec_a}天净空单主导 → 警惕大盘退潮走势')
    # R2
    if consec_m >= 3:
        signals['r2'] = True
        reasons.append(f'R2触发: 机构连续{consec_m}天净多单主导 → 期待大盘修复行情')

    # R3 北向持续流入后机构突现大空单 (北向不可得时用主力资金代理)
    inflow = main_inflow_series if (r3_use_main or not north_flow_series) else north_flow_series
    if inflow is not None:
        sustained_in = all(v > 0 for v in inflow[-5:]) if len(inflow) >= 2 else False
        spike = _spike(position_series, direction=1)
        if sustained_in and spike is not None and spike >= 0.5:
            signals['r3'] = True
            reasons.append(f'R3触发: 外资/主力持续流入后机构单日大空单(较前均放大{spike:.1f}倍)&净空单增强 → 大盘将现较大分歧')

    # R4 大盘连续下跌多日 + 机构突现大多单
    if index_pct_series and len(index_pct_series) >= 2:
        down_days = 0
        for p in index_pct_series[-4:]:
            if p < 0:
                down_days += 1
        recent_down = down_days >= 2 or (len(index_pct_series) >= 3 and sum(index_pct_series[-3:]) < 0)
        spike = _spike(position_series, direction=-1)
        if recent_down and spike is not None and spike >= 0.5:
            signals['r4'] = True
            reasons.append(f'R4触发: 大盘连续下跌后机构单日大幅转多(多单/空单离场增{spike:.1f}倍) → 即将反转')

    # 综合定势
    if signals['r4']:
        verdict = 'REVERSE'        # 反转在即
        action = '下跌末端+机构转多: 可逐步左侧布局; 反转确认后再加仓'
    elif signals['r1']:
        verdict = 'WITHDRAW'       # 退潮
        action = '机构连续净空: 防守优先, 降低仓位, 停止追高/通道B, 只留通道A低吸'
    elif signals['r3']:
        verdict = 'DIVERGENCE'     # 分歧
        action = '流入后突发大空单: 高位分歧, 减仓兑现, 等候分歧结束方向'
    elif signals['r2']:
        verdict = 'REPAIR'         # 修复
        action = '机构连续净多: 可期待修复, 回踩低吸为主线'
    else:
        verdict = 'NEUTRAL'
        action = '机构净持仓多空未决: 维持既有节奏, 等信号确认'

    return {
        'available': True,
        'series': [{'date': d, 'net_short': v} for d, v in position_series],
        'consecutive_short': consec_a,
        'consecutive_long': consec_m,
        'signals': signals,
        'reasons': reasons,
        'verdict': verdict,
        'action': action,
    }


def build_net_short_signal(market, index_pct_series=None, main_inflow_series=None):
    """供 run_v9_final 调用: 取数+判定, 返回可直接输出/写入output的 dict。"""
    series = load_position_series()
    if index_pct_series is None and isinstance(market, dict):
        index_pct_series = market.get('pct_serie_5d', None)
    main_inflow = None
    if isinstance(market, dict) and market.get('main_inflow_5d'):
        main_inflow = market['main_inflow_5d']
    if main_inflow_series:
        main_inflow = main_inflow_series
    res = analyze_net_short(series, index_pct_series, main_inflow_series=main_inflow)
    res['used_main_as_north'] = (main_inflow is not None)
    return res


# ============================================================
# CLI
# ============================================================
def _demo_data():
    """规则引擎演示: 用示意序列验证4条规则输出(非真实市场数据)。"""
    def run(note, pos, idx=None, infl=None):
        r = analyze_net_short(pos, index_pct_series=idx, main_inflow_series=infl)
        print(f"\n[{note}] signs={ {k: v for k, v in r['signals'].items()} } verdict={r['verdict']}")
        for x in r['reasons']:
            print('   ', x)
        print('    行动:', r['action'])

    run('R2多单', [('d1', -3000), ('d2', -4200), ('d3', -5100)])                       # 连续3天净多
    run('R1空单', [('d1', 2500), ('d2', 3600), ('d3', 4800)])                          # 连续3天净空
    run('R3分歧', [('d1', 0), ('d2', -600), ('d3', -500), ('d4', -400), ('d5', 21000)],
        infl=[100, 200, 150, 180, 120])                                                # 主力持续流入后单日大空单
    run('R4反转', [('d1', 8000), ('d2', 8600), ('d3', 8200), ('d4', -7400)],
        idx=[-1.2, -0.8, -1.1])                                                        # 连跌后单日大幅转多
    run('中性', [('d1', 1200), ('d2', -900), ('d3', 1400)])


def sync_from_akshare(days=8):
    """[真实数据] 从 akshare 拉取中金所 IF 全合约前20会员净空单, 每日作为个股序列写入缓存。
    值 = 空单top20 - 多单top20 (手), >0 空头主导。返回 (series_dict, ok, msg)。"""
    try:
        import akshare as ak
    except Exception as e:
        return {}, False, f'akshare未安装或导入失败: {e}'
    today = now_bj().date()
    start = (today - timedelta(days=days + 4)).strftime('%Y%m%d')
    end = today.strftime('%Y%m%d')
    try:
        df = ak.get_rank_sum_daily(start_day=start, end_day=end, vars_list=['IF'])
    except Exception as e:
        return {}, False, f'get_rank_sum_daily失败: {e}'
    if df is None or df.empty:
        return {}, False, '返回空数据'
    out = {}
    if 'symbol' in df.columns and 'long_open_interest_top20' in df.columns:
        agg = df[df['symbol'] == 'IF']           # 全合约汇总行
        dcol = 'date' if 'date' in agg.columns else 'variety_date'
        for _, r in agg.iterrows():
            d = str(r.get(dcol) or '')
            if len(d) < 8:
                continue
            ds = _norm_date(d)
            net = float(r['short_open_interest_top20']) - float(r['long_open_interest_top20'])
            out[ds] = round(net, 1)
    if out:
        _save_cache(out)
        return out, True, f'ok: 写入{len(out)}个交易日'
    return {}, False, '未找到全合约(IF)汇总行'


if __name__ == '__main__':
    if '--demo' in sys.argv:
        _demo_data()
    elif '--sync' in sys.argv:
        ser, ok, msg = sync_from_akshare()
        print('同步:', msg)
        for d, v in sorted(ser.items()):
            net = v
            print(f'  {d}  净空单={net:>10.0f} 手  ({net / 10000:.1f}万)  {"空头" if net > 0 else "多头"}主导')
    elif '--set' in sys.argv:
        for kv in sys.argv[sys.argv.index('--set') + 1:]:
            if ':' in kv:
                d, v = kv.split(':')
                _set_manual(d, float(v))
        print('cached now:', json.dumps(_load_cache(), ensure_ascii=False))
    elif '--show' in sys.argv:
        for d, v in load_position_series():
            print(d, v)
    else:
        print("用法: --sync(拉取真实IF净空单,需已装akshare) | --set 'YYYY-MM-DD:[手数]' | --demo | --show")