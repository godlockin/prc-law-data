#!/usr/bin/env python3
"""
import.py — prc-law-data 数据导入器

从上游 3 个来源导入 + 标准化到 prc-law-data schema:
- laws-data (13098806890/laws-data) — 主源 (法条结构化)
- HF parquet (twang2218/chinese-law-and-regulations) — 补充索引
- LawRefBook (LawRefBook/Laws) — 司法解释/行政法规补充

用法:
    python3 import.py --source laws-data --cache-dir /tmp
    python3 import.py --source hf --cache-dir /tmp
    python3 import.py --source lawrefbook --cache-dir /tmp
    python3 import.py --source all --cache-dir /tmp

输出:
    data/statutes/<slug>.json    # 单部法律
    data/index/laws.jsonl        # 索引
    data/index/articles.jsonl    # 法条反向索引
    data/index/slug-map.json     # 中文名 → slug
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
STATUTES_DIR = DATA_DIR / "statutes"
INDEX_DIR = DATA_DIR / "index"

# 法规类型常量
LAW_TYPES = {
    "法律": "法律",
    "宪法": "宪法",
    "行政法规": "行政法规",
    "部门规章": "部门规章",
    "地方性法规": "地方性法规",
    "司法解释": "司法解释",
    "监察法规": "监察法规",
}

# slug 命名 (核心法律)
SLUG_MAP = {
    "中华人民共和国民法典": "civil-code",
    "中华人民共和国刑法": "criminal-law",
    "中华人民共和国刑事诉讼法": "criminal-procedure-law",
    "中华人民共和国民事诉讼法": "civil-procedure-law",
    "中华人民共和国行政诉讼法": "administrative-procedure-law",
    "中华人民共和国公司法": "company-law",
    "中华人民共和国证券法": "securities-law",
    "中华人民共和国合伙企业法": "partnership-law",
    "中华人民共和国企业破产法": "bankruptcy-law",
    "中华人民共和国劳动合同法": "labor-contract-law",
    "中华人民共和国劳动法": "labor-law",
    "中华人民共和国社会保险法": "social-insurance-law",
    "中华人民共和国数据安全法": "data-security-law",
    "中华人民共和国网络安全法": "cyber-security-law",
    "中华人民共和国个人信息保护法": "personal-information-protection-law",
    "中华人民共和国电子商务法": "e-commerce-law",
    "中华人民共和国商标法": "trademark-law",
    "中华人民共和国专利法": "patent-law",
    "中华人民共和国著作权法": "copyright-law",
    "中华人民共和国反不正当竞争法": "anti-unfair-competition-law",
    "中华人民共和国反垄断法": "anti-monopoly-law",
    "中华人民共和国行政处罚法": "administrative-penalty-law",
    "中华人民共和国行政许可法": "administrative-license-law",
    "中华人民共和国行政复议法": "administrative-review-law",
    "中华人民共和国行政强制法": "administrative-coercion-law",
    "中华人民共和国消费者权益保护法": "consumer-rights-law",
    "中华人民共和国产品质量法": "product-quality-law",
    "中华人民共和国食品安全法": "food-safety-law",
    "中华人民共和国环境保护法": "environmental-protection-law",
    "中华人民共和国环境影响评价法": "eia-law",
    "中华人民共和国大气污染防治法": "air-pollution-law",
    "中华人民共和国税收征收管理法": "tax-collection-law",
    "中华人民共和国个人所得税法": "individual-income-tax-law",
    "中华人民共和国外商投资法": "foreign-investment-law",
    "中华人民共和国保险法": "insurance-law",
    "中华人民共和国商业银行法": "commercial-bank-law",
    "中华人民共和国反洗钱法": "anti-money-laundering-law",
    "中华人民共和国监察法": "supervision-law",
    "中华人民共和国未成年人保护法": "minor-protection-law",
    "中华人民共和国慈善法": "charity-law",
    "中华人民共和国电子签名法": "electronic-signature-law",
    "中华人民共和国个人独资企业法": "personal-solo-enterprise-law",
    "中华人民共和国道路交通安全法": "road-traffic-safety-law",
    "中华人民共和国档案法": "archives-law",
    "中华人民共和国统计法": "statistics-law",
    "中华人民共和国审计法": "audit-law",
    "中华人民共和国会计法": "accounting-law",
    "中华人民共和国预算法": "budget-law",
    "中华人民共和国测绘法": "surveying-mapping-law",
    "中华人民共和国出境入境管理法": "immigration-law",
    "中华人民共和国保守国家秘密法": "state-secrets-law",
    "中华人民共和国国家安全法": "national-security-law",
    "中华人民共和国反间谍法": "counter-espionage-law",
    "中华人民共和国国防法": "national-defense-law",
    "中华人民共和国兵役法": "military-service-law",
    "中华人民共和国人民防空法": "civil-air-defense-law",
    "中华人民共和国国防教育法": "defense-education-law",
    "中华人民共和国传染病防治法": "infectious-disease-law",
    "中华人民共和国药品管理法": "drug-administration-law",
    "中华人民共和国中医药法": "traditional-chinese-medicine-law",
    "中华人民共和国疫苗管理法": "vaccine-law",
    "中华人民共和国精神卫生法": "mental-health-law",
    "中华人民共和国基本医疗卫生与健康促进法": "basic-medical-health-law",
    "中华人民共和国母婴保健法": "maternal-child-health-law",
    "中华人民共和国红十字会法": "red-cross-law",
    "中华人民共和国体育法": "sports-law",
    "中华人民共和国教育法": "education-law",
    "中华人民共和国义务教育法": "compulsory-education-law",
    "中华人民共和国高等教育法": "higher-education-law",
    "中华人民共和国职业教育法": "vocational-education-law",
    "中华人民共和国民办教育促进法": "private-education-law",
    "中华人民共和国科学技术进步法": "science-progress-law",
    "中华人民共和国促进科技成果转化法": "tech-transfer-law",
    "中华人民共和国科学技术普及法": "science-popularization-law",
    "中华人民共和国广告法": "advertising-law",
    "中华人民共和国价格法": "price-law",
    "中华人民共和国计量法": "metrology-law",
    "中华人民共和国标准化法": "standardization-law",
    "中华人民共和国认证认可条例": "certification-law",
    "中华人民共和国招标投标法": "bidding-law",
    "中华人民共和国政府采购法": "government-procurement-law",
    "中华人民共和国资产评估法": "asset-appraisal-law",
    "中华人民共和国银行业监督管理法": "banking-supervision-law",
    "中华人民共和国证券投资基金法": "fund-law",
    "中华人民共和国期货和衍生品法": "futures-law",
    "中华人民共和国信托法": "trust-law",
    "中华人民共和国票据法": "negotiable-instruments-law",
    "中华人民共和国对外贸易法": "foreign-trade-law",
    "中华人民共和国海关法": "customs-law",
    "中华人民共和国增值税法": "vat-law",
    "中华人民共和国印花税法法": "stamp-tax-law",
    "中华人民共和国车辆购置税法": "vehicle-purchase-tax-law",
    "中华人民共和国耕地占用税法": "farmland-occupation-tax-law",
    "中华人民共和国水污染防治法": "water-pollution-law",
    "中华人民共和国固体废物污染环境防治法": "solid-waste-law",
    "中华人民共和国环境噪声污染防治法": "noise-pollution-law",
    "中华人民共和国放射性污染防治法": "radiation-pollution-law",
    "中华人民共和国土壤污染防治法": "soil-pollution-law",
    "中华人民共和国清洁生产促进法": "clean-production-law",
    "中华人民共和国循环经济促进法": "circular-economy-law",
    "中华人民共和国节约能源法": "energy-conservation-law",
    "中华人民共和国可再生能源法": "renewable-energy-law",
    "中华人民共和国电力法": "electric-power-law",
    "中华人民共和国煤炭法": "coal-law",
    "中华人民共和国矿产资源法": "mineral-resources-law",
    "中华人民共和国水法": "water-law",
    "中华人民共和国防洪法": "flood-control-law",
    "中华人民共和国水土保持法": "soil-water-conservation-law",
    "中华人民共和国森林法": "forest-law",
    "中华人民共和国草原法": "grassland-law",
    "中华人民共和国渔业法": "fisheries-law",
    "中华人民共和国野生动物保护法": "wildlife-protection-law",
    "中华人民共和国海域使用管理法": "sea-area-law",
    "中华人民共和国安全生产法": "work-safety-law",
    "中华人民共和国突发事件应对法": "emergency-response-law",
    "中华人民共和国消防法": "fire-protection-law",
    "中华人民共和国建筑法": "construction-law",
    "中华人民共和国城乡规划法": "urban-rural-planning-law",
    "中华人民共和国房地产管理法": "real-estate-law",
    "中华人民共和国土地管理法": "land-administration-law",
    "中华人民共和国农村土地承包法": "rural-land-contract-law",
    "中华人民共和国中小企业促进法": "sme-promotion-law",
    "中华人民共和国乡镇企业法": "township-enterprise-law",
    "中华人民共和国农民专业合作社法": "farmer-cooperative-law",
    "中华人民共和国国境卫生检疫法": "border-health-quarantine-law",
    "中华人民共和国国家赔偿法": "state-compensation-law",
    "中华人民共和国立法法": "legislation-law",
    "中华人民共和国公务员法": "civil-service-law",
    "中华人民共和国人民法院组织法": "people-court-organization-law",
    "中华人民共和国人民检察院组织法": "people-procuratorate-organization-law",
    "中华人民共和国法官法": "judges-law",
    "中华人民共和国检察官法": "procurators-law",
    "中华人民共和国律师法": "lawyers-law",
    "中华人民共和国公证法": "notary-law",
    "中华人民共和国仲裁法": "arbitration-law",
    "中华人民共和国人民调解法": "people-mediation-law",
    "中华人民共和国法律援助法": "legal-aid-law",
    "中华人民共和国工会法": "trade-union-law",
    "中华人民共和国老年人权益保障法": "elderly-rights-law",
    "中华人民共和国妇女权益保障法": "women-rights-law",
    "中华人民共和国残疾人保障法": "disabled-persons-law",
    "中华人民共和国反家庭暴力法": "anti-domestic-violence-law",
    "中华人民共和国公益事业捐赠法": "public-welfare-donation-law",
    "中华人民共和国退役军人保障法": "veterans-law",
    "中华人民共和国现役军官法": "active-officers-law",
    "中华人民共和国国旗法": "national-flag-law",
    "中华人民共和国国徽法": "national-emblem-law",
    "中华人民共和国国歌法": "national-anthem-law",
    "中华人民共和国国宾法": "state-guest-law",
    "中华人民共和国领海及毗连区法": "territorial-sea-law",
    "中华人民共和国专属经济区和大陆架法": "eez-law",
    "中华人民共和国国籍法": "nationality-law",
    "中华人民共和国护照法": "passport-law",
    "中华人民共和国驻外外交人员法": "diplomatic-personnel-law",
    "中华人民共和国领事特权与豁免条例": "consular-immunity-law"
}


def _slugify(name: str) -> str:
    """中文名 → ASCII slug. 优先查表, 缺失则启发式"""
    # 1. 完整名
    if name in SLUG_MAP:
        return SLUG_MAP[name]
    # 2. 去掉"中华人民共和国"前缀的短名 (兼容 LawRefBook/HF 简写)
    short = re.sub(r"^中华人民共和国", "", name)
    if short in SLUG_MAP:
        return SLUG_MAP[short]
    # 2b. 短名启发式: 假设去掉前缀后剩余短名可能已在 SLUG_MAP 中作为"完整名"出现
    # 例如 "刑法" 可能不存在但 "中华人民共和国刑法" 存在
    full = "中华人民共和国" + short
    if full in SLUG_MAP:
        return SLUG_MAP[full]
    # 3. 启发式
    out = short
    for cn, en in [("条例", "regulation"), ("规定", "provisions"),
                    ("办法", "measures"), ("解释", "interpretation"),
                    ("通则", "rules"), ("法", "law")]:
        out = out.replace(cn, f"-{en}")
    out = re.sub(r"-+", "-", out).strip("-").lower()
    if not out or not re.match(r"^[a-z0-9-]+$", out):
        out = "law-" + hashlib.md5(name.encode()).hexdigest()[:8]
    return out


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _content_hash(data: dict) -> str:
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _download(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 1000:
        return
    print(f"  download: {url} -> {dest.name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)


# === Source 1: laws-data ===
def import_laws_data(cache_dir: Path) -> Iterable[dict]:
    """从 13098806890/laws-data 导入 (中文 JSON + 英文 JSON)"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / "laws-data.zip"
    if not zip_path.exists() or zip_path.stat().st_size < 1000:
        _download(
            "https://github.com/13098806890/laws-data/archive/refs/heads/main.zip",
            zip_path,
        )
    extract_dir = cache_dir / "laws-data-main"
    if not extract_dir.exists():
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
    json_root = extract_dir / "laws-data-main" / "json"
    if not json_root.exists():
        json_root = extract_dir / "json"

    count = 0
    for cat_dir in json_root.iterdir():
        if not cat_dir.is_dir():
            continue
        cat = cat_dir.name
        for json_file in cat_dir.glob("*.json"):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            # laws-data JSON 结构: title/category/pub_date/effective_date/full_text/parts/total_articles
            law_name = data.get("title") or data.get("名称") or json_file.stem
            yield {
                "_source": "laws-data",
                "_raw": data,
                "name": law_name,
                "type": data.get("category") or cat,
                "publish_date": data.get("pub_date", ""),
                "effective_date": data.get("effective_date", ""),
                "office": data.get("issuing_org") or data.get("制定机关", ""),
                "content": data.get("full_text") or data.get("全文") or "",
                "total_articles": data.get("total_articles", 0),
                "is_current": data.get("is_current", True),
            }
            count += 1
    print(f"  laws-data: scanned {count} files")


