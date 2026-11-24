"""Render local comparison JSON as a Chinese report; never publishes datasets."""
from __future__ import annotations

import argparse
import json
import random
import re
import statistics
from collections import defaultdict
from pathlib import Path

from app.knowledge.comparison import summary_ms

NAMES = {
    "A": "关键词", "B": "向量", "C": "纯图", "D": "向量＋关键词",
    "E": "混合→种子图扩展", "F": "混合∥独立图", "G": "混合→独立图（调度控制）",
}


def exclude_timing_window(report, trials, window):
    """Exclude whole blocks exposed to external load; retain raw evidence."""
    clean = defaultdict(list)
    excluded = []
    for block in report["blocks"]:
        if block["start"] <= window["end"] and block["end"] >= window["start"]:
            excluded.append(block)
        else:
            clean[block["config"]].append(block)
    count = min(len(clean[config]) for config in report["latency_ms"])
    if count < 1:
        raise ValueError("no clean timing repeat for at least one configuration")
    selected = [block for blocks in clean.values() for block in blocks[-count:]]
    keep = {(b["config"], b["repeat"]) for b in selected}
    report["timing_exclusion"] = {"window": window, "excluded_blocks": excluded,
                                  "clean_repeats_per_config": count,
                                  "retained_blocks": selected}
    for config, timing in report["latency_ms"].items():
        own = [t for t in trials if t["config"] == config and (config, t["repeat"]) in keep]
        timing["n"] = len(own)
        timing["retrieval"] = summary_ms([t["elapsed_ms"] for t in own])
        timing["observed_sequential_qps"] = len(own) / sum(t["elapsed_ms"] / 1000 for t in own)
        timing["stages"] = {stage: summary_ms([t["stages"][stage] for t in own if stage in t["stages"]])
                            for stage in ("vector_ms", "keyword_ms", "graph_ms")}
    report["docker_samples"] = [s for s in report["docker_samples"] if any(
        b["start"] <= s["time"] <= b["end"] for b in selected)]
    return report


def memory_mib(value):
    match = re.fullmatch(r"([0-9.]+)([KMGT]?i?B)", value.strip())
    if not match:
        return None
    scale = {"B": 1, "kB": 1000, "KB": 1000, "KiB": 1024,
             "MB": 1000 ** 2, "MiB": 1024 ** 2, "GB": 1000 ** 3,
             "GiB": 1024 ** 3, "TB": 1000 ** 4, "TiB": 1024 ** 4}
    return float(match[1]) * scale[match[2]] / 1024 ** 2


def paired_recall_delta(report, left, right, split="frozen-test", stage="reranked", repeats=2000):
    groups = defaultdict(list)
    for index, case in enumerate(report["cases"]):
        if case.get("split") != split:
            continue
        labels = set(case["relevant_chunk_ids"])
        left_score = len(set(report["rankings"][left][stage][index][:5]) & labels) / len(labels)
        right_score = len(set(report["rankings"][right][stage][index][:5]) & labels) / len(labels)
        groups[case.get("fact_group", case["id"])].append(left_score - right_score)
    values = [statistics.mean(group) for group in groups.values()]
    if not values:
        return None
    rng = random.Random(241124)
    boot = sorted(statistics.mean(rng.choices(values, k=len(values))) for _ in range(repeats))
    return {"fact_groups": len(values), "macro_fact_delta": statistics.mean(values),
            "bootstrap95": [boot[int(repeats * .025)], boot[int(repeats * .975) - 1]]}


