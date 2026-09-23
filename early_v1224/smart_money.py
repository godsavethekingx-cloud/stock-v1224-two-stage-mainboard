#!/usr/bin/env python3
"""
主力资金动向检测模块 (Smart Money Detector)
V9新增: 锁定主力建仓板块 + 放量异动 + 回调到位提醒

核心功能:
1. 板块级别放量异动检测 (3倍以上放量)
2. 个股突发放量 (3倍+ vs 20日均量)
3. 主力建仓形态识别 (震荡向上→急跌→止跌)
4. 回调到位检测 (回踩MA10/MA20/黄金分割)
5. 用户关注板块专项监控 (家电/消费/中百等)

用法:
  from smart_money import SmartMoneyDetector
  detector = SmartMoneyDetector(all_kls, match_sector)
  results = detector.detect_all()
"""

import math
from collections import defaultdict
from datetime import datetime, timedelta


class SmartMoneyDetector:
    """主力资金检测器"""
    
    def __init__(self, all_kls, match_sector_fn, index_kl=None):
        """
        参数:
            all_kls: dict {code: {c, v, o, h, l, name, code}}
            match_sector_fn: 板块匹配函数 match_sector(name, code) -> [sector...]
            index_kl: 指数K线(可选)
        """
        self.all_kls = all_kls
        self.match_sector = match_sector_fn
        self.index_kl = index_kl
    
    # ================================================================
    # 1. 板块级别放量异动检测
    # ================================================================
    def detect_sector_volume_spike(self, min_vol_ratio=3.0, min_stocks=3):
        """
        检测板块整体放量: 板块内个股平均量比 > min_vol_ratio
        
        返回: [
            {sector, vol_ratio, stock_count, spike_count, 
             avg_vol_ratio, top_stocks, signal_strength}
        ]
        """
        # 按板块聚合个股量比
        sector_vols = defaultdict(list)
        sector_stocks = defaultdict(list)
        
        for code, kl in self.all_kls.items():
            c = kl['c']; v = kl['v']; n = len(c)
            if n < 25: continue
            
            # 计算个股量比: 今日量 / 20日均量
            today_vol = v[n-1]
            vol_20d = sum(v[max(0,n-21):n-1]) / min(20, n-1)
            if vol_20d <= 0: continue
            vol_ratio = today_vol / vol_20d
            
            # 今日成交额
            turnover = today_vol * c[n-1]
            if turnover < 5000: continue  # 过滤无量股
            
            # 今日涨跌幅
            if n >= 2 and c[n-2] > 0:
                pct = (c[n-1] / c[n-2] - 1) * 100
            else:
                pct = 0
            
            name = kl.get('name', code)
            sectors = self.match_sector(name, code)
            
            for sec in sectors:
                sector_vols[sec].append({
                    'code': code, 'name': name,
                    'vol_ratio': round(vol_ratio, 2),
                    'pct': round(pct, 2),
                    'turnover': turnover,
                })
                sector_stocks[sec].append(code)
        
        # 分析每个板块
        results = []
        for sec, stocks in sector_vols.items():
            total = len(stocks)
            if total < min_stocks: continue
            
            # 放量个股: 量比 > 阈值
            spikes = [s for s in stocks if s['vol_ratio'] >= min_vol_ratio]
            spike_count = len(spikes)
            spike_ratio = spike_count / total * 100
            
            if spike_count < 2: continue  # 至少2只放量
            
            # 平均量比
            avg_vr = sum(s['vol_ratio'] for s in stocks) / total
            
            # 放量股平均涨幅
            avg_pct = sum(s['pct'] for s in spikes) / len(spikes) if spikes else 0
            
            # 板块成交额
            total_turnover = sum(s['turnover'] for s in stocks)
            
            # 信号强度: 综合放量比+放量占比+涨幅
            signal_strength = (
                avg_vr * 5 +
                spike_ratio * 0.5 +
                abs(avg_pct) * 2 +
                min(total_turnover / 1e8, 10)  # 成交额上限10分
            )
            
            # 排序: 放量股中量比最大的
            top_spikes = sorted(spikes, key=lambda x: -x['vol_ratio'])[:5]
            
            results.append({
                'sector': sec,
                'total_stocks': total,
                'spike_count': spike_count,
                'spike_ratio': round(spike_ratio, 1),
                'avg_vol_ratio': round(avg_vr, 2),
                'avg_pct': round(avg_pct, 2),
                'total_turnover': total_turnover,
                'signal_strength': round(signal_strength, 1),
                'top_spikes': top_spikes,
                'direction': 'bullish' if avg_pct > 0 else 'accumulation',
            })
        
        results.sort(key=lambda x: -x['signal_strength'])
        return results
    
    # ================================================================
    # 2. 个股突发放量检测
    # ================================================================
    def detect_stock_volume_anomaly(self, min_vol_ratio=3.0, lookback=20):
        """
        检测个股突发放量 (3倍20日均量)
        
        返回: [{code, name, vol_ratio, pct, sector, anomaly_type, ...}]
        """
        results = []
        
        for code, kl in self.all_kls.items():
            c = kl['c']; v = kl['v']; o = kl['o']; h = kl['h']; l = kl['l']
            n = len(c)
            if n < lookback + 5: continue
            
            # T-1日 (最近完整交易日)
            ti = n - 1
            tc = c[ti]; tv = v[ti]; to = o[ti]; th = h[ti]; tl = l[ti]
            
            # 成交额过滤
            if tv * tc < 5000: continue
            
            # 20日均量
            idx_start = max(0, ti - lookback)
            vol_20d = sum(v[idx_start:ti]) / max(1, ti - idx_start)
            if vol_20d <= 0: continue
            vol_ratio = tv / vol_20d
            
            if vol_ratio < min_vol_ratio: continue
            
            # 涨跌幅
            if ti >= 1 and c[ti-1] > 0:
                pct = (tc / c[ti-1] - 1) * 100
            else:
                pct = 0
            
            # 振幅
            amp = (th - tl) / c[ti-1] * 100 if ti >= 1 and c[ti-1] > 0 else 0
            
            # 判断异常类型
            is_yang = tc >= to
            has_long_lower = min(to, tc) - tl > abs(tc - to) * 1.5
            
            if pct > 5 and is_yang:
                anomaly_type = '放量拉升'
            elif pct < -3:
                anomaly_type = '放量砸盘'
            elif has_long_lower:
                anomaly_type = '放量长下影(吸筹)'
            elif abs(pct) < 2 and amp > 4:
                anomaly_type = '放量洗盘(高振幅)'
            elif abs(pct) < 2:
                anomaly_type = '放量滞涨(换手)'
            else:
                anomaly_type = '放量异动'
            
            name = kl.get('name', code)
            sectors = self.match_sector(name, code)
            
            # 最近5日量比趋势
            vol_trend = []
            for j in range(max(0, ti-4), ti+1):
                if j > 0:
                    vj_ratio = v[j] / vol_20d if vol_20d > 0 else 1
                    vol_trend.append(round(vj_ratio, 2))
            
            # 5日涨幅
            pct_5d = (tc / c[ti-5] - 1) * 100 if ti >= 5 and c[ti-5] > 0 else 0
            
            results.append({
                'code': code,
                'name': name,
                'vol_ratio': round(vol_ratio, 2),
                'pct': round(pct, 2),
                'amp': round(amp, 2),
                'anomaly_type': anomaly_type,
                'sectors': sectors[:5],
                'turnover': tv * tc,
                'vol_trend': vol_trend,
                'pct_5d': round(pct_5d, 2),
                'is_yang': is_yang,
                'has_long_lower': has_long_lower,
            })
        
        results.sort(key=lambda x: -x['vol_ratio'])
        return results
    
    # ================================================================
    # 3. 主力建仓形态识别 (震荡向上→急跌→止跌)
    # ================================================================
    def detect_accumulation_pattern(self):
        """
        检测主力建仓形态:
        
        模式A: 震荡向上后急跌 (如中百集团)
        - 前10-20日: 震荡上行(涨幅10-30%)
        - 近3-5日: 连续急跌(跌幅>10%)
        - 今日: 缩量止跌(量比<0.8, 小阴小阳)
        
        模式B: 横盘建仓后突破
        - 前20日: 窄幅震荡(振幅<15%)
        - 近3日: 温和放量(量比1.2-2.0)
        - 今日: 突破MA20或新高
        
        返回: [{code, name, pattern, score, ...}]
        """
        results = []
        
        for code, kl in self.all_kls.items():
            c = kl['c']; v = kl['v']; o = kl['o']; h = kl['h']; l = kl['l']
            n = len(c)
            if n < 30: continue
            
            ti = n - 1
            tc = c[ti]; tv = v[ti]
            
            # 成交额
            if tv * tc < 5000: continue
            
            # 计算各阶段
            # 近5日 (急跌/止跌阶段)
            if ti < 5 or c[ti-5] <= 0: continue
            pct_5d = (tc / c[ti-5] - 1) * 100
            
            # 前10-5日 (蓄势阶段)
            if ti < 10 or c[ti-10] <= 0: continue
            pct_10_5 = (c[ti-5] / c[ti-10] - 1) * 100
            
            # 前20-10日 (建仓阶段)
            if ti < 20 or c[ti-20] <= 0: continue
            pct_20_10 = (c[ti-10] / c[ti-20] - 1) * 100
            
            # 20日整体涨幅
            pct_20d = (tc / c[ti-20] - 1) * 100
            
            # 量比
            vol_5d = sum(v[max(0,ti-5):ti]) / min(5, ti)
            vol_10d = sum(v[max(0,ti-10):ti]) / min(10, ti)
            vol_20d = sum(v[max(0,ti-20):ti]) / min(20, ti)
            vol_ratio = tv / vol_20d if vol_20d > 0 else 1
            
            # MA
            ma5 = sum(c[max(0,ti-4):ti+1]) / min(5, ti+1)
            ma10 = sum(c[max(0,ti-9):ti+1]) / min(10, ti+1)
            ma20 = sum(c[max(0,ti-19):ti+1]) / min(20, ti+1)
            dev_ma10 = (tc / ma10 - 1) * 100 if ma10 > 0 else 0
            dev_ma20 = (tc / ma20 - 1) * 100 if ma20 > 0 else 0
            
            # 近5日每天涨跌
            daily_pcts = []
            for j in range(max(0,ti-4), ti+1):
                if j > 0 and c[j-1] > 0:
                    daily_pcts.append((c[j] / c[j-1] - 1) * 100)
            
            # 连续下跌天数
            consec_down = 0
            for j in range(ti, max(0,ti-10), -1):
                if j > 0 and c[j-1] > 0 and c[j] < c[j-1]:
                    consec_down += 1
                else:
                    break
            
            name = kl.get('name', code)
            sectors = self.match_sector(name, code)
            
            # ===== 模式A: 震荡向上后急跌止跌 =====
            pattern_a_score = 0
            pattern_a_signals = []
            
            # 前20-10日: 震荡上行 (涨幅5-30%)
            if 5 <= pct_20_10 <= 40:
                pattern_a_score += 20
                pattern_a_signals.append(f'前段建仓{pct_20_10:+.1f}%')
            
            # 前10-5日: 继续上行 (涨幅3-20%)
            if 3 <= pct_10_5 <= 25:
                pattern_a_score += 15
                pattern_a_signals.append(f'中段蓄势{pct_10_5:+.1f}%')
            
            # 近5日: 急跌 (跌幅>5%)
            if pct_5d < -5:
                pattern_a_score += 25
                pattern_a_signals.append(f'急跌{pct_5d:+.1f}%')
            
            # 连续下跌2-5天
            if 2 <= consec_down <= 5:
                pattern_a_score += 15
                pattern_a_signals.append(f'连跌{consec_down}天')
            
            # 今日缩量止跌 (量比0.4-0.85, 小阴小阳)
            if 0.4 <= vol_ratio <= 0.85 and abs(pct_5d) < 8:
                pattern_a_score += 20
                pattern_a_signals.append(f'缩量止跌(量比{vol_ratio:.2f})')
            
            # 回踩MA10或MA20支撑
            if -3 <= dev_ma10 <= 3:
                pattern_a_score += 10
                pattern_a_signals.append(f'回踩MA10')
            elif -3 <= dev_ma20 <= 3:
                pattern_a_score += 8
                pattern_a_signals.append(f'回踩MA20')
            
            # 今日收阳(止跌信号)
            if tc >= o[ti]:
                pattern_a_score += 5
                pattern_a_signals.append('今日收阳')
            
            # 振幅收窄(止跌特征)
            if ti >= 1 and c[ti-1] > 0:
                today_amp = (h[ti] - l[ti]) / c[ti-1] * 100
                if today_amp < 3:
                    pattern_a_score += 5
                    pattern_a_signals.append('振幅收窄')
            
            # 20日整体涨幅为正(趋势未破)
            if pct_20d > 0:
                pattern_a_score += 5
            
            if pattern_a_score >= 50:
                pattern_a_signals.insert(0, f'得分{pattern_a_score}')
                results.append({
                    'code': code, 'name': name,
                    'pattern': 'A_急跌止跌',
                    'score': pattern_a_score,
                    'signals': pattern_a_signals,
                    'sectors': sectors[:5],
                    'ma5': round(ma5, 2),
                    'ma10': round(ma10, 2),
                    'ma20': round(ma20, 2),
                    'pct_5d': round(pct_5d, 2),
                    'pct_20d': round(pct_20d, 2),
                    'vol_ratio': round(vol_ratio, 2),
                    'dev_ma10': round(dev_ma10, 2),
                    'consec_down': consec_down,
                    'daily_pcts': [round(x, 2) for x in daily_pcts],
                })
            
            # ===== 模式B: 横盘建仓后突破 =====
            pattern_b_score = 0
            pattern_b_signals = []
            
            # 前20日: 窄幅震荡 (振幅<15%)
            high_20d = max(h[max(0,ti-19):ti+1])
            low_20d = min(l[max(0,ti-19):ti+1])
            amp_20d = (high_20d - low_20d) / low_20d * 100 if low_20d > 0 else 100
            
            if amp_20d < 18:
                pattern_b_score += 25
                pattern_b_signals.append(f'窄幅横盘{amp_20d:.1f}%')
            
            # 近3日温和放量
            vol_3d = sum(v[max(0,ti-2):ti+1]) / min(3, ti+1)
            vol_ratio_3d = vol_3d / vol_20d if vol_20d > 0 else 1
            if 1.2 <= vol_ratio_3d <= 2.5:
                pattern_b_score += 20
                pattern_b_signals.append(f'温和放量({vol_ratio_3d:.1f}x)')
            
            # 突破MA20
            if dev_ma20 > 0 and dev_ma20 < 5:
                pattern_b_score += 15
                pattern_b_signals.append(f'突破MA20')
            
            # 5日涨幅适中
            if 3 <= pct_5d <= 15:
                pattern_b_score += 15
                pattern_b_signals.append(f'突破启动{pct_5d:+.1f}%')
            
            # 今日放量(非巨量)
            if 1.5 <= vol_ratio <= 3.0:
                pattern_b_score += 10
                pattern_b_signals.append(f'今日放量{vol_ratio:.1f}x')
            
            if pattern_b_score >= 45:
                pattern_b_signals.insert(0, f'得分{pattern_b_score}')
                results.append({
                    'code': code, 'name': name,
                    'pattern': 'B_横盘突破',
                    'score': pattern_b_score,
                    'signals': pattern_b_signals,
                    'sectors': sectors[:5],
                    'pct_5d': round(pct_5d, 2),
                    'pct_20d': round(pct_20d, 2),
                    'vol_ratio': round(vol_ratio, 2),
                    'dev_ma10': round(dev_ma10, 2),
                    'consec_down': consec_down,
                    'daily_pcts': [round(x, 2) for x in daily_pcts],
                })
        
        results.sort(key=lambda x: -x['score'])
        return results
    
    # ================================================================
    # 4. 回调到位检测
    # ================================================================
    def detect_pullback_setup(self):
        """
        检测回调到位即将反弹的个股
        
        条件:
        - 前期有上升趋势(20日涨幅>5%)
        - 近5日回调(跌幅>3%)
        - 回踩关键均线(MA10/MA20/MA60)
        - 缩量(量比<0.8)
        - 今日止跌(小阳线或长下影)
        
        返回: [{code, name, pullback_pct, support_level, ...}]
        """
        results = []
        
        for code, kl in self.all_kls.items():
            c = kl['c']; v = kl['v']; o = kl['o']; h = kl['h']; l = kl['l']
            n = len(c)
            if n < 30: continue
            
            ti = n - 1
            tc = c[ti]; tv = v[ti]; to = o[ti]; th = h[ti]; tl = l[ti]
            
            if tv * tc < 5000: continue
            
            # 20日涨幅
            if ti < 20 or c[ti-20] <= 0: continue
            pct_20d = (tc / c[ti-20] - 1) * 100
            
            # 前15日高点 (回调起点)
            high_15d_idx = ti - 1
            high_15d_val = c[ti-1]
            for j in range(ti-15, ti):
                if h[j] > high_15d_val:
                    high_15d_val = h[j]
                    high_15d_idx = j
            
            # 回调幅度
            pullback_pct = (tc / high_15d_val - 1) * 100 if high_15d_val > 0 else 0
            
            # 必须回调>3%
            if pullback_pct > -3:
                continue
            
            # 5日涨跌幅
            if ti < 5 or c[ti-5] <= 0: continue
            pct_5d = (tc / c[ti-5] - 1) * 100
            
            # 量比
            vol_20d = sum(v[max(0,ti-20):ti]) / min(20, ti)
            vol_ratio = tv / vol_20d if vol_20d > 0 else 1
            
            # 均线
            ma5 = sum(c[max(0,ti-4):ti+1]) / min(5, ti+1)
            ma10 = sum(c[max(0,ti-9):ti+1]) / min(10, ti+1)
            ma20 = sum(c[max(0,ti-19):ti+1]) / min(20, ti+1)
            ma60 = sum(c[max(0,ti-59):ti+1]) / min(60, ti+1) if ti >= 59 else ma20
            
            dev_ma5 = (tc / ma5 - 1) * 100 if ma5 > 0 else 0
            dev_ma10 = (tc / ma10 - 1) * 100 if ma10 > 0 else 0
            dev_ma20 = (tc / ma20 - 1) * 100 if ma20 > 0 else 0
            
            # 振幅
            amp = (th - tl) / c[ti-1] * 100 if ti >= 1 and c[ti-1] > 0 else 0
            
            name = kl.get('name', code)
            sectors = self.match_sector(name, code)
            
            score = 0
            signals = []
            
            # 前期上升趋势
            if pct_20d > 5:
                score += 15
                signals.append(f'前期上升{pct_20d:+.1f}%')
            elif pct_20d > 0:
                score += 8
            
            # 回调幅度
            if -15 <= pullback_pct <= -5:
                score += 20
                signals.append(f'回调{pullback_pct:+.1f}%')
            elif -5 < pullback_pct <= -3:
                score += 12
                signals.append(f'浅回调{pullback_pct:+.1f}%')
            elif pullback_pct < -15:
                score += 5  # 回调太深
                signals.append(f'深回调{pullback_pct:+.1f}%')
            
            # 回踩支撑
            support_level = 'none'
            if -2 <= dev_ma10 <= 2:
                score += 20
                support_level = 'MA10'
                signals.append('回踩MA10')
            elif -2 <= dev_ma20 <= 2:
                score += 15
                support_level = 'MA20'
                signals.append('回踩MA20')
            elif ma60 > 0 and -2 <= (tc/ma60-1)*100 <= 2:
                score += 10
                support_level = 'MA60'
                signals.append('回踩MA60')
            
            # 缩量
            if vol_ratio < 0.7:
                score += 15
                signals.append(f'缩量{vol_ratio:.2f}x')
            elif vol_ratio < 0.85:
                score += 10
                signals.append(f'缩量{vol_ratio:.2f}x')
            
            # 止跌信号
            is_yang = tc >= to
            has_lower = (min(to, tc) - tl) > abs(tc - to) * 1.5
            
            if is_yang:
                score += 8
                signals.append('收阳止跌')
            if has_lower:
                score += 8
                signals.append('长下影')
            if amp < 2.5:
                score += 5
                signals.append('振幅收窄')
            
            # 连续下跌即将结束
            consec_down = 0
            for j in range(ti, max(0,ti-10), -1):
                if j > 0 and c[j-1] > 0 and c[j] < c[j-1]:
                    consec_down += 1
                else:
                    break
            if 2 <= consec_down <= 4:
                score += 5
                signals.append(f'连跌{consec_down}天尾声')
            
            if score >= 40:
                signals.insert(0, f'得分{score}')
                results.append({
                    'code': code, 'name': name,
                    'score': score,
                    'signals': signals,
                    'sectors': sectors[:5],
                    'ma5': round(ma5, 2),
                    'ma10': round(ma10, 2),
                    'ma20': round(ma20, 2),
                    'pullback_pct': round(pullback_pct, 2),
                    'pct_5d': round(pct_5d, 2),
                    'pct_20d': round(pct_20d, 2),
                    'vol_ratio': round(vol_ratio, 2),
                    'support_level': support_level,
                    'dev_ma10': round(dev_ma10, 2),
                    'dev_ma20': round(dev_ma20, 2),
                    'consec_down': consec_down,
                    'is_yang': is_yang,
                    'has_lower': has_lower,
                })
        
        results.sort(key=lambda x: -x['score'])
        return results
    
    # ================================================================
    # 5. 用户关注板块专项监控
    # ================================================================
    def monitor_watch_sectors(self, watch_sectors=None):
        """
        监控用户指定板块
        
        参数:
            watch_sectors: list 如 ['家电', '消费', '新零售', '社区团购', '电商概念']
        
        返回: {sector: {status, stocks, signals, ...}}
        """
        if watch_sectors is None:
            watch_sectors = ['家电', '家用电器', '电商概念', '新零售', '社区团购',
                           '商业百货', '食品饮料', '酿酒行业', '预制菜', '猪肉概念']
        
        results = {}
        
        for target_sec in watch_sectors:
            sec_stocks = []
            sec_vol_ratios = []
            sec_pcts = []
            
            for code, kl in self.all_kls.items():
                c = kl['c']; v = kl['v']; n = len(c)
                if n < 25: continue
                
                name = kl.get('name', code)
                sectors = self.match_sector(name, code)
                
                if target_sec not in sectors:
                    continue
                
                ti = n - 1
                tc = c[ti]; tv = v[ti]
                
                if tv * tc < 5000: continue
                
                # 量比
                vol_20d = sum(v[max(0,ti-20):ti]) / min(20, ti)
                vol_ratio = tv / vol_20d if vol_20d > 0 else 1
                
                # 涨跌幅
                if ti >= 1 and c[ti-1] > 0:
                    pct = (tc / c[ti-1] - 1) * 100
                else:
                    pct = 0
                
                # 5日涨幅
                if ti >= 5 and c[ti-5] > 0:
                    pct_5d = (tc / c[ti-5] - 1) * 100
                else:
                    pct_5d = 0
                
                # 20日涨幅
                if ti >= 20 and c[ti-20] > 0:
                    pct_20d = (tc / c[ti-20] - 1) * 100
                else:
                    pct_20d = 0
                
                # MA
                ma10 = sum(c[max(0,ti-9):ti+1]) / min(10, ti+1)
                dev_ma10 = (tc / ma10 - 1) * 100 if ma10 > 0 else 0
                
                stock_info = {
                    'code': code, 'name': name,
                    'vol_ratio': round(vol_ratio, 2),
                    'pct': round(pct, 2),
                    'pct_5d': round(pct_5d, 2),
                    'pct_20d': round(pct_20d, 2),
                    'dev_ma10': round(dev_ma10, 2),
                    'turnover': tv * tc,
                }
                sec_stocks.append(stock_info)
                sec_vol_ratios.append(vol_ratio)
                sec_pcts.append(pct)
            
            if not sec_stocks:
                continue
            
            # 板块统计
            avg_vr = sum(sec_vol_ratios) / len(sec_vol_ratios)
            avg_pct = sum(sec_pcts) / len(sec_pcts)
            
            # 放量股
            spike_stocks = [s for s in sec_stocks if s['vol_ratio'] >= 2.0]
            
            # 涨幅>3%的个股
            up_stocks = [s for s in sec_stocks if s['pct'] > 3]
            
            # 回调到位的个股
            pullback_stocks = [s for s in sec_stocks if s['pct_5d'] < -3 and s['dev_ma10'] < 0]
            
            # 判断板块状态
            if avg_vr >= 2.0:
                status = '🔥 放量异动'
            elif avg_vr >= 1.3:
                status = '📈 温和放量'
            elif avg_vr < 0.7:
                status = '📉 缩量观望'
            elif avg_pct > 2:
                status = '📈 走强'
            elif avg_pct < -2:
                status = '📉 走弱'
            else:
                status = '➡️ 震荡'
            
            results[target_sec] = {
                'status': status,
                'stock_count': len(sec_stocks),
                'avg_vol_ratio': round(avg_vr, 2),
                'avg_pct': round(avg_pct, 2),
                'spike_count': len(spike_stocks),
                'up_count': len(up_stocks),
                'pullback_count': len(pullback_stocks),
                'top_spikes': sorted(spike_stocks, key=lambda x: -x['vol_ratio'])[:5],
                'top_gainers': sorted(up_stocks, key=lambda x: -x['pct'])[:5],
                'pullback_candidates': sorted(pullback_stocks, key=lambda x: x['pct_5d'])[:5],
            }
        
        return results
    
    # ================================================================
    # 6. 综合检测
    # ================================================================
    def detect_all(self, watch_sectors=None):
        """
        运行所有检测, 返回综合报告
        
        返回: {
            'sector_spikes': [...],        # 板块放量异动
            'stock_anomalies': [...],      # 个股突发放量
            'accumulation': [...],         # 主力建仓形态
            'pullback_setups': [...],      # 回调到位
            'watch_sectors': {...},        # 关注板块监控
            'summary': str,                # 综合摘要
        }
        """
        print(f"\n{'='*80}")
        print(f"  🔍 主力资金动向检测 (Smart Money Detector)")
        print(f"{'='*80}")
        
        # 1. 板块放量异动
        print(f"\n  [1/4] 板块级别放量异动...")
        sector_spikes = self.detect_sector_volume_spike(min_vol_ratio=2.5)
        print(f"    检测到 {len(sector_spikes)} 个板块出现放量异动")
        
        # 2. 个股突发放量
        print(f"\n  [2/4] 个股突发放量...")
        stock_anomalies = self.detect_stock_volume_anomaly(min_vol_ratio=3.0)
        print(f"    检测到 {len(stock_anomalies)} 只个股突发放量")
        
        # 3. 主力建仓形态
        print(f"\n  [3/4] 主力建仓形态识别...")
        accumulation = self.detect_accumulation_pattern()
        print(f"    检测到 {len(accumulation)} 个建仓形态")
        
        # 4. 回调到位
        print(f"\n  [4/4] 回调到位检测...")
        pullback_setups = self.detect_pullback_setup()
        print(f"    检测到 {len(pullback_setups)} 个回调到位信号")
        
        # 5. 关注板块
        print(f"\n  [5/5] 关注板块专项监控...")
        watch = self.monitor_watch_sectors(watch_sectors)
        
        # 生成摘要
        summary_parts = []
        if sector_spikes:
            top_sec = sector_spikes[0]
            summary_parts.append(f"板块放量: {top_sec['sector']}({top_sec['spike_count']}只放量)")
        if stock_anomalies:
            top_anom = stock_anomalies[0]
            summary_parts.append(f"个股异动: {top_anom['name']}({top_anom['anomaly_type']} {top_anom['vol_ratio']:.1f}x)")
        if accumulation:
            top_acc = accumulation[0]
            summary_parts.append(f"建仓形态: {top_acc['name']}({top_acc['pattern']} {top_acc['score']}分)")
        if pullback_setups:
            top_pb = pullback_setups[0]
            summary_parts.append(f"回调到位: {top_pb['name']}(回踩{top_pb['support_level']} {top_pb['pullback_pct']:+.1f}%)")
        
        summary = ' | '.join(summary_parts) if summary_parts else '无显著信号'
        
        return {
            'sector_spikes': sector_spikes,
            'stock_anomalies': stock_anomalies,
            'accumulation': accumulation,
            'pullback_setups': pullback_setups,
            'watch_sectors': watch,
            'summary': summary,
        }