# === Source 2: HF parquet ===
def import_hf(cache_dir: Path) -> Iterable[dict]:
    """从 twang2218 HuggingFace parquet 导入"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    parquet = cache_dir / "hf-law.parquet"
    if not parquet.exists() or parquet.stat().st_size < 1000:
        _download(
            "https://huggingface.co/datasets/twang2218/chinese-law-and-regulations/resolve/main/data/train-00000-of-00001-8329bce6db03c820.parquet",
            parquet,
        )
    try:
        import pandas as pd
    except ImportError:
        print("  ⚠ pandas 不可用, 跳过 HF 源 (pip install pandas pyarrow)")
        return
    df = pd.read_parquet(parquet)
    count = 0
    for _, row in df.iterrows():
        yield {
            "_source": "hf",
            "name": row["title"],
            "type": row.get("type", "法律"),
            "office": row.get("office", ""),
            "publish_date": str(row.get("publish_date", ""))[:10],
            "effective_date": str(row.get("effective_date", ""))[:10],
            "status": row.get("status", ""),
            "content": row.get("content", ""),
            "_raw": row.to_dict(),
        }
        count += 1
    print(f"  HF: {count} records")


# === Source 3: LawRefBook SQLite ===
def import_lawrefbook(cache_dir: Path) -> Iterable[dict]:
    """从 LawRefBook/Laws SQLite 导入"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / "lawrefbook.zip"
    if not zip_path.exists() or zip_path.stat().st_size < 1000:
        _download(
            "https://github.com/LawRefBook/Laws/archive/refs/heads/master.zip",
            zip_path,
        )
    extract_dir = cache_dir / "Laws-master"
    if not extract_dir.exists():
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
    # db.sqlite3 在 extract_dir/Laws-master/ 下 (zip 嵌套了一层)
    db_candidates = list(extract_dir.glob("*/db.sqlite3"))
    if not db_candidates:
        db_candidates = list(extract_dir.glob("db.sqlite3"))
    if not db_candidates:
        print("  ⚠ Laws-master SQLite 缺失")
        return
    db = db_candidates[0]
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    cur.execute("SELECT id, level, name, publish, valid_from, valid_to FROM law")
    count = 0
    for row in cur.fetchall():
        law_id, level, name, publish, valid_from, valid_to = row
        status = "现行有效" if (valid_to and valid_to >= "2026-01-01") else "已废止"
        yield {
            "_source": "lawrefbook",
            "name": name,
            "type": level,
            "publish_date": publish or "",
            "effective_date": valid_from or "",
            "status": status,
            "_raw": {"id": law_id, "level": level},
        }
        count += 1
    print(f"  LawRefBook: {count} laws")


