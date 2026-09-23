#!/usr/bin/env python3
"""
板块轮动检测模块 (Sector Rotation Detector)
在选股前识别当日最强板块, 在领涨板块内优先选股

核心功能:
1. 检测T-1日最强板块 (基于涨停股分布)
2. 检测板块资金流入/流出
3. 板块温度评分 (涨停数+异动数+资金流向)
4. 返回优先选股板块列表

用法:
  from sector_rotation import detect_hot_sectors_v2, score_sector_heat
  hot_sectors = detect_hot_sectors_v2(all_tech_data, all_kls)
"""

from collections import defaultdict
import math


def detect_hot_sectors_v2(all_scored, match_sector_fn, all_kls=None):
    """
    V2版热点板块检测: 基于涨停股分布+异动密度+候选池集中度
    
    参数:
        all_scored: V6候选池 [{code, name, tech, v6_score, ...}]
        match_sector_fn: 板块匹配函数 match_sector(name, code)
        all_kls: dict 全量K线数据 (可选, 用于计算板块平均涨幅)
    
    返回: {
        'hot_sectors': {sector: {heat, zt_count, anomaly_count, avg_score, ...}},
        'top_sectors': [(sector, heat), ...],
        'dominant_sector': 'sector_name',
        'is_concentrated': bool,  # 涨停是否集中在少数板块
    }
    """
    # 统计每个板块的指标
    sector_stats = defaultdict(lambda: {
        'count': 0,           # 候选池内数量
        'zt_count': 0,        # 30日内涨停≥2的
        'zt_high': 0,         # 30日内涨停≥3的
        'anomaly': 0,         # 异动(涨停≥2)
        'total_score': 0,     # V6总分
        'avg_score': 0,       # 平均分
        'dev10_sum': 0,       # MA10偏离总和
        'rsi_sum': 0,         # RSI总和
        'codes': [],          # 股票代码
    })
    
    for s in all_scored[:500]:  # Top500候选
        sectors = match_sector_fn(s['name'], s['code'])
        tech = s.get('tech', {})
        zt30 = tech.get('zt_30d', 0)
        score = s.get('v6_score', s.get('c_score', 0))
        
        for sec in sectors:
            st = sector_stats[sec]
            st['count'] += 1
            st['total_score'] += score
            st['codes'].append(s['code'])
            st['dev10_sum'] += tech.get('dev_ma10', 0)
            st['rsi_sum'] += tech.get('rsi6', 50)
            if zt30 >= 2:
                st['anomaly'] += 1
            if zt30 >= 2:
                st['zt_count'] += 1
            if zt30 >= 3:
                st['zt_high'] += 1
    
    # 计算平均分
    for sec, st in sector_stats.items():
        if st['count'] > 0:
            st['avg_score'] = st['total_score'] / st['count']
            st['avg_dev10'] = st['dev10_sum'] / st['count']
            st['avg_rsi'] = st['rsi_sum'] / st['count']
    
    # 计算板块热度 (heat score)
    for sec, st in sector_stats.items():
        # 热度 = 异动密度*3 + 涨停密度*5 + 候选密度*2 + 平均分/10
        anomaly_density = st['anomaly'] / max(st['count'], 1) * 100
        zt_density = st['zt_high'] / max(st['count'], 1) * 100
        count_density = st['count'] / max(len(all_scored[:500]), 1) * 100
        
        st['heat'] = (
            anomaly_density * 3.0 +
            zt_density * 5.0 +
            count_density * 2.0 +
            st['avg_score'] / 10.0
        )
        st['anomaly_density'] = anomaly_density
        st['zt_density'] = zt_density
    
    # 排序: 按热度降序
    sorted_sectors = sorted(sector_stats.items(), key=lambda x: -x[1]['heat'])
    
    # 取热度>0的板块
    hot_sectors = {sec: dict(stats) for sec, stats in sorted_sectors if stats['heat'] > 0}
    
    # 判断涨停是否集中
    total_anomaly = sum(st['anomaly'] for st in sector_stats.values())
    top3_anomaly = sum(st['anomaly'] for _, st in sorted_sectors[:3])
    is_concentrated = total_anomaly > 0 and top3_anomaly / total_anomaly > 0.4
    
    # 主导板块
    dominant = sorted_sectors[0][0] if sorted_sectors and sorted_sectors[0][1]['heat'] > 5 else None
    
    return {
        'hot_sectors': hot_sectors,
        'top_sectors': [(sec, st['heat']) for sec, st in sorted_sectors[:10]],
        'dominant_sector': dominant,
        'is_concentrated': is_concentrated,
        'total_sectors': len(sector_stats),
        'active_sectors': len([s for s in sorted_sectors if s[1]['heat'] > 0]),
    }