def print_smart_money_report(results, top_n=10):
    """打印主力资金检测报告"""
    
    # 板块放量
    sector_spikes = results.get('sector_spikes', [])
    if sector_spikes:
        print(f"\n{'━'*100}")
        print(f"  📊 板块放量异动 Top{min(top_n, len(sector_spikes))}")
        print(f"{'━'*100}")
        for i, s in enumerate(sector_spikes[:top_n]):
            direction = '📈' if s['direction'] == 'bullish' else '📦'
            print(f"\n  {i+1}. {direction} {s['sector']}  强度:{s['signal_strength']:.0f}  放量{s['spike_count']}/{s['total_stocks']}只({s['spike_ratio']:.0f}%)")
            print(f"     平均量比:{s['avg_vol_ratio']:.1f}x  平均涨幅:{s['avg_pct']:+.1f}%  成交额:{s['total_turnover']/1e8:.1f}亿")
            for sp in s['top_spikes'][:3]:
                print(f"     {sp['name']}({sp['code']}) 量比{sp['vol_ratio']:.1f}x {sp['pct']:+.1f}%")
    
    # 个股异常放量
    stock_anomalies = results.get('stock_anomalies', [])
    if stock_anomalies:
        print(f"\n{'━'*100}")
        print(f"  ⚡ 个股突发放量(3x+) Top{min(top_n, len(stock_anomalies))}")
        print(f"{'━'*100}")
        for i, s in enumerate(stock_anomalies[:top_n]):
            print(f"\n  {i+1}. {s['name']}({s['code']}) 量比{s['vol_ratio']:.1f}x {s['pct']:+.1f}%")
            print(f"     类型:{s['anomaly_type']}  振幅:{s['amp']:.1f}%  板块:{','.join(s['sectors'][:3])}")
            if s['vol_trend']:
                print(f"     量比趋势: {' → '.join(str(x) for x in s['vol_trend'])}")
    
    # 建仓形态
    accumulation = results.get('accumulation', [])
    if accumulation:
        print(f"\n{'━'*100}")
        print(f"  🏗️ 主力建仓形态 Top{min(top_n, len(accumulation))}")
        print(f"{'━'*100}")
        for i, s in enumerate(accumulation[:top_n]):
            pattern_icon = '🔄' if 'A_' in s['pattern'] else '🚀'
            print(f"\n  {i+1}. {pattern_icon} {s['name']}({s['code']}) [{s['pattern']}] 得分:{s['score']}")
            print(f"     5日:{s['pct_5d']:+.1f}%  20日:{s['pct_20d']:+.1f}%  量比:{s['vol_ratio']:.2f}x  连跌:{s['consec_down']}天")
            print(f"     板块:{','.join(s['sectors'][:3])}")
            print(f"     信号: {' | '.join(s['signals'][1:])}")  # skip first (score)
    
    # 回调到位
    pullback_setups = results.get('pullback_setups', [])
    if pullback_setups:
        print(f"\n{'━'*100}")
        print(f"  🎯 回调到位 Top{min(top_n, len(pullback_setups))}")
        print(f"{'━'*100}")
        for i, s in enumerate(pullback_setups[:top_n]):
            support_icon = {'MA10': '🟢', 'MA20': '🟡', 'MA60': '🔵'}.get(s['support_level'], '⚪')
            print(f"\n  {i+1}. {support_icon} {s['name']}({s['code']}) 得分:{s['score']}")
            print(f"     回调:{s['pullback_pct']:+.1f}%  5日:{s['pct_5d']:+.1f}%  20日:{s['pct_20d']:+.1f}%")
            print(f"     回踩{s['support_level']}  量比:{s['vol_ratio']:.2f}x  连跌:{s['consec_down']}天")
            print(f"     板块:{','.join(s['sectors'][:3])}")
            print(f"     信号: {' | '.join(s['signals'][1:])}")
    
    # 关注板块
    watch = results.get('watch_sectors', {})
    if watch:
        print(f"\n{'━'*100}")
        print(f"  👀 关注板块监控")
        print(f"{'━'*100}")
        for sec, info in sorted(watch.items(), key=lambda x: -x[1]['avg_vol_ratio']):
            print(f"\n  {info['status']} {sec}: {info['stock_count']}只 | 均量比{info['avg_vol_ratio']:.1f}x | 均涨幅{info['avg_pct']:+.1f}%")
            if info['spike_count']:
                print(f"    放量{info['spike_count']}只 | 涨>3%:{info['up_count']}只 | 回调到位:{info['pullback_count']}只")
                for sp in info['top_spikes'][:3]:
                    print(f"    {sp['name']}({sp['code']}) 量比{sp['vol_ratio']:.1f}x {sp['pct']:+.1f}%")
            if info['pullback_candidates']:
                print(f"    💡回调候选:")
                for pb in info['pullback_candidates'][:3]:
                    print(f"    {pb['name']}({pb['code']}) 5日{pb['pct_5d']:+.1f}% MA10偏离{pb['dev_ma10']:+.1f}%")
    
    # 摘要
    print(f"\n{'='*100}")
    print(f"  📋 综合摘要: {results.get('summary', '')}")
    print(f"{'='*100}")


if __name__ == '__main__':
    # 独立测试
    import sys, os, time
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from scan_v1224 import get_all_stocks, get_kl, load_sector_mapping, match_sector
    
    load_sector_mapping()
    stocks = [s for s in get_all_stocks() if 2 <= s['price'] <= 80 and s['circ_mv'] <= 500]
    print(f"全量: {len(stocks)}只")
    
    all_kls = {}
    for i, s in enumerate(stocks[:500]):  # 测试只取500只
        kl = get_kl(s['code'], 80)
        if kl:
            kl['name'] = s['name']
            kl['code'] = s['code']
            all_kls[s['code']] = kl
        time.sleep(0.005)
    
    detector = SmartMoneyDetector(all_kls, match_sector)
    results = detector.detect_all()
    print_smart_money_report(results)