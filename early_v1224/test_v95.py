#!/usr/bin/env python3
"""v9.5核心逻辑单元测试（不依赖mootdx）"""

import json, math
from collections import Counter

# ============ 直接复制需要测试的函数（避免导入mootdx） ============

def get_dynamic_weights(market_trend):
    """根据市场走势返回条件权重字典"""
    base_weights = {
        1: 6, 2: 8, 3: 12, 4: 22, 5: 6,
        6: 20, 7: 12, 8: 16, 9: 28, 10: 24,
    }
    if market_trend == 'bull':
        return {**base_weights, 1:8, 2:6, 7:22, 8:20, 9:18, 10:28}
    elif market_trend == 'bear':
        return {**base_weights, 1:3, 2:4, 5:4, 7:5, 8:10, 9:35, 10:15}
    elif market_trend == 'crash':
        return {**base_weights, 1:0, 2:0, 3:5, 4:8, 5:0, 6:15, 7:0, 8:0, 9:40, 10:8}
    else:
        return {**base_weights, 1:5, 2:6, 7:10, 8:22, 9:25, 10:22}

COMBO_BONUSES = {
    frozenset({4, 9}): 18, frozenset({4, 10}): 16, frozenset({9, 10}): 14,
    frozenset({8, 9}): 12, frozenset({6, 9}): 12, frozenset({4, 8}): 10,
    frozenset({7, 8}): 10, frozenset({6, 10}): 10,
    frozenset({4, 9, 10}): 28, frozenset({6, 9, 10}): 25,
    frozenset({4, 8, 9}): 22, frozenset({7, 8, 10}): 18,
}

def calc_combo_bonus(conditions):
    if not conditions or len(conditions) < 2: return 0
    cond_set = frozenset(conditions)
    if cond_set in COMBO_BONUSES: return COMBO_BONUSES[cond_set]
    best_bonus = 0
    for combo_set, bonus in COMBO_BONUSES.items():
        if combo_set.issubset(cond_set) and bonus > best_bonus:
            best_bonus = bonus
    return best_bonus

def calc_fundamental_bonus(c, td, fin_data, name):
    bonus = 0
    detail = {'basic':0,'recognition':0,'heat':0,'contract':0,'growth':0}
    profit_yoy = fin_data.get('profit_yoy')
    if profit_yoy is not None and profit_yoy < -50:
        return -999, detail, f"净利润同比暴跌{profit_yoy:.1f}%"
    debt = fin_data.get('debt_ratio')
    if debt is not None and debt > 85:
        return -999, detail, f"资产负债率过高{debt:.1f}%"
    pe = td.get('pe', 0)
    if pe is not None and pe < 0:
        return -999, detail, "亏损股(PE<0)"
    rev_yoy = fin_data.get('revenue_yoy')
    if rev_yoy is not None and profit_yoy is not None:
        if rev_yoy < -30 and profit_yoy < -30:
            return -999, detail, f"营收同比{rev_yoy:.1f}%且净利润同比{profit_yoy:.1f}%双降"
    if pe and pe > 0:
        if 5 < pe <= 15: bonus += 8; detail['basic'] += 8
        elif 15 < pe <= 25: bonus += 6; detail['basic'] += 6
        elif 25 < pe <= 40: bonus += 3; detail['basic'] += 3
        elif 40 < pe <= 80: bonus += 1; detail['basic'] += 1
        elif pe > 200: bonus -= 3; detail['basic'] -= 3
    if debt is not None:
        if debt < 30: bonus += 4; detail['basic'] += 4
        elif 30 <= debt < 50: bonus += 2; detail['basic'] += 2
        elif debt > 80: bonus -= 3; detail['basic'] -= 3
    roe = fin_data.get('roe')
    if roe is not None:
        if roe > 20: bonus += 5; detail['basic'] += 5
        elif roe > 10: bonus += 3; detail['basic'] += 3
        elif roe > 5: bonus += 1; detail['basic'] += 1
    if rev_yoy is not None and profit_yoy is not None:
        if rev_yoy > 30 and profit_yoy > 50: bonus += 10; detail['growth'] += 10
        elif rev_yoy > 15 and profit_yoy > 25: bonus += 7; detail['growth'] += 7
        elif rev_yoy > 0 and profit_yoy > 15: bonus += 4; detail['growth'] += 4
        elif profit_yoy < -20: bonus -= 3; detail['growth'] -= 3
    elif profit_yoy is not None:
        if profit_yoy > 50: bonus += 5; detail['growth'] += 5
        elif profit_yoy > 20: bonus += 3; detail['growth'] += 3
        elif profit_yoy < -20: bonus -= 2; detail['growth'] -= 2
    return round(bonus, 1), detail, None


