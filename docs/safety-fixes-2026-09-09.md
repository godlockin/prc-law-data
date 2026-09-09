# 2026-09-09 数据安全修复

## 接口和数据版本

`GET /v1/resolve?law=<名称或slug>&as_of=YYYY-MM-DD` 按可用元数据选择版本；没有匹配或同日期正文冲突返回 404。可选 `article` 参数指定条号：所有同日期候选的所查条文一致时允许选择，并标注其他内容存在差异；所查条文不同仍拒绝。调用者仍需核验官方效力。

导入新增 `law_id`、`version_id` 和 `index/versions.json`，同名法律不同版本分别保存；保留旧数据并重建索引，不覆盖为单一来源版本。`--out-dir` 贯穿全部输出路径。文本保存 `raw_content` 和解析器版本；无条号全文保存为未结构化，不编造条文。

法条边界按行首标题解析，正文中引用条号不再切断，取消 2000 字截断，重复标题报错。所有输入解析后才发布批次；单文件用临时文件原子替换。多文件索引发布不是跨进程数据库事务，更新期间应暂停服务，更新完成后校验并重启。

HTTP slug 限制为单段标识符，并检查真实路径边界。分页限制为 1–1000，负 offset 和非整数返回 400。搜索覆盖全量法律；SQLite 以发布索引摘要校验代次，再使用 trigram 候选索引与精确子串过滤；详见 search-publication-upgrade-2026-09-09.md。旧库回退 JSON，本次仅做本机延迟测量。

## 维护

运行 `python3 -B -m unittest discover -s tests -v` 执行离线回归。

`python3 scripts/verify.py --data-dir <目录>` 检查哈希、身份、条数、索引集合及别名/版本目标。

`python3 scripts/repair_index.py --data-dir <目录> --backup-dir <不存在的备份目录>` 备份索引和缺哈希文件后重建索引；已有哈希不匹配时拒绝改写。缺失哈希只能建立带 `local_baseline_only` 来源说明的本地基线，不代表内容曾经正确或来源可信。

历史正文缺少上游原文时不能从哈希或当前条文恢复。重新导入可信原文后必须比对旧版本与新解析结果；不得删除原始备份或把结构校验成功当成法律效力核验。

本次已从三个固定提交的公共镜像重建并切换数据。`recover_snapshot.py --cache <快照缓存> --old-data <旧库> --staged-data <新目录>` 校验快照哈希、保留原文及来源、拆分插入条款并隔离未匹配旧记录。Parquet 恢复需要 PyArrow；基础回归不依赖它。

暂停服务后运行 `promote_recovery.py --staged <暂存库> --target <数据目录> --backup <新备份目录>`，校验、备份并切换；数据校验失败恢复旧目录。正常检索、HTTP 读取及新建 SQLite 均拒绝 `retrieval_status: quarantined` 记录。恢复过程和旧库备份见工作区 `outputs/remaining-work-2026-09-09`。
