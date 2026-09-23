#!/usr/bin/env python3
"""
v9.5 前瞻性事件日历模块
======================
采集未来3-7天即将发生的财经大事件，用于提前埋伏选股

数据源:
  1. 东方财富: 业绩预告、股东大会、停复牌、新股申购
  2. 华尔街见闻: 全球宏观经济事件(美联储议息/CPI/PMI等)
  3. 中国政府网: 最新政策发布

使用方式:
    from event_calendar import fetch_upcoming_events, score_event_impact
    events = fetch_upcoming_events(days_ahead=7)
    event_bonus = score_event_impact(code, name, events)
"""

import requests, json, time
from datetime import datetime, timedelta
from collections import defaultdict
import pandas as pd


# ============ akshare A股事件数据（远程沙盒可用） ============

def fetch_em_forecast(days_ahead=7):
    """获取业绩预告 — akshare版(远程沙盒可用)"""
    results = []
    try:
        import akshare as ak
        # 获取最近财报季的业绩预告
        df = ak.stock_yjyg_em(date='20260630')
        if df is None or df.empty:
            print(f"    业绩预告: 无数据")
            return results

        # 按公告日期过滤未来N天
        today = datetime.now()
        end_date = today + timedelta(days=days_ahead)
        df['公告日期_dt'] = pd.to_datetime(df['公告日期'], errors='coerce')
        recent = df[
            (df['公告日期_dt'] >= today) &
            (df['公告日期_dt'] <= end_date)
        ]

        # 只保留主板 + 预增/扭亏/略增
        if recent.empty:
            # 如果没有精确日期匹配，取全部最新数据
            recent = df

        main = recent[recent['股票代码'].str.startswith(('60', '00'), na=False)]
        positive = main[main['预告类型'].str.contains('预增|扭亏|略增|续盈', na=False)]

        for _, row in positive.iterrows():
            code = str(row.get('股票代码', ''))
            name = str(row.get('股票简称', ''))
            predict_type = str(row.get('预告类型', ''))
            yoy_val = row.get('业绩变动幅度', 0)
            notice_date = str(row.get('公告日期', ''))[:10]
            try:
                yoy = float(yoy_val)
            except (ValueError, TypeError):
                yoy = 0

            detail = f"{predict_type}" + (f" 净利润同比+{yoy:.1f}%" if yoy and yoy > 0 else "")
            results.append({
                'code': code,
                'name': name,
                'date': notice_date,
                'event_type': '业绩预告',
                'sub_type': predict_type,
                'detail': detail,
                'impact': 'positive',
                'strength': 3 if yoy > 50 else (2 if yoy > 20 else 1),
            })

        # 去重（同股票取最强预告）
        seen = {}
        for r in results:
            if r['code'] not in seen or r['strength'] > seen[r['code']]['strength']:
                seen[r['code']] = r
        results = list(seen.values())

    except ImportError:
        print("    业绩预告: akshare未安装")
    except Exception as e:
        print(f"  业绩预告获取失败: {e}")
    return results


def fetch_em_meeting(days_ahead=7):
    """获取股东大会 — akshare版(远程沙盒可用)"""
    results = []
    try:
        import akshare as ak
        today = datetime.now()
        end_date = today + timedelta(days=days_ahead)

        df = ak.stock_gddh_em()
        if df is None or df.empty:
            print(f"    股东大会: 无数据")
            return results

        # 按"召开开始日"过滤未来N天
        df['召开开始日_dt'] = pd.to_datetime(df['召开开始日'], errors='coerce')
        recent = df[
            (df['召开开始日_dt'] >= today) &
            (df['召开开始日_dt'] <= end_date)
        ]

        for _, row in recent.iterrows():
            code = str(row.get('代码', ''))
            name = str(row.get('简称', ''))
            if not code.startswith(('60', '00')): continue
            if 'ST' in name: continue
            meeting_date = str(row.get('召开开始日', ''))[:10]
            meeting_name = str(row.get('股东大会名称', ''))
            results.append({
                'code': code, 'name': name, 'date': meeting_date,
                'event_type': '股东大会', 'sub_type': meeting_name,
                'detail': meeting_name, 'impact': 'neutral', 'strength': 1,
            })

        print(f"    股东大会: {len(results)}条(未来{days_ahead}天)")
    except ImportError:
        print("    股东大会: akshare未安装")
    except Exception as e:
        print(f"  股东大会获取失败: {e}")
    return results