# === 标准化转换 ===
def _parse_articles(content: str) -> dict[str, str]:
    """从法律全文解析 '第X条 ... 第X+1条'. 忽略'第X章'/'第X节'/'第X编'
    返回 { '577': '...', '一千二百六十': '...' }
    keys 保留**原始**字符串 (用户/检索路由层做转换)
    """
    if not content:
        return {}
    arts = {}
    pattern = re.compile(
        r"第([一二三四五六七八九十百千零\d]+)条\s*(.*?)(?=(?:第[一二三四五六七八九十百千零\d]+条)|\Z)",
        re.S,
    )
    for m in pattern.finditer(content):
        num = m.group(1)
        start = m.start()
        ctx_before = content[max(0, start - 2):start]
        if ctx_before.endswith(("章", "节", "编")):
            continue
        text = m.group(2).strip()
        text = text[:2000]
        if len(text) > 5:
            arts[num] = text
    return arts


_CN_DIGITS = "零一二三四五六七八九"
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000}


def _cn_to_int(cn: str) -> int | None:
    """中文数字 → 阿拉伯数字.
    支持: 一千二百六十 (1260), 五百七十七 (577), 十二 (12), 五 (5),
          一百二十三 (123), 一千零五 (1005), 十 (10), 零 (0)
    """
    if not cn:
        return None
    if cn.isdigit():
        return int(cn)
    if cn == "十":
        return 10
    if all(c in _CN_DIGITS or c in _CN_UNITS for c in cn):
        if "亿" in cn or "万" in cn:
            return None  # 复杂先不处理
        total = 0
        current = 0
        for c in cn:
            if c in _CN_DIGITS:
                current = _CN_DIGITS.index(c)
            else:  # 十/百/千
                unit = _CN_UNITS[c]
                if current == 0:
                    current = 1
                total += current * unit
                current = 0
        total += current
        return total
    return None