def render(report):
    primary_split = "frozen-test" if all("frozen-test" in data for data in report["metrics"].values()) else "all"
    primary_name = "冻结集" if primary_split == "frozen-test" else "当前小样本集"
    lines = ["# pgvector 与 Milvus：A—F 第一版统一实验报告", "",
             f"生成时间（UTC）：{report['created_at']}。", "",
             f"本轮使用 {report['chunk_count']} 条现有知识片段、{report['case_count']} 条正向检索题。",
             "不扩充数据集；只评测检索及重排，不调用对话模型。", "",
             "## 两套 A—F 统一结果", "",
             f"效果为{primary_name}统一重排后的 Recall@5；速度为检索 P50，不包含 embedding、重排或生成。A、C 共用同一次测量，并非重复实验。", "",
             "| 方案 | pgvector Recall@5 | Milvus Recall@5 | pgvector P50 ms | Milvus P50 ms |",
             "|---|---:|---:|---:|---:|"]
    for strategy in "ABCDEF":
        keys = [f"shared_{strategy}"] * 2 if strategy in "AC" else [
            f"{backend}_{strategy}" for backend in ("pgvector", "milvus")]
        quality = [report["metrics"][key][primary_split]["reranked"]["recall@5"] for key in keys]
        speed = [report["latency_ms"][key]["retrieval"]["p50"] for key in keys]
        lines.append(f"| {strategy} {NAMES[strategy]} | {quality[0]:.2%} | {quality[1]:.2%} | {speed[0]:.2f} | {speed[1]:.2f} |")
    lines.extend(["",
             "## 实验设置", "",
             "- A：关键词重合评分；不是 BM25。A 在两个后端间共用。",
             "- B：精确余弦向量检索。pgvector 顺序扫描，Milvus FLAT，统一 Top-12。",
             "- C：查询实体／别名识别与 Neo4j 图扩展，两个后端共用。",
             "- D：向量与关键词并行召回，RRF 融合，最多 12 个候选，无图。",
             "- E：先 D，再将 D 的前 2 个片段作为种子进行图扩展，RRF 融合。",
             "- F：D 与不依赖向量结果的图召回并行，RRF 融合。",
             "- G：与 F 相同的两路召回依次调用，只用于验证调度差异。",
             f"- 每个配置全题集运行 {report['repeats']} 轮，固定随机种子打乱配置与问题顺序。",
             *([f"- 发现重复回归与部分计时重叠，排除重叠整组后统一保留每个配置 {report['timing_exclusion']['clean_repeats_per_config']} 轮干净计时。排除时间窗口、组别与未删改原始记录均已保留。"]
               if report.get("timing_exclusion") else []),
             "- 检索质量统计每题一次；重复轮次只增加计时样本，不扩大独立问题数量。",
             "- 统一候选数量、模型版本、资料、图谱和距离度量。重排前后分别报告。",
             f"- 每个配置另取 {len(report['live_timing_case_ids'])} 条分层问题做绕过缓存的重排计时。",
             "- 向量连接复用；当前图适配器计时包含实体处理、图构建校验与驱动创建。",
             "- Milvus 使用 Strong 一致性；pgvector 使用 PostgreSQL 默认已提交可见性。Strong 的同步等待包含在计时中，不能据此推断所有一致性配置的速度。",
             "", "## 模型与一致性", "",
             f"- Embedding：`{report['models']['embedding']}`，revision `{report['models']['embedding_revision']}`。",
             f"- Reranker：`{report['models']['reranker']}`，revision `{report['models']['reranker_revision']}`。",
             f"- 语料指纹：`{report['corpus_fingerprint']}`。",
             f"- 图身份：`{report['graph_identity']}`。",
             "- 冻结测试原始文件通过 SHA256 锁校验，并核对合并题集中的冻结题逐条一致。",
             "- 两种数据库 Top-12 与独立 Python 精确余弦基线的平均集合重合率：",
             *[f"  - {key}：{value:.2%}" for key, value in report['exact_oracle_overlap_at12'].items()],
             "- F 与 G 的逐题排序已由程序断言一致；不同则实验失败。",
             ""])
    for split, title in (("frozen-test", "冻结测试集"), ("all", "全部题集（含开发集）")):
        lines.extend([f"## {title}：效果", "",
                      "以下指标基于现有临时工程标注；语料通过专家审核不等于检索相关性标注已经独立审核。",
                      "", "| 配置 | 阶段 | 题数 | Recall@5 | Recall@10 | MRR@10 | nDCG@10 | 完整证据@5 |",
                      "|---|---|---:|---:|---:|---:|---:|---:|"])
        for config, splits in report["metrics"].items():
            if split not in splits:
                continue
            data = splits[split]
            for stage in ("raw", "reranked"):
                m = data[stage]
                lines.append(f"| {config} | {'召回融合' if stage == 'raw' else '统一重排'} | {data['count']} | {m['recall@5']:.2%} | {m['recall@10']:.2%} | {m['mrr@10']:.4f} | {m['ndcg@10']:.4f} | {m['sufficient_evidence_rate@5']:.2%} |")
        lines.append("")
    lines.extend(["## 查询速度（毫秒，热缓存）", "",
                  "检索计时不含查询 embedding、重排和回答生成；实时流水线列含检索与真实重排，不含查询 embedding。",
                  "", "| 配置 | 检索样本 | P50 | P95 | P99 | 观测串行 QPS | 实时流水线 P50 |",
                  "|---|---:|---:|---:|---:|---:|---:|"])
    for config, data in report["latency_ms"].items():
        r = data["retrieval"]
        live = data["live_pipeline"]["p50"]
        live_text = f"{live:.2f}" if live is not None else "未测"
        lines.append(f"| {config} | {data['n']} | {r['p50']:.2f} | {r['p95']:.2f} | {r['p99']:.2f} | {data['observed_sequential_qps']:.2f} | {live_text} |")
    e = report["query_embedding_live_ms"]
    lines.extend(["", f"查询 embedding 独立实时 P50：{e['p50']} ms；P95：{e['p95']} ms。",
                  "小样本实时重排的尾延迟只供探索，不能作为服务 SLA。串行 QPS 不是并发压测最大吞吐量。",
                  "", "## 图检索增益与调度对照", ""])
    for backend in ("pgvector", "milvus"):
        for strategy in ("E", "F"):
            left, right = f"{backend}_{strategy}", f"{backend}_D"
            delta = paired_recall_delta(report, left, right)
            if delta:
                low, high = delta["bootstrap95"]
                lines.append(f"- {left} 相对 {right}：冻结集重排后 Recall@5 的事实组宏平均差 {delta['macro_fact_delta'] * 100:+.2f} 个百分点；按 {delta['fact_groups']} 个事实组 bootstrap 95% 区间 [{low * 100:+.2f}, {high * 100:+.2f}]。")
        f = report["latency_ms"][f"{backend}_F"]["retrieval"]["p50"]
        g = report["latency_ms"][f"{backend}_G"]["retrieval"]["p50"]
        lines.append(f"- {backend} F/G 相同结果、不同调度：检索 P50 {f:.2f}/{g:.2f} ms。")
    lines.extend(["", "这些区间仅描述现有题集的不确定性，不能消除同一作者标注、反复查看旧测试集等偏差。",
                  "", "## 资源与构建", "",
                  "容器采样包含数据库固定开销，以下是整轮采样的范围，不能归因于某个索引或单条查询。",
                  "Milvus 另依赖 etcd，应合计其部署开销；Neo4j 为所有图方案的共同附加服务。",
                  "", "| 容器 | 样本数 | 内存中位数 MiB | 内存最大值 MiB | CPU 中位数 % |",
                  "|---|---:|---:|---:|---:|"])
    resources = defaultdict(list)
    for sample in report.get("docker_samples", []):
        resources[sample["Name"]].append(sample)
    for name, samples in resources.items():
        mem = [memory_mib(s["MemUsage"].split("/")[0]) for s in samples]
        mem = [v for v in mem if v is not None]
        cpu = [float(s["CPUPerc"].rstrip("%")) for s in samples]
        if mem:
            lines.append(f"| {name} | {len(samples)} | {statistics.median(mem):.2f} | {max(mem):.2f} | {statistics.median(cpu):.2f} |")
    lines.extend(["", *[f"- 本次载入／建库 {key}：{value:.3f} 秒。"
                            for key, value in report["build_seconds"].items()],
                  "- 如已有同名实验表／集合，以上含 upsert，并非首次空库构建速度对照。",
                  f"- 成功检索计时条数：{sum(v['n'] for v in report['latency_ms'].values())}；记录失败：{len(report['failures'])}。",
                  "", "## 适用范围与未执行项", "",
                  f"- 本轮未执行 {report['controls_not_executed']} 条非正向／安全控制题的对话验收，不将其计为通过。",
                  "- 未扩充语料，未做 HNSW/IVFFlat 参数搜索、冷缓存或并发负载测试。",
                  "- 候选重排最多 12 条，所有方案一致；现有相关性标注不穷尽，Precision 可能低估。",
                  "- F 不保证一定更快：图查询若占主导，或并发产生资源竞争，收益可能很小。",
                  "- 纯图结果不涉及 pgvector/Milvus，不能解释成某个向量数据库的效果。",
                  "- 不根据本轮小数据实验直接替换生产默认后端。",
                  "", "## 原始记录", "",
                  "- comparison.json：完整设置、逐题召回／重排结果、分组指标、原始资源与实时计时。",
                  "- comparison.csv：统一对照表，UTF-8 BOM。",
                  *(["- comparison.filtered.json / .csv：剔除受干扰整组计时后的最终对照数据，本报告速度表以此为准。",
                     "- timing-exclusion.json：排除窗口及其原始依据；原始三轮结果保留。"]
                    if report.get("timing_exclusion") else []),
                  "- comparison.trials.jsonl：每次检索的候选、耗时和分阶段计时。",
                  "- execution.log：运行进度；model-cache.json：本地模型复用记录。",
                  "- cache-transport-alias.json：相同权重版本从 Docker 主机地址切至本机地址的缓存复用说明。",
                  "- environment.json：版本、硬件与磁盘占用证据（另行采集）。",
                  "", "复现入口：代码分支的 scripts/compare-retrieval.ps1；依赖 compose.pgvector.yml、原有 Milvus/Neo4j 和本地模型。",
                  "全部真实语料、题目、缓存及报告仅存本地。", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--exclude-window", type=Path)
    args = parser.parse_args()
    report = json.loads(args.input.read_text(encoding="utf-8"))
    if args.exclude_window:
        trials = [json.loads(line) for line in args.input.with_suffix(".trials.jsonl").read_text(encoding="utf-8").splitlines()]
        window = json.loads(args.exclude_window.read_text(encoding="utf-8-sig"))
        report = exclude_timing_window(report, trials, window)
        args.input.with_suffix(".filtered.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        from app.knowledge.comparison import write_csv

        write_csv(report, args.input.with_suffix(".filtered.csv"))
    args.output.write_text(render(report), encoding="utf-8")


if __name__ == "__main__":
    main()