def fetch_em_suspend(days_ahead=7):
    """获取停复牌 — akshare版(远程沙盒可用)"""
    results = []
    try:
        import akshare as ak
        df = ak.stock_tfp_em()
        if df is None or df.empty: return results

        today = datetime.now()
        end_date = today + timedelta(days=days_ahead)

        for _, row in df.iterrows():
            code = str(row.get('代码', ''))
            name = str(row.get('名称', ''))
            if not code.startswith(('60', '00')): continue

            # 解析停牌/复牌时间
            suspend_time = str(row.get('停牌时间', ''))
            resume_time = str(row.get('预计复牌时间', ''))
            reason = str(row.get('停牌原因', ''))

            # 判断是否在未来N天内有复牌事件
            is_future_resume = False
            resume_date = ''
            try:
                if resume_time and resume_time != 'NaT' and resume_time != 'nan':
                    rd = pd.to_datetime(resume_time)
                    if today <= rd <= end_date:
                        is_future_resume = True
                        resume_date = rd.strftime('%Y-%m-%d')
            except: pass

            if is_future_resume:
                results.append({
                    'code': code, 'name': name, 'date': resume_date,
                    'event_type': '停复牌', 'sub_type': '复牌',
                    'detail': f"复牌 ({reason})",
                    'impact': 'positive', 'strength': 2,
                })
            elif suspend_time:
                # 未来停牌（风险）
                try:
                    sd = pd.to_datetime(suspend_time)
                    if today <= sd <= end_date:
                        results.append({
                            'code': code, 'name': name, 'date': sd.strftime('%Y-%m-%d'),
                            'event_type': '停复牌', 'sub_type': '停牌',
                            'detail': f"停牌 ({reason})",
                            'impact': 'negative', 'strength': 0,
                        })
                except: pass
    except ImportError:
        print("    停复牌: akshare未安装")
    except Exception as e:
        print(f"  停复牌获取失败: {e}")
    return results


def fetch_em_new_stock(days_ahead=7):
    """获取新股申购日历 — akshare版(远程沙盒可用)"""
    results = []
    try:
        import akshare as ak
        df = ak.stock_new_ipo_cninfo()
        if df is None or df.empty: return results

        today = datetime.now()
        end_date = today + timedelta(days=days_ahead)

        for _, row in df.iterrows():
            code = str(row.get('证劵代码', row.get('证券代码', '')))
            name = str(row.get('证券简称', ''))
            apply_date = str(row.get('申购日期', ''))
            list_date = str(row.get('上市日期', ''))

            try:
                if apply_date and apply_date not in ('NaT', 'nan', ''):
                    ad = pd.to_datetime(apply_date)
                    if today <= ad <= end_date:
                        results.append({
                            'code': code, 'name': name, 'date': ad.strftime('%Y-%m-%d'),
                            'event_type': '新股申购', 'sub_type': '申购',
                            'detail': f"申购日 {ad.strftime('%Y-%m-%d')}",
                            'impact': 'neutral', 'strength': 0,
                        })
                if list_date and list_date not in ('NaT', 'nan', ''):
                    ld = pd.to_datetime(list_date)
                    if today <= ld <= end_date:
                        results.append({
                            'code': code, 'name': name, 'date': ld.strftime('%Y-%m-%d'),
                            'event_type': '新股上市', 'sub_type': '上市',
                            'detail': f"上市日 {ld.strftime('%Y-%m-%d')}",
                            'impact': 'neutral', 'strength': 0,
                        })
            except: pass
    except ImportError:
        print("    新股申购: akshare未安装")
    except Exception as e:
        print(f"  新股申购获取失败: {e}")
    return results


