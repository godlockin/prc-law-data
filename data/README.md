# prc-law-data 数据集

中国大陆法律离线数据集,作为 PRC-Law skill 的配套独立仓库。

## 内容

- `data/index/laws.jsonl` — 全量法律元数据索引 (id, name, slug, type, article_count, status, source)
- `data/index/articles.jsonl` — 法条反向索引 (article_id → law_slug)
- `data/statutes/*.json` — 单部法律全文 (按 slug 存储,按需加载)

## 更新

```bash
./scripts/update.sh
```

## 使用

参见 [README.md](README.md)。