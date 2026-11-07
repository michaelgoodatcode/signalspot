"""Write pipeline DataFrames to Spark / Delta tables (used by the Databricks notebooks).

Rows are converted cell by cell with an explicit schema so that NaN becomes a real
NULL, all-empty columns keep a concrete type, and timestamps are stored as UTC.
Works on classic Spark and on Databricks serverless (Spark Connect).
"""
from __future__ import annotations

import pandas as pd


def _spark_types():
    from pyspark.sql import types as T
    return {
        "string": T.StringType(), "double": T.DoubleType(), "long": T.LongType(),
        "boolean": T.BooleanType(), "timestamp": T.TimestampType(),
    }


def _cell(v, t: str):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if t == "string":
        return str(v)
    if t == "double":
        return float(v)
    if t == "long":
        return int(v)
    if t == "boolean":
        return bool(v)
    if t == "timestamp":
        ts = pd.Timestamp(v)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        return ts.to_pydatetime()
    raise ValueError(f"Unknown type {t}")


def to_spark_df(spark, pdf: pd.DataFrame, types: dict):
    from pyspark.sql import types as T
    tm = _spark_types()
    missing = [c for c in types if c not in pdf.columns]
    if missing:
        raise KeyError(f"DataFrame is missing columns {missing}")
    cols = list(types)
    schema = T.StructType([T.StructField(c, tm[types[c]], True) for c in cols])
    rows = [tuple(_cell(v, types[c]) for c, v in zip(cols, rec)) for rec in pdf[cols].itertuples(index=False, name=None)]
    return spark.createDataFrame(rows, schema)


def write_table(spark, pdf: pd.DataFrame, types: dict, full_name: str, comment: str | None = None) -> int:
    sdf = to_spark_df(spark, pdf, types)
    (sdf.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(full_name))
    if comment:
        try:
            spark.sql(f"COMMENT ON TABLE {full_name} IS '{comment.replace(chr(39), '')}'")
        except Exception:  # comments are optional (not supported on every metastore)
            pass
    return len(pdf)