def score_sector_heat(sector_name, hot_sectors_result):
    """
    给单个板块评分 (0-100)
    用于个股评分时的板块加成
    """
    hs = hot_sectors_result.get('hot_sectors', {})
    if sector_name in hs:
        heat = hs[sector_name]['heat']
        # 归一化到0-100
        max_heat = max(s['heat'] for s in hs.values()) if hs else 1
        return min(100, heat / max_heat * 100)
    return 0


def get_sector_weight_multiplier(sector_name, hot_sectors_result):
    """
    获取板块权重乘数
    用于通道B接力评分时的板块加成
    
    返回: 1.0 ~ 1.5
    """
    hs = hot_sectors_result.get('hot_sectors', {})
    if sector_name in hs:
        heat = hs[sector_name]['heat']
        anomaly = hs[sector_name].get('zt_count', 0)
        zt_high = hs[sector_name].get('zt_high', 0)
        
        # 根据热度分级
        if zt_high >= 3:
            return 1.5  # 最高级: 板块涨停潮
        elif anomaly >= 3:
            return 1.4  # 高级: 板块异动明显
        elif anomaly >= 2:
            return 1.3  # 中级: 有板块效应
        elif heat > 10:
            return 1.2  # 低级: 热度较高
        elif heat > 5:
            return 1.1  # 微弱: 有一定热度
    return 1.0


def detect_sector_rotation_trend(all_scored, match_sector_fn, prev_hot_sectors=None):
    """
    检测板块轮动趋势: 比较当前热点vs历史热点
    
    返回: {
        'emerging': [...],   # 新兴板块 (之前不热, 现在热)
        'fading': [...],     # 衰退板块 (之前热, 现在不热)
        'sustaining': [...], # 持续热点
        'rotation_signal': 'emerging' | 'fading' | 'sustaining' | 'scattered',
    }
    """
    current = detect_hot_sectors_v2(all_scored, match_sector_fn)
    current_hot = set(current['hot_sectors'].keys())
    
    if not prev_hot_sectors:
        return {
            'emerging': list(current_hot)[:5],
            'fading': [],
            'sustaining': [],
            'rotation_signal': 'scattered',
        }
    
    prev_hot = set(prev_hot_sectors.keys())
    
    emerging = list(current_hot - prev_hot)[:5]
    fading = list(prev_hot - current_hot)[:5]
    sustaining = list(current_hot & prev_hot)[:5]
    
    if len(emerging) >= 3 and len(sustaining) <= 2:
        signal = 'emerging'  # 新板块崛起
    elif len(fading) >= 3 and len(sustaining) <= 2:
        signal = 'fading'    # 旧热点退潮
    elif len(sustaining) >= 3:
        signal = 'sustaining' # 热点持续
    else:
        signal = 'scattered'  # 分散, 无明确主线
    
    return {
        'emerging': emerging,
        'fading': fading,
        'sustaining': sustaining,
        'rotation_signal': signal,
    }


def print_sector_heatmap(hot_sectors_result, top_n=10):
    """打印板块热度图"""
    top = hot_sectors_result['top_sectors'][:top_n]
    if not top:
        print("  无热点板块")
        return
    
    print(f"\n  {'板块':<16} {'热度':>6} {'候选':>5} {'异动':>5} {'涨停':>5} {'平均分':>7}")
    print(f"  {'─'*50}")
    for sec, heat in top:
        st = hot_sectors_result['hot_sectors'][sec]
        print(f"  {sec:<16} {heat:>6.1f} {st['count']:>5} {st['anomaly']:>5} {st['zt_high']:>5} {st['avg_score']:>7.1f}")


if __name__ == '__main__':
    # 测试
    print("板块轮动检测模块加载成功")
    print("用法: from sector_rotation import detect_hot_sectors_v2, get_sector_weight_multiplier")