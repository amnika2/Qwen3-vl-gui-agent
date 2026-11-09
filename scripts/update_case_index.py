import csv
import json
from pathlib import Path

import yaml


def main():
    root = Path(__file__).resolve().parents[1]
    path = root / "cases/index.csv"
    rows = list(csv.DictReader(path.open()))
    analysis = yaml.safe_load((root / "configs/analysis.yaml").read_text())
    calibrations = {}
    for name in analysis["calibrations"]:
        matrix = json.loads((root / "runs" / name / "matrix.json").read_text())
        if not matrix["passed"]:
            raise ValueError(f"{name}: calibration failed")
        calibrations[matrix["cells"][0]["case_id"]] = name
    specifications = {
        "sweeper-005": ("Create localized-price product", "Fresh company and empty item catalog", "PHP 8.3, MariaDB, Composer, compiled Vue assets", "Saved item ID and list sale price", "none", ""),
        "sweeper-040": ("Persist provider mobile/phone", "Admin and Jane Doe synthetic provider", "PHP 8.3, MariaDB, Composer, compiled assets", "Same provider ID and post-save field", "none", ""),
        "sweeper-041": ("Persist writer/admin nickname", "Separate writer/admin fixtures", "PHP 8.3 and MariaDB", "Same account and reopened nickname", "two roles in separate runs", ""),
        "sweeper-007": ("Rename Smart Playlist", "Admin and private smart playlist ID 20", "PHP 8.3, MariaDB, Composer, Vite assets", "Same playlist ID and reopened name", "none", ""),
        "sweeper-008": ("Edit custom site variable", "Admin and custom_greeting=Hello Anchor", "PHP 7.4, MariaDB, normalized Composer metadata", "Same variable key and reopened value", "none", ""),
    }
    fields = ["business_action", "preconditions", "dependencies", "visible_evidence", "accounts_upload", "pending_reason"]
    for row in rows:
        row.update(dict(zip(fields, specifications[row["case_id"]])))
        row["calibration"] = calibrations.get(row["case_id"], "")
        row["status"] = "calibrated" if row["calibration"] else "candidate"
    with path.open("w") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    lines = ["# M0：真实应用校准", "", "| 应用 | case | 缺陷 target | 修复 target | 两版 preservation | 证据 |",
             "|---|---|---|---|---|---|"]
    total = 0
    for row in rows:
        if not row["calibration"]:
            continue
        matrix = json.loads((root / "runs" / row["calibration"] / "matrix.json").read_text())["cells"]
        count = lambda build, workflow: sum(cell["build"] == build and cell["workflow"] == workflow for cell in matrix)
        total += len(matrix)
        a, b = count("A", "target"), count("B", "target")
        normal = count("A", "preservation") + count("B", "preservation")
        name = row["calibration"]
        lines.append(f"| {row['slug']} | {row['case_id']} | fail {a}/{a} | pass {b}/{b} | pass {normal}/{normal} | [{name}]({name}/summary.md) |")
    lines += ["", f"共 {total} 次独立运行，{total}/{total} 符合预期矩阵。每格两次，每次从新数据库与浏览器会话开始。",
              "这是确定性 Playwright 脚本的环境与独立判据校准；模型实验见 [正式报告](summary.md)。",
              "开发：emlog、Akaunting；留出：Easy!Appointments、Ampache、Anchor CMS。",
              "历史部署失败与中断记录保留在其他 calibration-* 批次。"]
    (root / "reports/milestone-M0.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
