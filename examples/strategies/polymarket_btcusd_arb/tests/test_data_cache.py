import pandas as pd
from examples.strategies.polymarket_btcusd_arb.data_cache import canonical_rows_hash,write_cache,read_cache
def test_hash_row_content_not_file_bytes(tmp_path):
 rows=[{'ts_event_ns':2,'price':0.2},{'price':0.1,'ts_event_ns':1}]; assert canonical_rows_hash(rows)==canonical_rows_hash(list(reversed(rows))); assert canonical_rows_hash(rows)!=canonical_rows_hash([{'ts_event_ns':1,'price':0.9}])
def test_cache_roundtrip(tmp_path):
 df=pd.DataFrame([{'ts_event_ns':1,'price':0.1}]); m=write_cache(df,tmp_path/'x.parquet',tmp_path/'x.metadata.json',source='s',symbol_or_slug='x',sanitize_info=True,loader_version_or_module='t'); out,m2=read_cache(tmp_path/'x.parquet',tmp_path/'x.metadata.json'); assert m.data_hash==m2.data_hash and len(out)==1