def fetch_em_disclosure(days_ahead=7):
    """获取财报预约披露时间 — akshare版(远程沙盒可用)"""
    results = []
    try:
        import akshare as ak
        df = ak.stock_yysj_em(date='20260630')
        if df is None or df.empty: return results

        today = datetime.now()
        end_date = today + timedelta(days=days_ahead)

        for _, row in df.iterrows():
            code = str(row.get('股票代码', ''))
            name = str(row.get('股票简称', ''))
            if not code.startswith(('60', '00')): continue

            # 取首次预约时间
            appt = str(row.get('首次预约时间', ''))
            try:
                if appt and appt not in ('NaT', 'nan', ''):
                    ad = pd.to_datetime(appt)
                    if today <= ad <= end_date:
                        results.append({
                            'code': code, 'name': name, 'date': ad.strftime('%Y-%m-%d'),
                            'event_type': '财报披露', 'sub_type': '中报预约',
                            'detail': f"中报预约披露 {ad.strftime('%Y-%m-%d')}",
                            'impact': 'neutral', 'strength': 1,
                        })
            except: pass
    except Exception as e:
        print(f"  预约披露获取失败: {e}")
    return results


# ============ 华尔街见闻宏观经济日历 ============

def fetch_wscn_macro(days_ahead=7):
    """获取全球宏观经济事件(未来N天)"""
    results = []
    now = datetime.now()
    start_ts = int(now.timestamp())
    end_ts = int((now + timedelta(days=days_ahead)).timestamp())
    url = 'https://api-one-wscn.awtmt.com/apiv1/finance/macrodatas'
    params = {
        'start': start_ts,
        'end': end_ts,
    }
    try:
        r = requests.get(url, params=params, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
        data = r.json()
        if data.get('code') != 20000:
            return results
        for item in data.get('data', {}).get('items', []):
            event_time = item.get('public_date', 0)
            country = item.get('country', '')
            title = item.get('title', '')
            importance = item.get('importance', 0)
            # 只保留重要事件(重要性>=3 或 包含关键词)
            is_important = importance >= 3 or any(kw in title for kw in ['美联储', '议息', 'CPI', 'PPI', 'PMI', '非农', 'GDP', '央行', '利率决议', '央行', '欧央行'])
            if is_important:
                date_str = datetime.fromtimestamp(event_time).strftime('%Y-%m-%d') if event_time else item.get('observation_date', '')
                results.append({
                    'code': '',
                    'name': '',
                    'date': date_str,
                    'event_type': '宏观经济',
                    'sub_type': country,
                    'detail': title,
                    'impact': 'neutral',
                    'strength': importance,
                })
    except Exception as e:
        print(f"  宏观经济获取失败: {e}")
    return results


# ============ 中国政府网最新政策 ============

def fetch_gov_policy():
    """获取国务院最新政策发布"""
    results = []
    url = 'https://www.gov.cn/pushinfo/v150203/pushinfo.jsonp'
    try:
        r = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
        text = r.text
        # JSONP格式: pushInfoJsonpCallBack([{...}, {...}])
        # 去掉回调函数名和括号
        start = text.find('[')
        end = text.rfind(']') + 1
        if start >= 0 and end > start:
            items = json.loads(text[start:end])
            for item in items:
                title = item.get('title', '')
                pub_date = item.get('pubDate', '')
                # 识别政策相关行业
                sectors = []
                sector_keywords = {
                    '半导体': '半导体设备/材料', '芯片': '半导体设备/材料',
                    '新能源': '固态电池', '光伏': '固态电池', '储能': '固态电池',
                    '汽车': '汽车零部件', '自动驾驶': '汽车零部件',
                    '医药': '创新药', '医疗': '创新药', '医保': '创新药',
                    '农业': '养殖/农业', '粮食': '养殖/农业', '生猪': '养殖/农业',
                    '金融': '大金融', '银行': '大金融', '证券': '大金融',
                    '稀土': '稀土永磁', '黄金': '黄金/贵金属',
                    '机器人': '人形机器人', 'AI': '算力/AI服务器',
                    '算力': '算力基础设施', '数据中心': '算力基础设施',
                }
                for kw, sector in sector_keywords.items():
                    if kw in title:
                        sectors.append(sector)
                sectors = list(dict.fromkeys(sectors))  # 去重
                results.append({
                    'code': '',
                    'name': '',
                    'date': pub_date,
                    'event_type': '政策发布',
                    'sub_type': '国务院',
                    'detail': title,
                    'impact': 'positive',
                    'strength': 3 if sectors else 2,
                    'sectors': sectors,
                })
    except Exception as e:
        print(f"  政策获取失败: {e}")
    return results


# ============ 统一采集入口 ============

def fetch_upcoming_events(days_ahead=7):
    """
    采集未来N天的全部事件
    返回: {
        'stock_events': [个股事件],
        'macro_events': [宏观事件],
        'policy_events': [政策事件],
        'summary': str,
    }
    """
    print(f"\n📅 采集未来{days_ahead}天事件日历...")
    t0 = time.time()

    # 个股事件
    stock_events = []
    print("  [1/4] 业绩预告...")
    forecast = fetch_em_forecast(days_ahead)
    print(f"    预增事件: {len(forecast)}条")
    stock_events.extend(forecast)

    print("  [2/4] 股东大会...")
    meetings = fetch_em_meeting(days_ahead)
    print(f"    股东大会: {len(meetings)}条")
    stock_events.extend(meetings)

    print("  [3/4] 停复牌...")
    suspends = fetch_em_suspend(days_ahead)
    print(f"    停复牌: {len(suspends)}条")
    stock_events.extend(suspends)

    print("  [4/5] 新股申购...")
    new_stocks = fetch_em_new_stock(days_ahead)
    print(f"    新股申购: {len(new_stocks)}条")
    stock_events.extend(new_stocks)

    print("  [5/6] 财报预约披露...")
    disclosure = fetch_em_disclosure(days_ahead)
    print(f"    预约披露: {len(disclosure)}条")
    stock_events.extend(disclosure)

    # 宏观事件
    print("  [6/7] 宏观经济事件...")
    macro_events = fetch_wscn_macro(days_ahead)
    print(f"    宏观事件: {len(macro_events)}条")

    # 政策事件
    print("  [7/7] 最新政策...")
    policy_events = fetch_gov_policy()
    print(f"    政策发布: {len(policy_events)}条")

    elapsed = time.time() - t0
    total = len(stock_events) + len(macro_events) + len(policy_events)
    print(f"\n📅 事件日历采集完成: {total}条 (耗时{elapsed:.1f}s)")

    return {
        'stock_events': stock_events,
        'macro_events': macro_events,
        'policy_events': policy_events,
        'days_ahead': days_ahead,
    }


# ============ 事件影响评分 ============

def _parse_event_date(date_str):
    """解析事件日期字符串为date对象"""
    if not date_str:
        return None
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y%m%d'):
        try:
            return datetime.strptime(str(date_str)[:10], fmt).date()
        except ValueError:
            continue
    return None


def _event_date_factor(days_diff):
    """
    v9.7 T-1事件埋伏日期衰减因子
    days_diff = (event_date - today).days
    """
    if days_diff == 1:          # T-1 明天 → 最佳埋伏时机，全分
        return 1.0
    elif days_diff == 0:        # T-0 今天 → 利好出尽，不追
        return 0.0
    elif days_diff >= 2:        # T+2及以后 → 远期衰减，最低保留30%
        return max(0.3, 1.0 - (days_diff - 1) * 0.2)
    else:                       # 已过期事件
        return 0.0


def score_event_impact(code, name, events, news_sectors=None):
    """
    计算某只股票的事件埋伏加分 (v9.7 T-1布局)
    返回: (bonus, details_list)
    """
    bonus = 0
    details = []
    stock_events = events.get('stock_events', [])
    policy_events = events.get('policy_events', [])

    code = str(code)
    name = str(name)
    today = datetime.now().date()

    # 1. 个股直接事件匹配 —— T-1埋伏逻辑
    for evt in stock_events:
        if evt['code'] == code or evt['name'] in name:
            evt_date = _parse_event_date(evt.get('date', ''))
            days_diff = (evt_date - today).days if evt_date else 1
            date_factor = _event_date_factor(days_diff)

            if evt['impact'] == 'positive':
                strength = evt.get('strength', 1)
                base_bonus = strength * 15  # v9.6基础分
                evt_bonus = base_bonus * date_factor
                if evt_bonus > 0:
                    bonus += evt_bonus
                    t_label = f"T+{days_diff}" if days_diff >= 2 else ("T-1" if days_diff == 1 else "T-0")
                    details.append(f"📅 {evt['date']} {evt['event_type']}: {evt['detail']} (+{evt_bonus:.0f}分, {t_label})")
                elif days_diff == 0:
                    details.append(f"⚠️ {evt['date']} {evt['event_type']}: {evt['detail']} (今日利好出尽，不追)")
            elif evt['impact'] == 'negative':
                details.append(f"⚠️ {evt['date']} {evt['event_type']}: {evt['detail']} (风险)")

    # 2. 政策事件间接匹配（通过板块）—— 同样做日期衰减
    if news_sectors:
        for evt in policy_events:
            evt_sectors = evt.get('sectors', [])
            for ns in news_sectors:
                if ns in evt_sectors:
                    evt_date = _parse_event_date(evt.get('date', ''))
                    days_diff = (evt_date - today).days if evt_date else 1
                    date_factor = _event_date_factor(days_diff)

                    if date_factor > 0:
                        policy_bonus = evt.get('strength', 2) * 8 * date_factor
                        bonus += policy_bonus
                        details.append(f"🏛️ 政策利好[{evt['date']}]: {evt['detail'][:30]}... (+{policy_bonus:.0f}分)")
                    break

    return bonus, details


def generate_event_report(events, min_strength=2):
    """生成事件日历报告"""
    stock_events = events.get('stock_events', [])
    macro_events = events.get('macro_events', [])
    policy_events = events.get('policy_events', [])

    # 按日期分组
    by_date = defaultdict(list)
    for evt in stock_events + macro_events + policy_events:
        if evt.get('strength', 0) >= min_strength:
            by_date[evt['date']].append(evt)

    if not by_date:
        print("  未检测到重要事件")
        return

    print(f"\n📅 未来事件日历（重要性≥{min_strength}）:")
    print("=" * 70)

    for date in sorted(by_date.keys()):
        day_events = by_date[date]
        print(f"\n  {date} ({len(day_events)}个事件):")
        for evt in sorted(day_events, key=lambda x: -x.get('strength', 0)):
            icon = {'positive': '📈', 'negative': '📉', 'neutral': '📌'}.get(evt['impact'], '📌')
            code_str = f"[{evt['code']}] " if evt['code'] else ""
            print(f"    {icon} {code_str}{evt['event_type']}: {evt['detail'][:50]}")

    # 宏观事件特别提醒
    important_macro = [e for e in macro_events if e.get('strength', 0) >= 3]
    if important_macro:
        print(f"\n  ⚠️ 重要宏观事件提醒:")
        for e in important_macro[:5]:
            print(f"    📊 {e['date']} {e['sub_type']}: {e['detail']}")


# ============ 独立测试 ============

if __name__ == '__main__':
    print("=" * 70)
    print("v9.5 前瞻性事件日历测试")
    print("=" * 70)

    events = fetch_upcoming_events(days_ahead=7)
    generate_event_report(events, min_strength=1)

    # 测试个股评分
    test_code = '000001'
    test_name = '平安银行'
    bonus, details = score_event_impact(test_code, test_name, events)
    if details:
        print(f"\n📊 {test_name} 事件埋伏评分: +{bonus}分")
        for d in details:
            print(f"  {d}")
