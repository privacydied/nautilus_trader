from __future__ import annotations
import ast,json
from pathlib import Path
BANNED_IMPORTS={'PolymarketExecutionClient','PolymarketLiveExecClientFactory','LiveNode','TradingNode','OrderFactory'}; BANNED_ENVS={'POLYMARKET_PK','POLYMARKET_API_KEY','POLYMARKET_API_SECRET','POLYMARKET_FUNDER','POLYMARKET_PASSPHRASE'}
def check_path(path:Path):
    violations=[]
    for py in path.rglob('*.py'):
        if '__pycache__' in py.parts: continue
        tree=ast.parse(py.read_text(),filename=str(py))
        for n in ast.walk(tree):
            if isinstance(n,ast.ImportFrom):
                mod=n.module or ''
                if mod.startswith('nautilus_trader.live'): violations.append(f'{py}: import from {mod}')
                for a in n.names:
                    if a.name in BANNED_IMPORTS: violations.append(f'{py}: banned import {a.name}')
            if isinstance(n,ast.Call):
                f=n.func
                if isinstance(f,ast.Name) and f.id in BANNED_IMPORTS: violations.append(f'{py}: banned constructor {f.id}')
                if isinstance(f,ast.Attribute) and f.attr in {'submit_order','cancel_order'}: violations.append(f'{py}: banned call .{f.attr}')
                if isinstance(f,ast.Attribute) and f.attr in {'get','getenv'}:
                    for a in n.args[:1]:
                        if isinstance(a,ast.Constant) and a.value in BANNED_ENVS: violations.append(f'{py}: banned env read {a.value}')
            if isinstance(n,ast.Subscript) and isinstance(n.value,ast.Attribute) and getattr(n.value,'attr','')=='environ':
                sl=n.slice
                if isinstance(sl,ast.Constant) and sl.value in BANNED_ENVS: violations.append(f'{py}: banned env read {sl.value}')
    return {'ok':not violations,'violations':violations}
def assert_observer_only(path:Path):
    r=check_path(path)
    if not r['ok']: raise RuntimeError(json.dumps(r,indent=2))
