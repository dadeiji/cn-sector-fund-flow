#!/usr/bin/env python3
"""
资金流向实时走势图
实时采集板块资金流数据，绘制多板块资金净流入走势图。
用法:
  python fund_flow.py              # 实时采集 + HTTP 实时图表（默认）
  python fund_flow.py --once       # 单次采集生成静态图表
  python fund_flow.py --collect    # 仅后台采集数据
  python fund_flow.py --chart      # 仅从已有数据生成图表
"""

import argparse
import http.server
import json
import socketserver
import sys
import threading
import time
import webbrowser
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path

import requests
import pandas as pd
import plotly.graph_objects as go

try:
    import py_mini_racer
    from akshare.stock_feature.stock_fund_flow import _get_file_content_ths
    HAS_THS_AUTH = True
except ImportError:
    HAS_THS_AUTH = False

DATA_DIR = Path(__file__).parent / "data"
CHART_DIR = Path(__file__).parent / "charts"
COLLECT_INTERVAL = 60

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
{refresh}
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ height: 100%; overflow: hidden; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Microsoft YaHei", "PingFang SC", sans-serif; }}
#chart-container {{ width: 100%; height: 100%; }}
</style>
<script src="https://cdn.plot.ly/plotly-2.35.0.min.js"></script>
</head>
<body>
<div id="chart-container">{plot_html}</div>
</body>
</html>"""

DUAL_TAB_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
{refresh}
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ height: 100%; overflow: hidden; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Microsoft YaHei", "PingFang SC", sans-serif; background: #f5f5f5; }}
.tab-bar {{
    display: flex; align-items: center; gap: 0;
    background: #fff; border-bottom: 2px solid #e0e0e0;
    padding: 0 16px; height: 44px; flex-shrink: 0;
}}
.tab-btn {{
    padding: 8px 24px; font-size: 14px; font-weight: 600;
    border: none; background: none; cursor: pointer;
    color: #888; border-bottom: 3px solid transparent;
    transition: all 0.2s; margin-bottom: -2px;
}}
.tab-btn:hover {{ color: #333; }}
.tab-btn.active {{ color: #1a73e8; border-bottom-color: #1a73e8; }}
.tab-badge {{
    font-size: 11px; color: #999; margin-left: 4px; font-weight: 400;
}}
.tab-panel {{ display: none; width: 100%; height: calc(100vh - 44px); }}
.tab-panel.active {{ display: block; }}
.page-wrap {{ display: flex; flex-direction: column; height: 100vh; }}
</style>
<script src="https://cdn.plot.ly/plotly-2.35.0.min.js"></script>
</head>
<body>
<div class="page-wrap">
  <div class="tab-bar">
    <button class="tab-btn active" onclick="switchTab('em')">东方财富<span class="tab-badge">主力净流入</span></button>
    <button class="tab-btn" onclick="switchTab('ths')">同花顺<span class="tab-badge">资金净额</span></button>
  </div>
  <div id="panel-em" class="tab-panel active">{em_chart}</div>
  <div id="panel-ths" class="tab-panel">{ths_chart}</div>
</div>
<script>
function switchTab(tab) {{
    document.querySelectorAll('.tab-btn').forEach(function(btn, i) {{
        btn.classList.toggle('active', (tab === 'em' && i === 0) || (tab === 'ths' && i === 1));
    }});
    document.getElementById('panel-em').classList.toggle('active', tab === 'em');
    document.getElementById('panel-ths').classList.toggle('active', tab === 'ths');
    // Trigger Plotly resize for the newly visible chart
    var panel = document.getElementById('panel-' + tab);
    var gd = panel.querySelector('.js-plotly-plot');
    if (gd) {{ Plotly.Plots.resize(gd); }}
}}
</script>
</body>
</html>"""

