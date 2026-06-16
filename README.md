# 📈 Stock AI — 概念板块主力资金流向走势图

实时采集 A 股概念板块主力资金流向数据，自动生成交互式 Plotly 走势图，支持浏览器查看与 HTTP 实时推送。

## 功能特性

- **实时数据采集** — 基于东方财富 API，每分钟抓取概念板块主力净流入数据
- **交互式图表** — Plotly 生成的可缩放、可悬停的多板块资金走势图
- **双模式切换** — 增量模式（观察资金节奏与转折）/ 累计模式（看全天总结果）
- **智能交易时段识别** — 自动识别交易时段（9:30–11:30 / 13:00–15:00），午休与非交易时间自动暂停
- **自动数据清理** — 启动时自动清理 30 天前的历史数据文件
- **HTTP 实时服务** — 内置 HTTP 服务器，页面自动刷新，适合挂屏监控

## 快速开始

### 环境要求

- Python 3.10+

### 安装依赖

```bash
pip install -r requirements.txt
```

### 运行

```bash
# 默认模式：采集 + 浏览器自动打开图表，定时刷新
python fund_flow.py

# 单次采集并生成图表
python fund_flow.py --once

# 仅后台采集数据（不生成图表）
python fund_flow.py --collect

# 仅从已有数据生成图表
python fund_flow.py --chart

# 采集 + 启动 HTTP 实时图表服务
python fund_flow.py --live
```

## 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--once` | 单次采集并生成图表 | — |
| `--collect` | 仅后台采集数据 | — |
| `--chart` | 仅从已有数据生成图表 | — |
| `--live` | 采集 + HTTP 实时图表服务 | — |
| `--cleanup` | 仅清理过期数据文件 | — |
| `--csv <file>` | 从 CSV 文件加载数据生成图表 | — |
| `--interval <N>` | 采集间隔（秒） | `60` |
| `--top <N>` | 显示板块数量 | `10` |
| `--mode <delta\|cumulative>` | 显示模式 | `cumulative` |
| `--log` | Y 轴使用对数坐标 | `false` |
| `--port <N>` | HTTP 服务端口 | `8899` |
| `--timeout <N>` | 运行 N 秒后自动退出（0=手动退出） | `0` |
| `--keep-days <N>` | 数据保留天数 | `30` |

## 数据说明

采集数据存储在 `data/` 目录下，按日期命名：

```
data/fund_flow_20260616.json
```

格式为 JSON Lines，每行一条记录：

```json
{
  "time": "10:30:00",
  "sector": "半导体",
  "main_net_inflow": 5.23,
  "delta": 0.45,
  "change_pct": null,
  "index_value": null
}
```

| 字段 | 说明 |
|------|------|
| `time` | 采集时间 |
| `sector` | 板块名称 |
| `main_net_inflow` | 主力净流入累计值（亿元） |
| `delta` | 本周期净流入增量（亿元） |

## 生成的图表

图表输出到 `charts/fund_flow.html`，可在浏览器中直接打开。`--live` 模式下通过 `http://localhost:8899` 访问，页面每 60 秒自动刷新。

## 模拟数据

项目附带 `mock_data.py`，可生成模拟数据用于测试：

```bash
python mock_data.py
```

将在 `data/` 目录下生成当天的模拟数据文件。

## 项目结构

```
stock-ai/
├── fund_flow.py        # 主程序（采集 + 图表 + HTTP 服务）
├── mock_data.py        # 模拟数据生成器
├── requirements.txt    # Python 依赖
├── data/               # 采集数据存储目录
└── charts/             # 图表输出目录
```

## License

MIT
