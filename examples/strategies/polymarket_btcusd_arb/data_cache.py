from __future__ import annotations
import hashlib,json,re
from dataclasses import asdict
from datetime import datetime,timezone
from pathlib import Path
from typing import Any,Iterable
import pandas as pd
from .models import CacheMetadata
def safe_name(v:str)->str: return re.sub(r'[^A-Za-z0-9_.-]+','_',v).strip('_') or 'unknown'
def _canon(v:Any)->Any:
    try:
        if pd.isna(v): return None
    except Exception: pass
    if hasattr(v,'item'): v=v.item()
    return round(v,12) if isinstance(v,float) else v
def canonical_rows_hash(rows:Iterable[dict[str,Any]])->str:
    norm=[{k:_canon(v) for k,v in sorted(r.items())} for r in rows]
    norm.sort(key=lambda r:(r.get('ts_event_ns',0),json.dumps(r,sort_keys=True,separators=(',',':'))))
    payload = "\n".join(json.dumps(r, sort_keys=True, separators=(",", ":"), allow_nan=False) for r in norm)
    return hashlib.sha256(payload.encode()).hexdigest()
def write_cache(df:pd.DataFrame,data_path:Path,metadata_path:Path,*,source:str,symbol_or_slug:str,sanitize_info:bool,loader_version_or_module:str)->CacheMetadata:
    data_path.parent.mkdir(parents=True,exist_ok=True); metadata_path.parent.mkdir(parents=True,exist_ok=True)
    start=int(df.ts_event_ns.min()) if len(df) and 'ts_event_ns' in df else 0; end=int(df.ts_event_ns.max()) if len(df) and 'ts_event_ns' in df else 0
    h=canonical_rows_hash(df.to_dict('records')); df.to_parquet(data_path,index=False)
    m=CacheMetadata(source,symbol_or_slug,start,end,len(df),datetime.now(timezone.utc).isoformat(),h,sanitize_info,loader_version_or_module,str(data_path)); metadata_path.write_text(json.dumps(asdict(m),indent=2,sort_keys=True)); return m
def read_cache(data_path:Path,metadata_path:Path):
    df=pd.read_parquet(data_path); raw=json.loads(metadata_path.read_text());
    if canonical_rows_hash(df.to_dict('records'))!=raw['data_hash']: raise RuntimeError(f'cache hash mismatch for {data_path}')
    return df,CacheMetadata(**raw)
