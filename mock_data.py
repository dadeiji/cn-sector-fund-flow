#!/usr/bin/env python3
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

random.seed(42)

SECTORS = [
    "半导体", "人工智能", "新能源汽车", "光伏概念", "军工",
    "5G概念", "消费电子", "创新药", "机器人概念", "芯片概念",
    "锂电池", "储能", "元宇宙", "数字经济", "信创",
    "碳中和", "特高压", "工业母机", "东数西算", "数据要素",
]

BASE_INFLOWS = {
    "半导体": 520.0, "人工智能": 480.0, "新能源汽车": 650.0,
    "光伏概念": 380.0, "军工": 420.0, "5G概念": 560.0,
    "消费电子": 440.0, "创新药": 310.0, "机器人概念": 350.0,
    "芯片概念": 290.0, "锂电池": 600.0, "储能": 270.0,
    "元宇宙": 180.0, "数字经济": 400.0, "信创": 220.0,
    "碳中和": 340.0, "特高压": 160.0, "工业母机": 130.0,
    "东数西算": 200.0, "数据要素": 250.0,
}

BASE_INDEX = {
    "半导体": 5800, "人工智能": 4200, "新能源汽车": 5100,
    "光伏概念": 3600, "军工": 4500, "5G概念": 6900,
    "消费电子": 4800, "创新药": 3200, "机器人概念": 3800,
    "芯片概念": 3500, "锂电池": 5500, "储能": 3000,
    "元宇宙": 2500, "数字经济": 4100, "信创": 2800,
    "碳中和": 3700, "特高压": 2200, "工业母机": 1900,
    "东数西算": 2600, "数据要素": 2900,
}


def generate_mock_data(output_path: Path, start_hour: int = 9, start_min: int = 30,
                       end_hour: int = 15, end_min: int = 0, interval: int = 60):
    output_path.parent.mkdir(parents=True, exist_ok=True)

    values = {s: BASE_INFLOWS[s] for s in SECTORS}
    prev_values = {s: None for s in SECTORS}

    now = datetime.now().replace(hour=start_hour, minute=start_min, second=0, microsecond=0)
    end = datetime.now().replace(hour=end_hour, minute=end_min, second=0, microsecond=0)

    records = []
    while now <= end:
        for sector in SECTORS:
            drift = random.uniform(-0.5, 3.0)
            values[sector] += drift
            values[sector] = max(0, values[sector])

            delta = values[sector] - prev_values[sector] if prev_values[sector] is not None else None
            prev_values[sector] = values[sector]

            change_pct = random.uniform(-3.0, 5.0)
            index_value = BASE_INDEX[sector] * (1 + change_pct / 100)

            records.append({
                "time": now.strftime("%H:%M:%S"),
                "sector": sector,
                "main_net_inflow": round(values[sector], 2),
                "delta": round(delta, 2) if delta is not None else None,
                "change_pct": round(change_pct, 2),
                "index_value": round(index_value, 2),
            })

        now += timedelta(seconds=interval)

    with open(output_path, "w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"生成 {len(records)} 条记录 → {output_path}")
    print(f"  板块数: {len(SECTORS)}，时间点数: {len(records) // len(SECTORS)}")


if __name__ == "__main__":
    today = datetime.now().strftime("%Y%m%d")
    out = Path(__file__).parent / "data" / f"fund_flow_{today}.json"
    generate_mock_data(out)