"""End-to-end pipeline used by both the Databricks notebook and the local script."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .aggregate import aggregate
from .clean import clean
from .config import load_config
from .export import build_app_json, collection_summary_md, write_json
from .io import read_raw, read_spots


@dataclass
class PipelineResult:
    config: dict
    raw: pd.DataFrame
    spots: pd.DataFrame
    clean: pd.DataFrame
    qa: pd.DataFrame
    stats: pd.DataFrame
    app_json: dict
    summary_md: str

    @property
    def spots_resolved(self) -> pd.DataFrame:
        s = pd.DataFrame([{k: v for k, v in sp.items() if k != "stats"} for sp in self.app_json["spots"]])
        return s


def run(raw_files: list[str], spots_csv: str | Path, config_path: str | Path | None = None,
        event_start: str | None = None, event_end: str | None = None,
        allow_synthetic: bool = False) -> PipelineResult:
    config = load_config(config_path)
    raw = read_raw(raw_files)
    spots = read_spots(spots_csv)
    clean_df, qa = clean(raw, spots, config, event_start=event_start or None,
                         event_end=event_end or None, allow_synthetic=allow_synthetic)
    stats = aggregate(clean_df, config)
    app = build_app_json(spots, stats, clean_df, qa, len(raw), config)
    summary = collection_summary_md(app, clean_df, qa, stats)
    return PipelineResult(config, raw, spots, clean_df, qa, stats, app, summary)


def write_outputs(result: PipelineResult, app_json_path: str | Path, processed_dir: str | Path) -> dict:
    """Write the app JSON and the processed CSV/markdown outputs. Returns the written paths."""
    processed_dir = Path(processed_dir)
    processed_dir.mkdir(parents=True, exist_ok=True)
    Path(app_json_path).parent.mkdir(parents=True, exist_ok=True)
    paths = {
        "app_json": str(app_json_path),
        "readings_clean": str(processed_dir / "readings_clean.csv"),
        "spot_stats": str(processed_dir / "spot_stats.csv"),
        "qa_log": str(processed_dir / "qa_log.csv"),
        "spots_resolved": str(processed_dir / "spots_resolved.csv"),
        "summary": str(processed_dir / "collection_summary.md"),
    }
    write_json(result.app_json, paths["app_json"])
    clean_out = result.clean.copy()
    clean_out["ts_utc"] = clean_out["ts_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    clean_out.to_csv(paths["readings_clean"], index=False)
    result.stats.to_csv(paths["spot_stats"], index=False)
    result.qa.to_csv(paths["qa_log"], index=False)
    result.spots_resolved.to_csv(paths["spots_resolved"], index=False)
    with open(paths["summary"], "w", encoding="utf-8") as fh:
        fh.write(result.summary_md)
    return paths