# AJAX 动态模板：JS 轮询 /api/data，Plotly.react 增量更新
AJAX_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { height: 100%; overflow: hidden; }
body { font-family: -apple-system, BlinkMacSystemFont, "Microsoft YaHei", "PingFang SC", sans-serif; background: #f5f5f5; }
.tab-bar {
    display: flex; align-items: center; gap: 0;
    background: #fff; border-bottom: 2px solid #e0e0e0;
    padding: 0 16px; height: 44px; flex-shrink: 0;
}
.tab-btn {
    padding: 8px 24px; font-size: 14px; font-weight: 600;
    border: none; background: none; cursor: pointer;
    color: #888; border-bottom: 3px solid transparent;
    transition: all 0.2s; margin-bottom: -2px;
}
.tab-btn:hover { color: #333; }
.tab-btn.active { color: #1a73e8; border-bottom-color: #1a73e8; }
.tab-badge { font-size: 11px; color: #999; margin-left: 4px; font-weight: 400; }
.status-dot {
    width: 8px; height: 8px; border-radius: 50%;
    display: inline-block; margin-left: 8px; vertical-align: middle;
}
.status-dot.ok { background: #2ecc71; }
.status-dot.loading { background: #f39c12; animation: pulse 1s infinite; }
.status-dot.error { background: #e74c3c; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
.tab-panel { display: none; width: 100%; height: calc(100vh - 44px); }
.tab-panel.active { display: block; }
.page-wrap { display: flex; flex-direction: column; height: 100vh; }
.chart-box { width: 100%; height: 100%; }
.loading-msg {
    display: flex; align-items: center; justify-content: center;
    height: 100%; color: #999; font-size: 14px;
}
</style>
<script src="https://cdn.plot.ly/plotly-2.35.0.min.js"></script>
</head>
<body>
<div class="page-wrap">
  <div class="tab-bar">
    <button class="tab-btn active" onclick="switchTab('em')">
      东方财富<span class="tab-badge">主力净流入</span>
      <span class="status-dot loading" id="dot-em"></span>
    </button>
    <button class="tab-btn" onclick="switchTab('ths')">
      同花顺<span class="tab-badge">资金净额</span>
      <span class="status-dot loading" id="dot-ths"></span>
    </button>
  </div>
  <div id="panel-em" class="tab-panel active"><div id="chart-em" class="chart-box"><div class="loading-msg">加载中...</div></div></div>
  <div id="panel-ths" class="tab-panel"><div id="chart-ths" class="chart-box"><div class="loading-msg">加载中...</div></div></div>
</div>
<script>
const POLL_INTERVAL = __POLL_MS__;
const COLORS = __COLORS_JSON__;
const OUTFLOW_COLORS = __OUTFLOW_JSON__;
let activeTab = 'em';
let charts = { em: null, ths: null };

function switchTab(tab) {
    activeTab = tab;
    document.querySelectorAll('.tab-btn').forEach(function(btn, i) {
        btn.classList.toggle('active', (tab === 'em' && i === 0) || (tab === 'ths' && i === 1));
    });
    document.getElementById('panel-em').classList.toggle('active', tab === 'em');
    document.getElementById('panel-ths').classList.toggle('active', tab === 'ths');
    var panel = document.getElementById('panel-' + tab);
    var gd = panel.querySelector('.js-plotly-plot');
    if (gd) { Plotly.Plots.resize(gd); }
}

function setDot(source, status) {
    var dot = document.getElementById('dot-' + source);
    if (dot) { dot.className = 'status-dot ' + status; }
}

function buildTraces(sectors, markerSize) {
    return sectors.map(function(s) {
        var cfg = {
            x: s.times,
            y: s.values,
            mode: 'lines+markers',
            name: s.name,
            line: { color: s.color, width: 2 },
            marker: { color: s.color, size: markerSize },
            hovertemplate: '<b>' + s.name + '</b><br>时间: %{x|%H:%M:%S}<br>' + s.y_label + ': %{y:.2f}亿<extra></extra>',
            hoverlabel: { namelength: -1, font_size: 13 }
        };
        if (s.is_outflow) {
            cfg.marker.symbol = 'diamond';
        }
        return cfg;
    });
}

function buildAnnotations(sectors, y_label) {
    var annots = [];
    sectors.forEach(function(s) {
        var lastVal = s.values[s.values.length - 1];
        var valStr = (lastVal >= 0 ? '+' : '') + lastVal.toFixed(2);
        if (Math.abs(lastVal) < 1) { valStr = (lastVal >= 0 ? '+' : '') + lastVal.toFixed(4); }
        annots.push({
            x: 1.01, y: lastVal,
            xref: 'paper', yref: 'y',
            text: '<b>' + s.name + '</b> ' + valStr,
            showarrow: false,
            xanchor: 'left', yanchor: 'middle',
            font: { color: s.color, size: s.is_outflow ? 11 : 12, family: 'Microsoft YaHei, SimHei, sans-serif' },
            bgcolor: 'rgba(255,255,255,0.95)',
            bordercolor: s.color,
            borderwidth: 1.5,
            borderpad: 3
        });
    });
    return annots;
}

function buildLayout(data, divId) {
    return {
        title: {
            text: '<b>' + data.source_label + ' · 概念板块主力资金流向走势图 ' + data.title_suffix + '</b>',
            font: { size: 16, color: '#333' },
            x: 0.01, y: 0.98, xanchor: 'left', yanchor: 'top'
        },
        xaxis: {
            title: '', type: 'date', tickformat: '%H:%M',
            showgrid: false, zeroline: false,
            range: data.x_range,
            rangebreaks: [{ bounds: [11.5, 13], pattern: 'hour' }],
            tickfont: { size: 11, color: '#666' }
        },
        yaxis: {
            title: { text: data.y_label, font: { size: 12, color: '#666' } },
            type: data.log_scale ? 'log' : 'linear',
            showgrid: true, gridcolor: '#f0f0f0',
            zeroline: true, zerolinecolor: '#ccc', zerolinewidth: 1,
            tickfont: { size: 11, color: '#666' }
        },
        plot_bgcolor: '#fafafa',
        paper_bgcolor: 'white',
        hovermode: 'x unified',
        legend: {
            orientation: 'h', yanchor: 'top', y: 0.99, xanchor: 'left', x: 0.01,
            font: { size: 10 }, bgcolor: 'rgba(255,255,255,0.8)',
            bordercolor: '#ddd', borderwidth: 1
        },
        margin: { l: 60, r: 220, t: 80, b: 40 },
        annotations: buildAnnotations(data.sectors, data.y_label).concat([{
            x: 0.995, y: 0.01, xref: 'paper', yref: 'paper',
            text: '更新于 ' + data.updated_at,
            showarrow: false, font: { size: 10, color: '#999' }
        }])
    };
}

function renderChart(source, data) {
    var divId = 'chart-' + source;
    var el = document.getElementById(divId);
    if (!data || !data.sectors || data.sectors.length === 0) {
        el.innerHTML = '<div class="loading-msg">暂无' + (data ? data.source_label : '') + '数据</div>';
        setDot(source, 'error');
        return;
    }
    var timePoints = data.time_points || 10;
    var markerSize = timePoints <= 3 ? 6 : 3;
    var traces = buildTraces(data.sectors, markerSize);
    var layout = buildLayout(data, divId);
    var config = { responsive: true, displayModeBar: true };

    if (charts[source]) {
        Plotly.react(divId, traces, layout, config);
    } else {
        el.innerHTML = '';
        Plotly.newPlot(divId, traces, layout, config);
        charts[source] = true;
    }
    setDot(source, 'ok');
}

async function fetchData() {
    try {
        var resp = await fetch('/api/data');
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        var json = await resp.json();
        if (json.em) renderChart('em', json.em);
        else setDot('em', 'error');
        if (json.ths) renderChart('ths', json.ths);
        else setDot('ths', 'error');
    } catch (e) {
        console.error('Fetch error:', e);
        setDot('em', 'error');
        setDot('ths', 'error');
    }
}

// 启动轮询
fetchData();
setInterval(fetchData, POLL_INTERVAL);
</script>
</body>
</html>"""


def _wrap_html(plot_html: str, refresh_seconds: int = None) -> str:
    refresh = f'<meta http-equiv="refresh" content="{refresh_seconds}">' if refresh_seconds else ""
    return HTML_TEMPLATE.format(refresh=refresh, plot_html=plot_html)


def _wrap_dual_html(em_chart: str, ths_chart: str, refresh_seconds: int = None) -> str:
    refresh = f'<meta http-equiv="refresh" content="{refresh_seconds}">' if refresh_seconds else ""
    return DUAL_TAB_TEMPLATE.format(refresh=refresh, em_chart=em_chart, ths_chart=ths_chart)
MARKET_OPEN = dt_time(9, 30)
MARKET_CLOSE = dt_time(15, 0)
LUNCH_START = dt_time(11, 30)
LUNCH_END = dt_time(13, 0)
TOP_N = 10
BOTTOM_N = 5  # 净流出前5名
HTTP_PORT = 8899
DATA_FILENAME = "fund_flow_{date}.json"
THS_DATA_FILENAME = "ths_fund_flow_{date}.json"

COLORS = [
    "#e74c3c", "#2ecc71", "#3498db", "#f39c12", "#9b59b6",
    "#1abc9c", "#e67e22", "#34495e", "#c0392b", "#16a085",
    "#2980b9", "#8e44ad", "#27ae60", "#d35400", "#2c3e50",
    "#e91e63", "#00bcd4", "#ff5722", "#795548", "#607d8b",
]
# 净流出板块配色（偏冷/暗色调，与净流入区分）
OUTFLOW_COLORS = [
    "#546e7a", "#78909c", "#90a4ae", "#b0bec5", "#455a64",
]


def is_trading_day() -> bool:
    return datetime.now().weekday() < 5


def is_market_open() -> bool:
    now = datetime.now().time()
    return MARKET_OPEN <= now <= MARKET_CLOSE


def is_lunch_break() -> bool:
    """11:30-13:00 午休时段。"""
    now = datetime.now().time()
    return LUNCH_START <= now < LUNCH_END


def is_collecting_hours() -> bool:
    """采集时段：交易日 9:30-11:30 和 13:00-15:00。"""
    if not is_trading_day():
        return False
    now = datetime.now().time()
    morning = MARKET_OPEN <= now < LUNCH_START
    afternoon = LUNCH_END <= now <= MARKET_CLOSE
    return morning or afternoon


def market_session() -> str:
    """返回当前市场状态：trading / lunch / closed。"""
    if not is_trading_day():
        return "closed"
    now = datetime.now().time()
    if MARKET_OPEN <= now < LUNCH_START:
        return "trading"
    if LUNCH_START <= now < LUNCH_END:
        return "lunch"
    if LUNCH_END <= now <= MARKET_CLOSE:
        return "trading"
    return "closed"


def fmt_value(v: float) -> str:
    if abs(v) >= 1:
        return f"{v:+.2f}"
    return f"{v:+.4f}"


class FundFlowCollector:
    """基于东方财富 API 实时采集概念板块主力净流入数据。

    每个 tick 存储板块名、主力净流入累计值（亿）及增量值（亿）。
    前端可在"增量模式"和"累计模式"间切换：
    - 增量模式：观察资金节奏和转折点
    - 累计模式：看全天总结果
    """

    EAST_MONEY_URL = "https://data.eastmoney.com/dataapi/bkzj/getbkzj"
    EAST_MONEY_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://data.eastmoney.com/bkzj/gn.html",
    }

    def __init__(self, save_dir: Path = DATA_DIR):
        self.save_dir = save_dir
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.running = False
        self._thread = None
        self._prev_flow: dict[str, float] = {}
        self._session = requests.Session()
        self._session.trust_env = False

    def _fetch_raw(self) -> list[dict]:
        r = self._session.get(
            self.EAST_MONEY_URL,
            params={"key": "f62", "code": "m:90+t:3"},
            headers=self.EAST_MONEY_HEADERS,
            timeout=15,
        )
        data = r.json()
        return data.get("data", {}).get("diff", [])

    # 过滤掉非行业/概念板块（市场机制类）
    SECTOR_BLACKLIST = {
        "融资融券", "融资", "融券",
        "深股通", "沪股通", "北向资金", "南向资金",
        "中证500", "中证1000", "沪深300", "上证50",
        "创业板指", "科创50", "MSCI", "富时罗素",
        "标普道琼斯中国", "央行票据", "国债逆回购",
    }

    def _parse(self, raw: list[dict]) -> list[dict]:
        now = datetime.now()
        rows = []
        for item in raw:
            name = item.get("f14", "")
            if name in self.SECTOR_BLACKLIST:
                continue
            main_net = (item.get("f62") or 0) / 1e8  # 转为亿
            prev = self._prev_flow.get(name)
            delta = main_net - prev if prev is not None else None
            rows.append({
                "time": now.strftime("%H:%M:%S"),
                "sector": name,
                "main_net_inflow": main_net,
                "delta": delta,
                "change_pct": None,
                "index_value": None,
            })
        self._prev_flow = {r["sector"]: r["main_net_inflow"] for r in rows}
        return rows

    def snapshot(self) -> list[dict]:
        raw = self._fetch_raw()
        rows = self._parse(raw)
        self._persist(rows)
        return rows

    def _persist(self, rows: list[dict]):
        today = datetime.now().strftime("%Y%m%d")
        fp = self.save_dir / DATA_FILENAME.format(date=today)
        with open(fp, "a") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def start(self, interval: int = COLLECT_INTERVAL):
        if self.running:
            return
        self.running = True
        self._cleanup()

        def _loop():
            while self.running:
                try:
                    if is_collecting_hours():
                        self.snapshot()
                    else:
                        session = market_session()
                        label = {"lunch": "午休时段", "closed": "非交易时间"}.get(session, "非交易时间")
                        print(f"\r[{datetime.now().strftime('%H:%M:%S')}] {label}，等待中...", end="")
                except Exception as e:
                    print(f"\n采集错误: {e}")
                time.sleep(interval)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()
        print(f"数据采集已启动（间隔 {interval}s）")

    def _cleanup(self, keep_days: int = 30):
        """清理超过 keep_days 天的历史数据文件。"""
        cutoff = datetime.now() - timedelta(days=keep_days)
        cutoff_str = cutoff.strftime("%Y%m%d")
        removed = 0
        for fp in self.save_dir.glob("fund_flow_*.json"):
            date_str = fp.stem.replace("fund_flow_", "")
            if date_str <= cutoff_str:
                fp.unlink()
                removed += 1
        if removed:
            print(f"已清理 {removed} 个过期数据文件（保留近 {keep_days} 天）")

    def stop(self):
        self.running = False


class THSFundFlowCollector:
    """基于同花顺 API 实时采集概念板块资金净流入排名数据。

    同时请求降序（净流入前20）和升序（净流出前20）两页数据，
    通过 hexin-v 认证头访问。数据格式与 FundFlowCollector 一致，
    共用 FundFlowChart 生成图表。
    """

    THS_URL_DESC = "http://data.10jqka.com.cn/funds/gnzjl/field/je/order/desc/page/1/ajax/1/free/1/"
    THS_URL_ASC = "http://data.10jqka.com.cn/funds/gnzjl/field/je/order/asc/page/1/ajax/1/free/1/"

    def __init__(self, save_dir: Path = DATA_DIR):
        self.save_dir = save_dir
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.running = False
        self._thread = None
        self._prev_flow: dict[str, float] = {}
        self._session = requests.Session()
        self._session.trust_env = False

    def _make_headers(self) -> dict:
        js_code = py_mini_racer.MiniRacer()
        js_content = _get_file_content_ths("ths.js")
        js_code.eval(js_content)
        v_code = js_code.call("v")
        return {
            "Accept": "text/html, */*; q=0.01",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
            "hexin-v": v_code,
            "Host": "data.10jqka.com.cn",
            "Pragma": "no-cache",
            "Referer": "http://data.10jqka.com.cn/funds/gnzjl/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/90.0.4430.85 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest",
        }

    def _fetch_raw(self) -> list[dict]:
        import re as _re
        headers = self._make_headers()

        results = []
        seen_names = set()

        for url in (self.THS_URL_DESC, self.THS_URL_ASC):
            try:
                r = self._session.get(url, headers=headers, timeout=15)
                r.encoding = "gbk"
                match = _re.search(r'var JS_DATA = (\[.*?\]);', r.text)
                if match:
                    items = json.loads(match.group(1))
                    for item in items:
                        name = item.get("name", "")
                        if name and name not in seen_names:
                            seen_names.add(name)
                            results.append(item)
            except Exception as e:
                print(f"同花顺请求失败 ({url}): {e}")
                continue

        return results

    def _parse(self, raw: list[dict]) -> list[dict]:
        now = datetime.now()
        rows = []
        for item in raw:
            name = item.get("name", "")
            amount = item.get("amount", 0)  # 已经是亿
            prev = self._prev_flow.get(name)
            delta = amount - prev if prev is not None else None
            rows.append({
                "time": now.strftime("%H:%M:%S"),
                "sector": name,
                "main_net_inflow": amount,
                "delta": delta,
                "change_pct": None,
                "index_value": None,
            })
        self._prev_flow = {r["sector"]: r["main_net_inflow"] for r in rows}
        return rows

    def snapshot(self) -> list[dict]:
        raw = self._fetch_raw()
        rows = self._parse(raw)
        self._persist(rows)
        return rows

    def _persist(self, rows: list[dict]):
        today = datetime.now().strftime("%Y%m%d")
        fp = self.save_dir / THS_DATA_FILENAME.format(date=today)
        with open(fp, "a") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def start(self, interval: int = COLLECT_INTERVAL):
        if self.running:
            return
        if not HAS_THS_AUTH:
            print("同花顺采集需要 py_mini_racer 和 akshare，跳过")
            return
        self.running = True
        self._cleanup()

        def _loop():
            while self.running:
                try:
                    if is_collecting_hours():
                        self.snapshot()
                    else:
                        session = market_session()
                        label = {"lunch": "午休时段", "closed": "非交易时间"}.get(session, "非交易时间")
                        print(f"\r[{datetime.now().strftime('%H:%M:%S')}] [同花顺] {label}，等待中...", end="")
                except Exception as e:
                    print(f"\n[同花顺] 采集错误: {e}")
                time.sleep(interval)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()
        print(f"[同花顺] 数据采集已启动（间隔 {interval}s）")

    def _cleanup(self, keep_days: int = 30):
        cutoff = datetime.now() - timedelta(days=keep_days)
        cutoff_str = cutoff.strftime("%Y%m%d")
        removed = 0
        for fp in self.save_dir.glob("ths_fund_flow_*.json"):
            date_str = fp.stem.replace("ths_fund_flow_", "")
            if date_str <= cutoff_str:
                fp.unlink()
                removed += 1
        if removed:
            print(f"[同花顺] 已清理 {removed} 个过期数据文件（保留近 {keep_days} 天）")

    def stop(self):
        self.running = False


class FundFlowChart:

    def load_data(self, data_path: Path = None, source: str = "em") -> pd.DataFrame:
        if data_path is not None and data_path.suffix == ".csv":
            return pd.read_csv(data_path, parse_dates=["time"])
        today = datetime.now().strftime("%Y%m%d")
        if source == "ths":
            pattern = "ths_fund_flow_*.json"
            default_fp = DATA_DIR / THS_DATA_FILENAME.format(date=today)
        else:
            pattern = "fund_flow_*.json"
            default_fp = DATA_DIR / DATA_FILENAME.format(date=today)
        fp = data_path or default_fp
        if not fp.exists():
            today_files = sorted(fp.parent.glob(pattern))
            if not today_files:
                raise FileNotFoundError(f"数据文件不存在: {fp}")
            dfs = [pd.read_json(f, lines=True) for f in today_files]
            df = pd.concat(dfs, ignore_index=True)
        else:
            df = pd.read_json(fp, lines=True)
        df["time"] = pd.to_datetime(df["time"], format="%H:%M:%S")
        # 允许收盘后采集的数据（取 15:00 和实际最大时间的较大值）
        close_time = df["time"].iloc[0].replace(hour=MARKET_CLOSE.hour, minute=MARKET_CLOSE.minute, second=0)
        max_time = df["time"].max()
        cutoff = max(close_time, max_time)
        df = df[df["time"] <= cutoff]
        return df

    def generate(self, df: pd.DataFrame, mode: str = "delta",
                 top_n: int = TOP_N, bottom_n: int = BOTTOM_N,
                 output: str = None, log_scale: bool = False,
                 title_extra: str = "", source: str = "em") -> go.Figure:

        source_label = "同花顺" if source == "ths" else "东方财富"
        value_col = "delta" if mode == "delta" else "main_net_inflow"
        y_label = "增量净流入（亿）" if mode == "delta" else "累计净流入（亿）"
        title_suffix = "· 增量" if mode == "delta" else "· 累计"
        if log_scale:
            title_suffix += " (对数)"
        if title_extra:
            title_suffix += f" · {title_extra}"

        last_tick = df["time"].max()
        min_records = df["time"].nunique() * 0.5  # 至少覆盖 50% 时间点
        sector_counts = df.groupby("sector").size()
        valid_sectors = sector_counts[sector_counts >= min_records].index

        last_slice = df[(df["time"] == last_tick) & (df["sector"].isin(valid_sectors))]
        ranking = last_slice.groupby("sector")["main_net_inflow"].last().sort_values(ascending=False)

        # 净流入前 N 名
        top_sectors = ranking.head(top_n).index.tolist()
        # 净流出前 N 名（排名末尾，负值）
        bottom_sectors = ranking.tail(bottom_n).index.tolist() if bottom_n > 0 else []
        # 去重（防止板块极少时重叠）
        bottom_sectors = [s for s in bottom_sectors if s not in top_sectors]
        all_sectors = top_sectors + bottom_sectors

        df_top = df[df["sector"].isin(all_sectors)]

        # 始终显示 markers 保证 hover 可触发，数据多时用较小标记
        time_points = df["time"].nunique()
        marker_size = 6 if time_points <= 3 else 3

        fig = go.Figure()

        # --- 净流入板块（实线） ---
        for i, sector in enumerate(top_sectors):
            sub = df_top[df_top["sector"] == sector].sort_values("time")
            color = COLORS[i % len(COLORS)]
            fig.add_trace(go.Scatter(
                x=sub["time"],
                y=sub[value_col],
                mode="lines+markers",
                name=sector,
                line=dict(color=color, width=2),
                marker=dict(color=color, size=marker_size),
                hovertemplate=(
                    f"<b>{sector}</b><br>"
                    f"时间: %{{x|%H:%M:%S}}<br>"
                    f"{y_label}: %{{y:.2f}}亿<extra></extra>"
                ),
                hoverlabel=dict(namelength=-1, font_size=13),
            ))
            last_row = sub.iloc[-1]
            fig.add_annotation(
                x=1.01,
                y=last_row[value_col],
                xref="paper",
                yref="y",
                text=f"<b>{sector}</b> {fmt_value(last_row[value_col])}",
                showarrow=False,
                xanchor="left",
                yanchor="middle",
                font=dict(color=color, size=12, family="Microsoft YaHei, SimHei, sans-serif"),
                bgcolor="rgba(255,255,255,0.95)",
                bordercolor=color,
                borderwidth=1.5,
                borderpad=3,
            )

        # --- 净流出板块（虚线，灰冷色调） ---
        for i, sector in enumerate(bottom_sectors):
            sub = df_top[df_top["sector"] == sector].sort_values("time")
            color = OUTFLOW_COLORS[i % len(OUTFLOW_COLORS)]
            fig.add_trace(go.Scatter(
                x=sub["time"],
                y=sub[value_col],
                mode="lines+markers",
                name=sector,
                line=dict(color=color, width=2),
                marker=dict(color=color, size=marker_size, symbol="diamond"),
                hovertemplate=(
                    f"<b>{sector}</b><br>"
                    f"时间: %{{x|%H:%M:%S}}<br>"
                    f"{y_label}: %{{y:.2f}}亿<extra></extra>"
                ),
                hoverlabel=dict(namelength=-1, font_size=13),
            ))
            last_row = sub.iloc[-1]
            fig.add_annotation(
                x=1.01,
                y=last_row[value_col],
                xref="paper",
                yref="y",
                text=f"<b>{sector}</b> {fmt_value(last_row[value_col])}",
                showarrow=False,
                xanchor="left",
                yanchor="middle",
                font=dict(color=color, size=11, family="Microsoft YaHei, SimHei, sans-serif"),
                bgcolor="rgba(255,255,255,0.95)",
                bordercolor=color,
                borderwidth=1.5,
                borderpad=3,
            )

        fig.update_layout(
            title=dict(
                text=f"<b>{source_label} · 概念板块主力资金流向走势图 {title_suffix}</b>",
                font=dict(size=16, color="#333"),
                x=0.01,
                y=0.98,
                xanchor="left",
                yanchor="top",
            ),
            xaxis=dict(
                title="",
                type="date",
                tickformat="%H:%M",
                showgrid=False,
                zeroline=False,
                range=[
                    df_top["time"].iloc[0].replace(hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute, second=0),
                    df_top["time"].iloc[0].replace(hour=MARKET_CLOSE.hour, minute=MARKET_CLOSE.minute, second=0),
                ],
                rangebreaks=[
                    dict(bounds=[11.5, 13], pattern="hour"),
                ],
                tickfont=dict(size=11, color="#666"),
            ),
            yaxis=dict(
                title=dict(text=y_label, font=dict(size=12, color="#666")),
                type="log" if log_scale else "linear",
                showgrid=True,
                gridcolor="#f0f0f0",
                zeroline=True,
                zerolinecolor="#ccc",
                zerolinewidth=1,
                tickfont=dict(size=11, color="#666"),
            ),
            plot_bgcolor="#fafafa",
            paper_bgcolor="white",
            hovermode="x unified",
            legend=dict(
                orientation="h",
                yanchor="top",
                y=0.99,
                xanchor="left",
                x=0.01,
                font=dict(size=10),
                bgcolor="rgba(255,255,255,0.8)",
                bordercolor="#ddd",
                borderwidth=1,
            ),
            margin=dict(l=60, r=220, t=80, b=40),
        )

        fig.add_annotation(
            x=0.995, y=0.01,
            xref="paper", yref="paper",
            text=f"更新于 {datetime.now().strftime('%H:%M:%S')}",
            showarrow=False,
            font=dict(size=10, color="#999"),
        )

        if output:
            plot_html = fig.to_html(include_plotlyjs=False, full_html=False, div_id="chart",
                                    config={"responsive": True, "displayModeBar": True})
            Path(output).write_text(_wrap_html(plot_html), encoding="utf-8")
            print(f"图表已保存: {output}")
        return fig

    def generate_json(self, df: pd.DataFrame, mode: str = "delta",
                      top_n: int = TOP_N, bottom_n: int = BOTTOM_N,
                      log_scale: bool = False, title_extra: str = "",
                      source: str = "em") -> dict:
        """生成图表数据的 JSON 表示，供前端 JS 动态渲染。"""

        source_label = "同花顺" if source == "ths" else "东方财富"
        value_col = "delta" if mode == "delta" else "main_net_inflow"
        y_label = "增量净流入（亿）" if mode == "delta" else "累计净流入（亿）"
        title_suffix = "· 增量" if mode == "delta" else "· 累计"
        if log_scale:
            title_suffix += " (对数)"
        if title_extra:
            title_suffix += f" · {title_extra}"

        last_tick = df["time"].max()
        min_records = df["time"].nunique() * 0.5
        sector_counts = df.groupby("sector").size()
        valid_sectors = sector_counts[sector_counts >= min_records].index

        last_slice = df[(df["time"] == last_tick) & (df["sector"].isin(valid_sectors))]
        ranking = last_slice.groupby("sector")["main_net_inflow"].last().sort_values(ascending=False)

        top_sectors = ranking.head(top_n).index.tolist()
        bottom_sectors = ranking.tail(bottom_n).index.tolist() if bottom_n > 0 else []
        bottom_sectors = [s for s in bottom_sectors if s not in top_sectors]
        all_sectors = top_sectors + bottom_sectors

        df_top = df[df["sector"].isin(all_sectors)]
        time_points = df["time"].nunique()

        x_range_start = df_top["time"].iloc[0].replace(
            hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute, second=0
        ).strftime("%Y-%m-%d %H:%M:%S")
        x_range_end = df_top["time"].iloc[0].replace(
            hour=MARKET_CLOSE.hour, minute=MARKET_CLOSE.minute, second=0
        ).strftime("%Y-%m-%d %H:%M:%S")

        sectors_data = []

        # 净流入板块
        for i, sector in enumerate(top_sectors):
            sub = df_top[df_top["sector"] == sector].sort_values("time")
            sectors_data.append({
                "name": sector,
                "times": sub["time"].dt.strftime("%Y-%m-%d %H:%M:%S").tolist(),
                "values": sub[value_col].round(4).tolist(),
                "color": COLORS[i % len(COLORS)],
                "is_outflow": False,
                "y_label": y_label,
            })

        # 净流出板块
        for i, sector in enumerate(bottom_sectors):
            sub = df_top[df_top["sector"] == sector].sort_values("time")
            sectors_data.append({
                "name": sector,
                "times": sub["time"].dt.strftime("%Y-%m-%d %H:%M:%S").tolist(),
                "values": sub[value_col].round(4).tolist(),
                "color": OUTFLOW_COLORS[i % len(OUTFLOW_COLORS)],
                "is_outflow": True,
                "y_label": y_label,
            })

        return {
            "source_label": source_label,
            "y_label": y_label,
            "title_suffix": title_suffix,
            "log_scale": log_scale,
            "time_points": time_points,
            "x_range": [x_range_start, x_range_end],
            "updated_at": datetime.now().strftime("%H:%M:%S"),
            "sectors": sectors_data,
        }

    def show(self, df: pd.DataFrame, mode: str = "delta", top_n: int = TOP_N,
             bottom_n: int = BOTTOM_N, log_scale: bool = False):
        CHART_DIR.mkdir(parents=True, exist_ok=True)
        html_path = CHART_DIR / "fund_flow.html"
        fig = self.generate(df, mode=mode, top_n=top_n, bottom_n=bottom_n, log_scale=log_scale)
        plot_html = fig.to_html(include_plotlyjs=False, full_html=False, div_id="chart",
                                config={"responsive": True, "displayModeBar": True})
        html_path.write_text(_wrap_html(plot_html), encoding="utf-8")
        print(f"图表已保存: {html_path}")
        webbrowser.open(f"file://{html_path}")

    def show_dual(self, em_df: pd.DataFrame, ths_df: pd.DataFrame,
                  mode: str = "delta", top_n: int = TOP_N, bottom_n: int = BOTTOM_N,
                  log_scale: bool = False):
        CHART_DIR.mkdir(parents=True, exist_ok=True)
        html_path = CHART_DIR / "fund_flow.html"
        html = self._build_dual_html(em_df, ths_df, mode=mode, top_n=top_n,
                                     bottom_n=bottom_n, log_scale=log_scale)
        html_path.write_text(html, encoding="utf-8")
        print(f"双 Tab 图表已保存: {html_path}")
        webbrowser.open(f"file://{html_path}")

    def _build_dual_html(self, em_df: pd.DataFrame = None, ths_df: pd.DataFrame = None,
                         mode: str = "delta", top_n: int = TOP_N, bottom_n: int = BOTTOM_N,
                         log_scale: bool = False,
                         refresh_seconds: int = None, title_extra: str = "") -> str:
        plot_config = {"responsive": True, "displayModeBar": True}

        em_chart = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#999;">暂无东方财富数据</div>'
        ths_chart = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#999;">暂无同花顺数据</div>'

        if em_df is not None and len(em_df) > 0:
            em_fig = self.generate(em_df, mode=mode, top_n=top_n, bottom_n=bottom_n,
                                   log_scale=log_scale, source="em", title_extra=title_extra)
            em_chart = em_fig.to_html(include_plotlyjs=False, full_html=False,
                                      div_id="chart-em", config=plot_config)

        if ths_df is not None and len(ths_df) > 0:
            ths_fig = self.generate(ths_df, mode=mode, top_n=top_n, bottom_n=bottom_n,
                                    log_scale=log_scale, source="ths", title_extra=title_extra)
            ths_chart = ths_fig.to_html(include_plotlyjs=False, full_html=False,
                                        div_id="chart-ths", config=plot_config)

        return _wrap_dual_html(em_chart, ths_chart, refresh_seconds=refresh_seconds)


class _ChartHandler(http.server.SimpleHTTPRequestHandler):
    data_gen = None       # 返回 dict 的回调
    ajax_html = ""        # AJAX 模板 HTML
    refresh_seconds = 60
    _cache = {"json": None, "data_mtime": 0}
    _cache_lock = threading.Lock()

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            # 返回 AJAX 模板（静态 HTML，数据由 JS 轮询获取）
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(self.ajax_html.encode("utf-8"))

        elif self.path == "/api/data":
            try:
                data = self._get_cached_data()
                body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8"))

        elif self.path == "/api/health":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            super().do_GET()

    def _get_cached_data(self):
        today = datetime.now().strftime("%Y%m%d")
        em_fp = DATA_DIR / DATA_FILENAME.format(date=today)
        ths_fp = DATA_DIR / THS_DATA_FILENAME.format(date=today)
        mtime = max(
            em_fp.stat().st_mtime if em_fp.exists() else 0,
            ths_fp.stat().st_mtime if ths_fp.exists() else 0,
        )
        if mtime == 0:
            raise FileNotFoundError("暂无数据")
        with self._cache_lock:
            if self._cache["json"] and self._cache["data_mtime"] == mtime:
                return self._cache["json"]
        data = self.data_gen()
        with self._cache_lock:
            self._cache["json"] = data
            self._cache["data_mtime"] = mtime
        return data

    def log_message(self, format, *args):
        pass  # 静默日志，减少干扰


def _make_handler(data_gen, ajax_html, refresh_seconds=60):
    cls = type("Handler", (_ChartHandler,), {
        "data_gen": staticmethod(data_gen),
        "ajax_html": ajax_html,
        "refresh_seconds": refresh_seconds,
    })
    return cls


def _build_ajax_html(refresh_seconds: int = 60) -> str:
    """生成 AJAX 模板 HTML，注入配置参数。"""
    return (
        AJAX_TEMPLATE
        .replace("__POLL_MS__", str(refresh_seconds * 1000))
        .replace("__COLORS_JSON__", json.dumps(COLORS))
        .replace("__OUTFLOW_JSON__", json.dumps(OUTFLOW_COLORS))
    )


def _start_http_server(data_gen, port: int = HTTP_PORT, refresh_seconds: int = 60):
    ajax_html = _build_ajax_html(refresh_seconds)
    handler = _make_handler(data_gen, ajax_html, refresh_seconds)
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    socketserver.ThreadingTCPServer.daemon_threads = True
    with socketserver.ThreadingTCPServer(("", port), handler) as httpd:
        print(f"图表服务: http://localhost:{port}")
        httpd.serve_forever()


def _load_today_data(chart: FundFlowChart, source: str = "em"):
    """加载当日数据，不存在则返回 None。"""
    try:
        return chart.load_data(source=source)
    except FileNotFoundError:
        return None


def main():
    parser = argparse.ArgumentParser(description="资金流向实时走势图")
    parser.add_argument("--once", action="store_true", help="单次采集并生成图表")
    parser.add_argument("--collect", action="store_true", help="仅后台采集数据")
    parser.add_argument("--chart", action="store_true", help="仅从已有数据生成图表")
    parser.add_argument("--cleanup", action="store_true", help="仅清理过期数据文件")
    parser.add_argument("--csv", type=str, help="从 CSV 文件加载数据生成图表")
    parser.add_argument("--live", action="store_true", help="（默认行为）采集 + HTTP 实时图表")
    parser.add_argument("--port", type=int, default=HTTP_PORT, help=f"HTTP 端口 (默认 {HTTP_PORT})")
    parser.add_argument("--interval", type=int, default=COLLECT_INTERVAL,
                        help=f"采集间隔秒数 (默认 {COLLECT_INTERVAL})")
    parser.add_argument("--keep-days", type=int, default=30,
                        help="数据保留天数 (默认 30)")
    parser.add_argument("--top", type=int, default=TOP_N, help=f"显示净流入板块数 (默认 {TOP_N})")
    parser.add_argument("--bottom", type=int, default=BOTTOM_N, help=f"显示净流出板块数 (默认 {BOTTOM_N})")
    parser.add_argument("--mode", choices=["delta", "cumulative"], default="cumulative",
                        help="显示模式: cumulative=累计 (默认), delta=增量")
    parser.add_argument("--log", action="store_true", help="Y 轴使用对数坐标")
    parser.add_argument("--timeout", type=int, default=0,
                        help="采集 N 秒后自动退出 (0=手动 Ctrl+C)")
    args = parser.parse_args()

    collector = FundFlowCollector()
    ths_collector = THSFundFlowCollector() if HAS_THS_AUTH else None

    if args.cleanup:
        collector._cleanup(keep_days=args.keep_days)
        if ths_collector:
            ths_collector._cleanup(keep_days=args.keep_days)
        return

    if args.once:
        print("单次采集...")
        collector.snapshot()
        if ths_collector:
            ths_collector.snapshot()

        chart = FundFlowChart()
        em_df = _load_today_data(chart, source="em")
        ths_df = _load_today_data(chart, source="ths") if ths_collector else None

        if em_df is not None and ths_df is not None:
            chart.show_dual(em_df, ths_df, mode=args.mode, top_n=args.top,
                            bottom_n=args.bottom, log_scale=args.log)
        elif em_df is not None:
            chart.show(em_df, mode=args.mode, top_n=args.top,
                       bottom_n=args.bottom, log_scale=args.log)
        else:
            print("无数据可显示")
        return

    if args.collect:
        print("后台采集模式，按 Ctrl+C 停止")
        collector.start(interval=args.interval)
        if ths_collector:
            ths_collector.start(interval=args.interval)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            collector.stop()
            if ths_collector:
                ths_collector.stop()
        return

    if args.chart:
        chart = FundFlowChart()
        try:
            data_path = Path(args.csv) if args.csv else None
            em_df = chart.load_data(data_path, source="em") if not args.csv else chart.load_data(data_path)
            ths_df = _load_today_data(chart, source="ths") if not args.csv else None
            if ths_df is not None:
                chart.show_dual(em_df, ths_df, mode=args.mode, top_n=args.top,
                                bottom_n=args.bottom, log_scale=args.log)
            else:
                chart.show(em_df, mode=args.mode, top_n=args.top,
                           bottom_n=args.bottom, log_scale=args.log)
        except FileNotFoundError as e:
            print(e)
            sys.exit(1)
        return

    # 默认模式：实时采集 + HTTP 服务
    collector.start(interval=args.interval)
    if ths_collector:
        ths_collector.start(interval=args.interval)
    chart = FundFlowChart()

    print("=" * 60)
    print("  资金流向实时走势图（东方财富 + 同花顺）")
    print(f"  累计模式 · 净流入前 {args.top} + 净流出前 {args.bottom} · {args.interval}s 采集")
    print(f"  图表服务: http://localhost:{args.port}")
    print("  Ctrl+C 停止")
    print("=" * 60)

    def gen_data():
        session = market_session()
        title_extra = {
            "lunch": "上午半场总结",
            "closed": "当日总结",
        }.get(session, "")
        em_df = _load_today_data(chart, source="em")
        ths_df = _load_today_data(chart, source="ths") if ths_collector else None
        if em_df is None and ths_df is None:
            raise FileNotFoundError("暂无数据")
        result = {}
        if em_df is not None:
            result["em"] = chart.generate_json(em_df, mode=args.mode, top_n=args.top,
                                               bottom_n=args.bottom, log_scale=args.log,
                                               title_extra=title_extra, source="em")
        if ths_df is not None:
            result["ths"] = chart.generate_json(ths_df, mode=args.mode, top_n=args.top,
                                                bottom_n=args.bottom, log_scale=args.log,
                                                title_extra=title_extra, source="ths")
        return result

    try:
        _start_http_server(gen_data, port=args.port, refresh_seconds=args.interval)
    except KeyboardInterrupt:
        collector.stop()
        if ths_collector:
            ths_collector.stop()
    print("\n已退出")


if __name__ == "__main__":
    main()