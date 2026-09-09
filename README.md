# prc-law-data — 中国大陆法律离线数据集

> PRC-Law 配套独立数据集仓库。
> 与 PRC-Law skill 解耦,可独立更新、独立演进、按需加载。

## 设计目标

2026-09-09：新增[数据校验与安全修复](docs/safety-fixes-2026-09-09.md)、[搜索构建与发布](docs/search-publication-upgrade-2026-09-09.md)、[内部服务运维](docs/internal-service-operations.md)及[共享规则与官方核验](docs/common-and-official-controls.md)。当前为内部辅助检索数据服务；来源可访问、结构完整和法律现行效力是不同的验证事项。

- **独立性**: 与 PRC-Law 仓库无强制绑定,通过 HTTP / 本地路径 / git submodule 三种模式对接
- **可演进**: 数据集更新与技能库发布解耦；提供显式更新及复核工具，不承诺新法自动即时入库
- **按需加载**: 不预下载,首次检索时按 `slug` 拉取单部法律全文 (zip/json)
- **成本边界**: 离线公开数据检索不调用商业credit接口；网络、存储、算力和人工仍有成本，来源再分发权限须分别核实

## 数据来源

| 来源 | 用途 | 许可 | 大小 |
|------|------|------|------|
| [13098806890/laws-data](https://github.com/13098806890/laws-data) | 法条结构化 (编/章/节/条) + 中英双语 + RAG 增强 | MIT (代码) / 公共领域 (法律文本) | 460 MB |
| [LawRefBook/Laws](https://github.com/LawRefBook/Laws) | 1688 部法律 + 459 部司法解释 + 622 部行政法规 | 未声明 (中国法律文本为公共领域) | 130 MB |
| [twang2218/chinese-law-and-regulations](https://huggingface.co/datasets/twang2218/chinese-law-and-regulations) | 22,552 条标准化条目 (Parquet) | Apache-2.0 | 152 MB |
| [pkulaw 中国法律英文译本](https://www.pkulaw.com/en) | 双语法律 (P1 计划) | 商业订阅 | - |

## 目录结构

```
prc-law-data/
├── README.md                # 本文件
├── LICENSE                  # 数据集许可声明
├── data/                    # 数据存储 (按需填充)
│   ├── statutes/            # 标准化法条 JSON (按 slug 分文件)
│   │   ├── civil-code.json  # 民法典
│   │   ├── company-law.json # 公司法
│   │   └── ...
│   ├── index/               # 全量索引 (轻量,常驻)
│   │   ├── laws.jsonl       # 所有法律元数据 (id, name, slug, type, status, source, updated_at)
│   │   └── articles.jsonl   # 法条-法律映射 (用于 O(1) 查找)
│   └── sources/             # 上游原始数据 (gitignored, 下载缓存)
├── scripts/                 # 维护脚本
│   ├── import.py            # 从上游导入并标准化
│   ├── update.sh            # 增量更新
│   ├── serve.py             # HTTP 检索 API (按需加载)
│   └── verify.py            # 完整性校验
├── docs/
│   ├── schema.md            # 数据 schema 说明
│   └── sources.md           # 上游变更日志
└── .gitignore               # 忽略 data/sources/ (大文件缓存)
```

## 数据 schema (标准化)

每部法律一个 JSON 文件,位于 `data/statutes/<slug>.json`:

```json
{
  "id": "civil-code",
  "name": "中华人民共和国民法典",
  "short_name": "民法典",
  "slug": "civil-code",
  "type": "法律",
  "level": "national",
  "office": "全国人民代表大会",
  "publish_date": "2020-05-28",
  "effective_date": "2021-01-01",
  "status": "现行有效",
  "source": {
    "upstream": "flk.npc.gov.cn",
    "via": "13098806890/laws-data",
    "license": "公共领域",
    "fetched_at": "2026-09-01T09:00:00Z"
  },
  "structure": {
    "编": [...],
    "章": [...],
    "条": 1260
  },
  "articles": {
    "1": "为了...",
    "2": "民法调整...",
    ...
  },
  "content_hash": "sha256:..."
}
```

索引文件 `data/index/laws.jsonl` (一行一条法律元数据,便于快速筛选):

```json
{"id": "civil-code", "name": "中华人民共和国民法典", "short_name": "民法典", "type": "法律", "article_count": 1260, "status": "现行有效", "source": "flk.npc.gov.cn", "size_bytes": 245678}
```

## 三种对接模式

### 模式 1: Git Submodule (推荐本地开发)

```bash
cd /path/to/PRC-Law
git submodule add https://github.com/your-org/prc-law-data.git vendor/prc-law-data
git submodule update --init --recursive
```

PRC-Law 侧通过 `vendor/prc-law-data/data/statutes/` 直接读取。

### 模式 2: HTTP API (远程按需加载)

启动 prc-law-data 的检索服务:

```bash
cd prc-law-data
python3 scripts/serve.py --port 8765
```

PRC-Law 侧通过 HTTP 调用:

```python
import urllib.request
url = "http://localhost:8765/v1/statute/civil-code/article/577"
data = json.loads(urllib.request.urlopen(url).read())
```

### 模式 3: 直接 Git Clone (一次性全量)

```bash
git clone https://github.com/your-org/prc-law-data.git
```

## 维护流程

### 一次性导入

```bash
./scripts/import.py --source laws-data   # 从 laws-data 仓库导入
./scripts/import.py --source hf          # 从 HuggingFace parquet 导入
./scripts/import.py --source lawrefbook  # 从 LawRefBook 仓库导入
```

### 增量更新

```bash
./scripts/update.sh  # 自动检测上游版本, 增量下载变化的法律
```

### 校验完整性

```bash
./scripts/verify.py  # 检查 hash, 报告缺失/损坏文件
```

## 与 PRC-Law 的协作关系

```
┌─────────────────┐         ┌─────────────────────┐
│   PRC-Law       │ reads   │   prc-law-data      │
│  (skill 仓库)    │ ◄───── │   (独立数据集仓库)    │
│                 │         │                     │
│ - SKILL.md      │         │ - statutes/*.json   │
│ - 检索路由       │         │ - index/*.jsonl     │
│ - 复合技能       │         │ - 上游同步脚本       │
└─────────────────┘         └─────────────────────┘
        │                              ▲
        │                              │
        │ writes feedback              │ pulls
        ▼                              │
   "更新建议" PR ───────────────► 自动/手动 sync
```

更新流程:
1. prc-law-data 自动检测上游变化 (laws-data/LawRefBook/HF 仓库 releases)
2. 增量更新并重新计算 hash
3. PRC-Law 在 SessionStart hook 中检查 prc-law-data 是否有新版本
4. 如有,提示用户 `git submodule update --remote` 或触发自动 pull

## GitHub Actions 自动同步

### 周级全量同步 (`sync-all.yml`)

- **触发**: 每周日 18:00 UTC 自动 + 手动 `workflow_dispatch`
- **架构**: 3 个并行 import job (laws-data / hf / lawrefbook) + 串行 verify + SQLite 构建
- **失败隔离**: `fail-fast: false` + `continue-on-error: true`, 单源失败不影响其他

```
import-laws-data  ─┐
import-hf         ─┼─→  verify + sqlite-build  →  notify summary
import-lawrefbook ─┘
```

### 单源快速同步 (`sync-single.yml`)

- **触发**: 手动 `workflow_dispatch`, 输入 `laws-data | hf | lawrefbook`
- **用途**: 开发者调试单个源, 不用等全量跑完
- **安全**: 输入经 `env: SOURCE` 转义, 不直接嵌入 shell

### 配置 `.github/workflows/`

| 文件 | 用途 | 频率 |
|------|------|------|
| `sync-all.yml` | 3 源并行 + 串行 verify/build | 周级自动 |
| `sync-single.yml` | 单源快速同步 | 手动 |

### SQLite 数据库构建

- 每周日全量同步完成后, 自动跑 `scripts/build_sqlite.py`
- 产出 `data/prc-law.db` (上传 artifact + 提交仓库)
- 失败不阻塞(降级, 仍可用 JSON 数据)
- PRC-Law 通过 `dataset_client.py` 自动加载

## 路线图

- [x] v0.1 — 仓库结构 + schema 设计
- [ ] v0.2 — 从 laws-data 导入核心法律 (50 部)
- [ ] v0.3 — 从 LawRefBook 补充司法解释
- [ ] v0.4 — HF parquet 索引合并
- [ ] v0.5 — HTTP API 服务
- [x] v1.0 — 全量覆盖 + 自动更新 + CI

## 许可证

- 数据集内容 (中国法律文本) 为**公共领域** (中国法律不享有著作权)
- 工具脚本 (scripts/) 采用 MIT
- 借鉴来源仓库的许可证以各上游仓库声明为准

## 借鉴合规声明

本数据集的内容均为中华人民共和国已公开的法律法规,法律文本本身在中国不受著作权法保护 (公共领域)。数据集的整合、标准化、索引工作为本项目原创,采用 MIT 许可证。
