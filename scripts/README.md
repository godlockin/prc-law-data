# prc-law-data scripts

## import.py — 数据导入

```bash
python3 import.py --source laws-data    # 从 13098806890/laws-data 导入
python3 import.py --source hf           # 从 twang2218 HuggingFace parquet 导入
python3 import.py --source lawrefbook   # 从 LawRefBook/Laws 导入
python3 import.py --source all          # 全部导入
```

## update.sh — 增量更新

```bash
./update.sh
```

## serve.py — HTTP 检索 API

```bash
python3 serve.py --port 8765
```

Endpoints:
- `GET /v1/laws` — 列出所有法律
- `GET /v1/statute/<slug>` — 获取单部法律全文
- `GET /v1/statute/<slug>/article/<n>` — 获取指定条
- `GET /v1/search?q=<query>` — 全文搜索 (按需扩展)

## verify.py — 完整性校验

```bash
python3 verify.py
```

检查每个 statute.json 的 hash, 报告缺失/损坏文件。