def normalize(raw: dict) -> dict | None:
    """上游 dict → 标准化 Statute"""
    name = raw.get("name", "").strip()
    if not name:
        return None
    slug = _slugify(name)
    # 内容提取 (不同源不同字段) — laws-data 用 full_text, HF 用 content
    content = raw.get("content", "")
    if not content and isinstance(raw.get("_raw"), dict):
        r = raw["_raw"]
        content = r.get("full_text") or r.get("content") or r.get("正文") or r.get("全文") or ""
        if isinstance(content, list):
            content = "\n".join(str(c) for c in content)
    articles = _parse_articles(content) if content else {}

    law_type = raw.get("type") or "法律"
    office = raw.get("office", "")
    publish = raw.get("publish_date", "") or raw.get("_raw", {}).get("publish", "")
    effective = raw.get("effective_date", "") or raw.get("_raw", {}).get("valid_from", "")
    status = raw.get("status", "现行有效")
    if raw.get("is_current") is False:
        status = "已废止"

    data = {
        "id": slug,
        "name": name,
        "short_name": re.sub(r"^中华人民共和国", "", name),
        "slug": slug,
        "type": law_type if law_type in LAW_TYPES else "法律",
        "level": "national",
        "office": office,
        "publish_date": str(publish)[:10] if publish else "",
        "effective_date": str(effective)[:10] if effective else "",
        "status": status or "现行有效",
        "source": {
            "upstream": "https://flk.npc.gov.cn",
            "via": raw["_source"],
            "license": "公共领域 (中国法律文本) / MIT (代码)",
            "fetched_at": _now_iso(),
        },
        "articles": articles,
        "article_count": len(articles),
    }
    # 标准化前: 把中文键转换为 int 键镜像 (加速客户端按阿拉伯查)
    int_articles = {}
    for cn_key, text in articles.items():
        n = _cn_to_int(cn_key)
        if n is not None and str(n) not in int_articles:
            int_articles[str(n)] = text
    data["articles_by_int"] = int_articles
    data["content_hash"] = _content_hash(data)
    return data


