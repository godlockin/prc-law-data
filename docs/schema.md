# 数据 schema 定义

## 1. 法律文件 — `data/statutes/<slug>.json`

```typescript
interface Statute {
  id: string;                  // 唯一 slug, 如 "civil-code"
  name: string;                // 全称, 如 "中华人民共和国民法典"
  short_name: string;          // 简称, 如 "民法典"
  slug: string;                // 路由用, 同 id
  type: LawType;              // 类型
  level: 'national' | 'departmental' | 'local';
  office: string;              // 制定机关
  publish_date: string;        // 发布日期 ISO 8601
  effective_date: string;      // 生效日期 ISO 8601
  status: '现行有效' | '已废止' | '已修订' | '尚未生效';
  source: Source;              // 来源溯源
  structure?: Structure;              // 法律结构 (可选,法典类有)
  articles: Record<string, string>;   // 条文字典 { "1": "...", "2": "..." }
  article_count: number;      // 总条数
  content_hash: string;        // sha256:hex
}

type LawType = '宪法' | '法律' | '行政法规' | '部门规章' |
               '地方性法规' | '司法解释' | '监察法规' |
               '国际条约' | '规范性文件';

interface Source {
  upstream: string;            // 上游 URL/项目
  via: string;                 // 实际拉取的项目
  license: string;             // 许可证
  fetched_at: string;          // ISO 8601
}

interface Structure {
  编?: Volume[];
  章?: Chapter[];
  条: number;
}

interface Volume { name: string; chapters?: Chapter[]; }
interface Chapter { name: string; articles: string[]; }
```

## 2. 索引文件 — `data/index/laws.jsonl`

一行一条:

```json
{"id":"civil-code","name":"中华人民共和国民法典","short_name":"民法典","type":"法律","article_count":1260,"status":"现行有效","source":"flk.npc.gov.cn","size_bytes":245678,"updated_at":"2026-09-01T09:00:00Z"}
```

字段:

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| id | string | ✅ | slug |
| name | string | ✅ | 全称 |
| short_name | string | ✅ | 简称 (用于检索匹配) |
| type | LawType | ✅ | 法律类型 |
| article_count | number | ✅ | 条文总数 |
| status | string | ✅ | 效力状态 |
| source | string | ✅ | 上游来源 |
| size_bytes | number | ⚠️ | 文件大小 (用于按需加载决策) |
| updated_at | string | ✅ | ISO 8601 |

## 3. 法条索引 — `data/index/articles.jsonl`

按法条号反向索引 (供"民法典第577条 → 跳到 slug=civil-code"):

```json
{"law_slug":"civil-code","article_num":"577","hash":"sha256:..."}
```

## 4. slug 命名规范

- ASCII 小写,连字符分隔
- 优先用英文名: `civil-code`, `company-law`, `data-security-law`
- 司法解释加 `-interpretation` 后缀: `civil-code-contract-interpretation`
- 部门规章加部门缩写: `miit-regulation-xxx`

slug 列表 (`data/index/slug-map.json`):

```json
{
  "民法典": "civil-code",
  "公司法": "company-law",
  "数据安全法": "data-security-law",
  ...
}
```

## 5. hash 计算

每个 `data/statutes/<slug>.json` 的 `content_hash` 字段:

```python
import hashlib, json
canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
h = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
data['content_hash'] = f"sha256:{h}"
```

变更检测: 重新计算 hash, 与 `~/.prc-law-data/known_hashes.json` 比对, 仅下载 hash 变化的。