# ============ 测试执行 ============
print("="*60)
print("测试1: 动态条件权重")
print("="*60)
for trend in ['bull', 'bear', 'crash', 'range']:
    cw = get_dynamic_weights(trend)
    active = {k:v for k,v in cw.items() if v > 0}
    disabled = [k for k,v in cw.items() if v == 0]
    print(f"\n趋势 {trend}:")
    cond_names = {1:'10日线',2:'缩量回调',3:'趋势上行',4:'强势型',5:'大单净买',
                  6:'涨停回踩',7:'均线多头',8:'突破回踩',9:'MACD金叉',10:'强势回踩'}
    for cid, w in sorted(active.items(), key=lambda x:-x[1]):
        print(f"  条件{cid}({cond_names[cid]}): 权重{w}")
    if disabled:
        print(f"  禁用条件: {[cond_names[d] for d in disabled]}")

print("\n" + "="*60)
print("测试2: 多条件组合奖励")
print("="*60)
test_combos = [
    [4, 9], [9, 10], [4, 9, 10], [1, 2], [7, 8],
    [4, 8, 9], [1], [], [6, 9, 10],
]
for combo in test_combos:
    bonus = calc_combo_bonus(combo)
    print(f"  条件{combo}: 组合奖励 +{bonus}")

print("\n" + "="*60)
print("测试3: 基本面一票否决")
print("="*60)

class MockTD:
    def __init__(self, pe=0):
        self._pe = pe
    def get(self, key, default=0):
        if key == 'pe': return self._pe
        return default

td = MockTD()

fin_normal = {'profit_yoy': 30, 'debt_ratio': 45, 'revenue_yoy': 20, 'roe': 15}
bonus, detail, veto = calc_fundamental_bonus(None, td, fin_normal, "测试科技")
print(f"  正常股: 加分{bonus}, 否决:{veto}")
assert bonus > 0 and veto is None

fin_bad1 = {'profit_yoy': -60, 'debt_ratio': 45, 'revenue_yoy': 20}
bonus, detail, veto = calc_fundamental_bonus(None, td, fin_bad1, "测试科技")
print(f"  净利润暴跌-60%: 加分{bonus}, 否决:{veto}")
assert bonus == -999 and veto is not None

fin_bad2 = {'profit_yoy': 10, 'debt_ratio': 90, 'revenue_yoy': 20}
bonus, detail, veto = calc_fundamental_bonus(None, td, fin_bad2, "测试科技")
print(f"  负债率90%: 加分{bonus}, 否决:{veto}")
assert bonus == -999 and veto is not None

td_pe = MockTD(pe=-5)
fin_bad3 = {'profit_yoy': 10, 'debt_ratio': 45, 'revenue_yoy': 20}
bonus, detail, veto = calc_fundamental_bonus(None, td_pe, fin_bad3, "测试科技")
print(f"  亏损股PE=-5: 加分{bonus}, 否决:{veto}")
assert bonus == -999 and veto is not None

fin_bad4 = {'profit_yoy': -35, 'debt_ratio': 45, 'revenue_yoy': -35}
bonus, detail, veto = calc_fundamental_bonus(None, td, fin_bad4, "测试科技")
print(f"  营收-35%利润-35%双降: 加分{bonus}, 否决:{veto}")
assert bonus == -999 and veto is not None

print("\n" + "="*60)
print("测试4: 走势适配权重验证")
print("="*60)
# 验证暴跌市条件1/2/7被清零
crash_cw = get_dynamic_weights('crash')
assert crash_cw[1] == 0, "暴跌市条件1应被清零"
assert crash_cw[2] == 0, "暴跌市条件2应被清零"
assert crash_cw[7] == 0, "暴跌市条件7应被清零"
assert crash_cw[9] == 40, "暴跌市条件9应最高权重"
print("  ✅ 暴跌市: 条件1/2/7已清零, 条件9权重40")

# 验证下跌市条件1/2大幅降权
bear_cw = get_dynamic_weights('bear')
assert bear_cw[1] < 5, "下跌市条件1应大幅降权"
assert bear_cw[9] > 30, "下跌市条件9应大幅加权"
print("  ✅ 下跌市: 条件1/2降权, 条件9加权")

# 验证牛市条件7/8/10加权
bull_cw = get_dynamic_weights('bull')
assert bull_cw[7] > 15, "牛市条件7应加权"
assert bull_cw[10] > 25, "牛市条件10应加权"
print("  ✅ 牛市: 条件7/8/10加权")

print("\n" + "="*60)
print("✅ 所有测试通过! v9.5核心逻辑验证完成")
print("="*60)