# === Main ===
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["laws-data", "hf", "lawrefbook", "all"],
                        required=True)
    parser.add_argument("--cache-dir", default="/tmp/prc-law-data-cache",
                        help="上游下载缓存目录")
    parser.add_argument("--out-dir", default=str(DATA_DIR))
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    out_dir = Path(args.out_dir)
    STATUTES_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    sources = ["laws-data", "hf", "lawrefbook"] if args.source == "all" else [args.source]
    fn_map = {
        "laws-data": import_laws_data,
        "hf": import_hf,
        "lawrefbook": import_lawrefbook,
    }

    # 第一轮: 收集所有 (slug → raw) 去重
    seen: dict[str, dict] = {}  # slug → first raw
    sources_seen: dict[str, str] = {}  # slug → 来源标记
    for src in sources:
        print(f"\n=== {src} ===")
        for raw in fn_map[src](cache_dir):
            normalized = normalize(raw)
            if not normalized:
                continue
            slug = normalized["slug"]
            # 优先保留 laws-data (结构化最好)
            if slug not in seen or (src == "laws-data" and sources_seen.get(slug) != "laws-data"):
                seen[slug] = normalized
                sources_seen[slug] = src

    # 第二轮: 写入
    print(f"\n=== 写入 {len(seen)} 部法律 ===")
    for slug, data in seen.items():
        out_file = STATUTES_DIR / f"{slug}.json"
        out_file.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print(f"  -> {STATUTES_DIR}/ ({len(seen)} files)")

    # 第三轮: 写索引
    index_path = INDEX_DIR / "laws.jsonl"
    with index_path.open("w", encoding="utf-8") as f:
        for slug, data in seen.items():
            entry = {
                "id": data["slug"],
                "name": data["name"],
                "short_name": data["short_name"],
                "type": data["type"],
                "article_count": data["article_count"],
                "status": data["status"],
                "source": data["source"]["via"],
                "size_bytes": (STATUTES_DIR / f"{slug}.json").stat().st_size,
                "updated_at": data["source"]["fetched_at"],
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"  -> {index_path}")

    # slug map
    slug_map_path = INDEX_DIR / "slug-map.json"
    slug_map = {data["short_name"]: data["slug"] for data in seen.values()}
    slug_map.update({data["name"]: data["slug"] for data in seen.values()})
    slug_map_path.write_text(json.dumps(slug_map, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(f"  -> {slug_map_path} ({len(slug_map)} entries)")

    # 法条反向索引 (双键: 中文 + 阿拉伯)
    articles_index = INDEX_DIR / "articles.jsonl"
    with articles_index.open("w", encoding="utf-8") as f:
        for slug, data in seen.items():
            for art_num in data["articles"]:
                # 同时写阿拉伯键 (供 PRC-Law 检索路由)
                int_num = _cn_to_int(art_num)
                if int_num is not None:
                    f.write(json.dumps({"law_slug": slug, "article_num": str(int_num),
                                        "key_type": "int"}, ensure_ascii=False) + "\n")
                # 保留原键 (中文)
                f.write(json.dumps({"law_slug": slug, "article_num": art_num,
                                    "key_type": "cn"}, ensure_ascii=False) + "\n")
    print(f"  -> {articles_index}")

    return 0


if __name__ == "__main__":
    sys.exit(main())