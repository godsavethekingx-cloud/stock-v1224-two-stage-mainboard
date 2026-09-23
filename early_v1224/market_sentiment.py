#!/usr/bin/env python3
"""
市场情绪指标模块 (Market Sentiment)
在选股前评估市场情绪, 动态调整仓位和选股数量

维度:
1. 涨停家数/跌停家数/炸板率
2. 连板高度/连板数量
3. 涨跌比
4. 成交量变化
5. 北向资金(如有)

用法:
  from market_sentiment import assess_market_sentiment
  sentiment = assess_market_sentiment(all_kls, index_kl, rt_data)
"""

from collections import defaultdict


def assess_market_sentiment(all_kls, index_kl, rt_data=None):
    """
    评估市场情绪
    
    返回: {
        'sentiment_score': 0-100 (越高越好),
        'sentiment_level': 'hot' | 'warm' | 'neutral' | 'cool' | 'cold',
        'zt_count': int,      # 涨停家数(估算)
        'dt_count': int,      # 跌停家数(估算)
        'up_down_ratio': float, # 涨跌比
        'recommend_position': '≤30%' | '≤50%' | '≤80%',
        'recommend_picks': int,  # 建议选股数量
    }
    """
    # 统计涨跌
    up_count = 0
    down_count = 0
    zt_count = 0
    dt_count = 0
    total_checked = 0
    
    for code, kl in all_kls.items():
        if len(kl['c']) < 2:
            continue
        total_checked += 1
        c = kl['c']
        pct = (c[-1] - c[-2]) / c[-2] * 100 if c[-2] > 0 else 0
        
        if pct > 0:
            up_count += 1
        elif pct < 0:
            down_count += 1
        
        if pct >= 9.5:
            zt_count += 1
        elif pct <= -9.5:
            dt_count += 1
    
    up_down_ratio = up_count / max(down_count, 1)
    
    # 评分
    score = 50  # 基础分
    
    # 涨停家数
    if zt_count >= 80: score += 20
    elif zt_count >= 50: score += 15
    elif zt_count >= 30: score += 10
    elif zt_count >= 15: score += 5
    elif zt_count < 10: score -= 10
    
    # 跌停家数
    if dt_count >= 20: score -= 15
    elif dt_count >= 10: score -= 10
    elif dt_count >= 5: score -= 5
    elif dt_count == 0: score += 5
    
    # 涨跌比
    if up_down_ratio >= 3: score += 10
    elif up_down_ratio >= 2: score += 5
    elif up_down_ratio >= 1.5: score += 3
    elif up_down_ratio < 0.5: score -= 15
    elif up_down_ratio < 0.8: score -= 8
    
    # 指数判断
    if index_kl and len(index_kl['c']) >= 5:
        idx_c = index_kl['c']
        idx_pct_1d = (idx_c[-1] - idx_c[-2]) / idx_c[-2] * 100 if idx_c[-2] > 0 else 0
        idx_pct_5d = (idx_c[-1] - idx_c[-6]) / idx_c[-6] * 100 if len(idx_c) >= 6 and idx_c[-6] > 0 else 0
        
        if idx_pct_1d > 1: score += 8
        elif idx_pct_1d > 0: score += 3
        elif idx_pct_1d < -2: score -= 10
        elif idx_pct_1d < -1: score -= 5
        
        if idx_pct_5d > 3: score += 5
        elif idx_pct_5d < -3: score -= 5
    
    # 确定情绪等级
    score = max(0, min(100, score))
    if score >= 75:
        level = 'hot'
        position = '≤80%'
        picks = 8
    elif score >= 60:
        level = 'warm'
        position = '≤70%'
        picks = 6
    elif score >= 40:
        level = 'neutral'
        position = '≤50%'
        picks = 6
    elif score >= 25:
        level = 'cool'
        position = '≤30%'
        picks = 4
    else:
        level = 'cold'
        position = '≤20%'
        picks = 3
    
    return {
        'sentiment_score': score,
        'sentiment_level': level,
        'zt_count': zt_count,
        'dt_count': dt_count,
        'up_down_ratio': round(up_down_ratio, 2),
        'up_count': up_count,
        'down_count': down_count,
        'recommend_position': position,
        'recommend_picks': picks,
    }


def print_sentiment(sentiment):
    """打印市场情绪"""
    level_emoji = {'hot': '🔥', 'warm': '☀️', 'neutral': '😐', 'cool': '❄️', 'cold': '🧊'}
    emoji = level_emoji.get(sentiment['sentiment_level'], '')
    
    print(f"\n  市场情绪: {emoji} {sentiment['sentiment_level'].upper()} ({sentiment['sentiment_score']}/100)")
    print(f"  涨停:{sentiment['zt_count']} | 跌停:{sentiment['dt_count']} | 涨跌比:{sentiment['up_down_ratio']:.2f}")
    print(f"  建议仓位: {sentiment['recommend_position']} | 建议选股: {sentiment['recommend_picks']}只")


if __name__ == '__main__':
    print("市场情绪模块加载成功")
    print("用法: from market_sentiment import assess_market_sentiment")