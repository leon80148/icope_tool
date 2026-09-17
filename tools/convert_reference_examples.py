"""一次性工具：把參考專案的嘉義市社區資源 JSON 轉成本工具的設定包 JSON。

用法：python tools/convert_reference_examples.py <community-resources.json> examples/chiayi-city-resources.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from icope_tool.domains import DOMAIN_BY_EXCEL_CODE
from icope_tool.models import Resource
from icope_tool.services.importer import split_phones
from icope_tool.services.pack import PACK_FORMAT, PACK_VERSION, PackManifest

# 盤點表「類別」欄只有部分資源標了 A–F 次類別。以下依參考專案的手動對應
# （巷弄長照站 → 認知／行動／營養／憂鬱／社會；輔具 → 行動）補上示範用的預設適用項目。
CATEGORY_DEFAULTS = {
    "巷弄長照站(社照C據點)": ["cognitive", "mobility", "nutrition", "depression", "social"],
    "巷弄長照站(醫事C據點)": ["cognitive", "mobility", "nutrition", "depression", "social"],
    "高齡友善社區藥局": ["medication"],
    "社區關懷訪視": ["social", "depression"],
    "輔具租賃、購買": ["mobility"],
    "免費輪椅租借(輪椅趴趴GO)": ["mobility"],
    "家庭照顧者支持服務據點": ["social"],
    "社福中心": ["social"],
    "樂齡學習中心": ["cognitive", "social"],
    "社區大學": ["social"],
    "失智共照中心": ["cognitive"],
    "長青綜合服務": ["social"],
    "心理諮商中心": ["depression"],
    "身心障礙福利服務中心": ["social"],
    "長期照顧管理中心": ["social"],
}


def main(src: str, dest: str) -> None:
    data = json.loads(Path(src).read_text(encoding="utf-8"))
    resources = []
    for item in data["resources"]:
        code = str(item.get("classCode", "")).upper()
        resources.append(Resource(
            name=item["name"], type=item.get("category", ""),
            domains=[DOMAIN_BY_EXCEL_CODE[c].id for c in code if c in DOMAIN_BY_EXCEL_CODE]
            + CATEGORY_DEFAULTS.get(item.get("category", ""), []),
            phones=split_phones(item.get("phone")), address=item.get("address", ""),
            website=item.get("website", ""),
        ))
    manifest = PackManifest(format=PACK_FORMAT, version=PACK_VERSION, exported_at="2026-09-17T00:00:00",
                            app_version="example", resources=resources)
    payload = manifest.model_dump(mode="json")
    payload["source"] = (f"嘉義市社區資源盤點表（{data.get('source', '')}），公開資料，僅供示範；"
                         "適用項目為示範用預設對應，請依實際情況調整")
    Path(dest).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(resources)} resources -> {dest}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
