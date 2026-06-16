#!/usr/bin/env python3
"""
资金流向实时走势图
实时采集板块资金流数据，绘制多板块资金净流入走势图。
用法:
  python fund_flow.py              # 实时采集+浏览器打开图表
  python fund_flow.py --once       # 单次采集生成静态图表
  python fund_flow.py --collect    # 仅后台采集数据
  python fund_flow.py --chart      # 仅从已有数据生成图表
  python fund_flow.py --live       # 采集 + HTTP 实时图表
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


def _wrap_html(plot_html: str, refresh_seconds: int = None) -> str:
    refresh = f'<meta http-equiv="refresh" content="{refresh_seconds}">' if refresh_seconds else ""
    return HTML_TEMPLATE.format(refresh=refresh, plot_html=plot_html)
MARKET_OPEN = dt_time(9, 30)
MARKET_CLOSE = dt_time(15, 0)
TOP_N = 10
HTTP_PORT = 8899
DATA_FILENAME = "fund_flow_{date}.json"

COLORS = [
    "#e74c3c", "#2ecc71", "#3498db", "#f39c12", "#9b59b6",
    "#1abc9c", "#e67e22", "#34495e", "#c0392b", "#16a085",
    "#2980b9", "#8e44ad", "#27ae60", "#d35400", "#2c3e50",
    "#e91e63", "#00bcd4", "#ff5722", "#795548", "#607d8b",
]


def is_trading_day() -> bool:
    return datetime.now().weekday() < 5


def is_market_open() -> bool:
    now = datetime.now().time()
    return MARKET_OPEN <= now <= MARKET_CLOSE


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

    def _parse(self, raw: list[dict]) -> list[dict]:
        now = datetime.now()
        rows = []
        for item in raw:
            name = item.get("f14", "")
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

        def _loop():
            while self.running:
                try:
                    if is_trading_day() and is_market_open():
                        self.snapshot()
                    else:
                        print(f"\r[{datetime.now().strftime('%H:%M:%S')}] 非交易时间，等待中...", end="")
                except Exception as e:
                    print(f"\n采集错误: {e}")
                time.sleep(interval)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()
        print(f"数据采集已启动（间隔 {interval}s）")

    def stop(self):
        self.running = False


class FundFlowChart:

    def load_data(self, data_path: Path = None) -> pd.DataFrame:
        if data_path is not None and data_path.suffix == ".csv":
            return pd.read_csv(data_path, parse_dates=["time"])
        today = datetime.now().strftime("%Y%m%d")
        fp = (data_path or (DATA_DIR / DATA_FILENAME.format(date=today)))
        if not fp.exists():
            today_files = sorted(fp.parent.glob("fund_flow_*.json"))
            if not today_files:
                raise FileNotFoundError(f"数据文件不存在: {fp}")
            dfs = [pd.read_json(f, lines=True) for f in today_files]
            df = pd.concat(dfs, ignore_index=True)
        else:
            df = pd.read_json(fp, lines=True)
        df["time"] = pd.to_datetime(df["time"], format="%H:%M:%S")
        close_time = df["time"].iloc[0].replace(hour=MARKET_CLOSE.hour, minute=MARKET_CLOSE.minute, second=0)
        df = df[df["time"] <= close_time]
        return df

    def generate(self, df: pd.DataFrame, mode: str = "delta",
                 top_n: int = TOP_N, output: str = None, log_scale: bool = False) -> go.Figure:

        value_col = "delta" if mode == "delta" else "main_net_inflow"
        y_label = "增量净流入（亿）" if mode == "delta" else "累计净流入（亿）"
        title_suffix = "· 增量" if mode == "delta" else "· 累计"
        if log_scale:
            title_suffix += " (对数)"

        last_tick = df["time"].max()
        min_records = df["time"].nunique() * 0.5  # 至少覆盖 50% 时间点
        sector_counts = df.groupby("sector").size()
        valid_sectors = sector_counts[sector_counts >= min_records].index

        last_slice = df[(df["time"] == last_tick) & (df["sector"].isin(valid_sectors))]
        top_sectors = (
            last_slice.groupby("sector")["main_net_inflow"]
            .last()
            .sort_values(ascending=False)
            .head(top_n)
            .index.tolist()
        )
        df_top = df[df["sector"].isin(top_sectors)]

        fig = go.Figure()
        for i, sector in enumerate(top_sectors):
            sub = df_top[df_top["sector"] == sector].sort_values("time")
            color = COLORS[i % len(COLORS)]
            fig.add_trace(go.Scatter(
                x=sub["time"],
                y=sub[value_col],
                mode="lines",
                name=sector,
                line=dict(color=color, width=2),
                hovertemplate=(
                    f"<b>{sector}</b><br>"
                    f"时间: %{{x|%H:%M:%S}}<br>"
                    f"{y_label}: %{{y:.2f}}亿<extra></extra>"
                ),
            ))
            last_row = sub.iloc[-1]
            label_x = last_row["time"] + timedelta(seconds=30)
            fig.add_annotation(
                x=label_x,
                y=last_row[value_col],
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

        fig.update_layout(
            title=dict(
                text=f"<b>概念板块主力资金流向走势图 {title_suffix}</b>",
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

    def show(self, df: pd.DataFrame, mode: str = "delta", top_n: int = TOP_N, log_scale: bool = False):
        CHART_DIR.mkdir(parents=True, exist_ok=True)
        html_path = CHART_DIR / "fund_flow.html"
        fig = self.generate(df, mode=mode, top_n=top_n, log_scale=log_scale)
        plot_html = fig.to_html(include_plotlyjs=False, full_html=False, div_id="chart",
                                config={"responsive": True, "displayModeBar": True})
        html_path.write_text(_wrap_html(plot_html), encoding="utf-8")
        print(f"图表已保存: {html_path}")
        webbrowser.open(f"file://{html_path}")


class _ChartHandler(http.server.SimpleHTTPRequestHandler):
    chart_gen = None

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                fig = self.chart_gen()
                plot_html = fig.to_html(
                    include_plotlyjs="cdn",
                    full_html=False,
                    div_id="chart",
                    config={"responsive": True, "displayModeBar": True},
                )
                html = _wrap_html(plot_html, refresh_seconds=COLLECT_INTERVAL)
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f"生成图表失败: {e}".encode())
        else:
            super().do_GET()


def _make_handler(chart_gen):
    cls = type("Handler", (_ChartHandler,), {"chart_gen": staticmethod(chart_gen)})
    return cls


def _start_http_server(chart_gen, port: int = HTTP_PORT):
    handler = _make_handler(chart_gen)
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", port), handler) as httpd:
        print(f"图表服务: http://localhost:{port}")
        httpd.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="资金流向实时走势图")
    parser.add_argument("--once", action="store_true", help="单次采集并生成图表")
    parser.add_argument("--collect", action="store_true", help="仅后台采集数据")
    parser.add_argument("--chart", action="store_true", help="仅从已有数据生成图表")
    parser.add_argument("--csv", type=str, help="从 CSV 文件加载数据生成图表")
    parser.add_argument("--live", action="store_true", help="采集 + HTTP 实时图表")
    parser.add_argument("--port", type=int, default=HTTP_PORT, help=f"HTTP 端口 (默认 {HTTP_PORT})")
    parser.add_argument("--interval", type=int, default=COLLECT_INTERVAL,
                        help=f"采集间隔秒数 (默认 {COLLECT_INTERVAL})")
    parser.add_argument("--top", type=int, default=TOP_N, help=f"显示板块数 (默认 {TOP_N})")
    parser.add_argument("--mode", choices=["delta", "cumulative"], default="cumulative",
                        help="显示模式: cumulative=累计 (默认), delta=增量")
    parser.add_argument("--log", action="store_true", help="Y 轴使用对数坐标")
    parser.add_argument("--timeout", type=int, default=0,
                        help="采集 N 秒后自动退出 (0=手动 Ctrl+C)")
    args = parser.parse_args()

    collector = FundFlowCollector()

    if args.once:
        print("单次采集...")
        collector.snapshot()
        today = datetime.now().strftime("%Y%m%d")
        fp = DATA_DIR / DATA_FILENAME.format(date=today)
        chart = FundFlowChart()
        df = chart.load_data(fp)
        chart.show(df, mode=args.mode, top_n=args.top, log_scale=args.log)
        return

    if args.collect:
        print("后台采集模式，按 Ctrl+C 停止")
        collector.start(interval=args.interval)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            collector.stop()
        return

    if args.chart:
        chart = FundFlowChart()
        try:
            data_path = Path(args.csv) if args.csv else None
            df = chart.load_data(data_path)
            chart.show(df, mode=args.mode, top_n=args.top, log_scale=args.log)
        except FileNotFoundError as e:
            print(e)
            sys.exit(1)
        return

    if args.live:
        collector.start(interval=args.interval)
        chart = FundFlowChart()

        def gen_chart():
            today = datetime.now().strftime("%Y%m%d")
            fp = DATA_DIR / DATA_FILENAME.format(date=today)
            if not fp.exists():
                raise FileNotFoundError("暂无数据")
            df = chart.load_data(fp)
            return chart.generate(df, mode=args.mode, top_n=args.top, log_scale=args.log)

        try:
            _start_http_server(gen_chart, port=args.port)
        except KeyboardInterrupt:
            collector.stop()
        return

    print("=" * 60)
    print("  资金流向实时走势图")
    print("  Ctrl+C 停止")
    print("=" * 60)

    collector.start(interval=args.interval)
    print("等待首次数据采集...")
    time.sleep(3)
    print("首次采集完成，打开图表...\n")

    chart = FundFlowChart()

    if args.timeout > 0:
        print(f"将运行 {args.timeout} 秒后自动退出")

    start_time = time.time()

    try:
        while True:
            if args.timeout > 0 and (time.time() - start_time) > args.timeout:
                print("\n达到设定时长，退出")
                break

            try:
                today = datetime.now().strftime("%Y%m%d")
                fp = DATA_DIR / DATA_FILENAME.format(date=today)
                if fp.exists():
                    df = chart.load_data(fp)
                    html_path = str(CHART_DIR / "fund_flow.html")
                    fig = chart.generate(df, mode=args.mode, top_n=args.top, log_scale=args.log)
                    plot_html = fig.to_html(include_plotlyjs="cdn", full_html=False, div_id="chart",
                                            config={"responsive": True, "displayModeBar": True})
                    Path(html_path).write_text(_wrap_html(plot_html), encoding="utf-8")
                    if len(df) > 0:
                        latest = df["time"].max()
                        sectors = len(df["sector"].unique())
                        print(f"\r图表已更新 [{latest.strftime('%H:%M:%S')}] 已追踪 {sectors} 个板块", end="")
            except Exception as e:
                print(f"\n刷新图表出错: {e}")

            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        collector.stop()
        print("\n已退出")


if __name__ == "__main__":
    main()