"""Read-only source/code proof at existing startup and natural job boundaries.
Never imports target modules, executes their source, reads inputs or controls workers.
"""
from __future__ import annotations
import hashlib,json,logging,os,sys,types
from datetime import UTC,datetime
from pathlib import Path
SCHEMA="AISP_RUNTIME_SOURCE_PROOF_V1"
RUNTIME_RELEASE_PATH=Path("C:/Users/james/asp-model-studio/outputs/consumer-reliability-20261021-v2/combined-qb-native-release-20261023-v5-lock-release/RUNTIME_RELEASE.json")
_disabled_reason=None

def _sha(raw): return hashlib.sha256(raw).hexdigest()

def _compiled_codes(code):
    result={}
    for value in code.co_consts:
        if isinstance(value,types.CodeType):
            result[value.co_qualname]=value
            result.update(_compiled_codes(value))
    return result

def _loaded_codes(module):
    result={}
    def add(value):
        if isinstance(value,(staticmethod,classmethod)): value=value.__func__
        if isinstance(value,property):
            for fn in (value.fget,value.fset,value.fdel):
                if fn is not None:add(fn)
            return
        if isinstance(value,types.FunctionType) and value.__module__==module.__name__:
            original=value
            visited=set()
            while isinstance(getattr(original,"__wrapped__",None),types.FunctionType) and id(original) not in visited:
                visited.add(id(original));original=original.__wrapped__
            result[original.__code__.co_qualname]=original.__code__
    for value in vars(module).values():
        add(value)
        if isinstance(value,type) and value.__module__==module.__name__:
            for member in vars(value).values():add(member)
    return result

def member_proof(repo,member,*,modules=None):
    target=member["target"];path=(repo/target).resolve()
    try:path.relative_to((repo/"server/app").resolve())
    except ValueError:raise ValueError("Proof target outside reviewed application source")
    if not target.startswith("server/app/") or not target.endswith(".py"):
        raise ValueError("Proof target is not reviewed Python application source")
    raw=path.read_bytes();digest=_sha(raw)
    row={"target":target,"module":member["module"],"module_loaded":False,"source_sha256":digest,
         "code_identity_verified":False,"status":"NOT_LOADED","functions_verified":0}
    if digest!=member["sha256"]:row["status"]="SOURCE_HASH_MISMATCH";return row
    module=(modules if modules is not None else sys.modules).get(member["module"])
    if module is None:return row
    row["module_loaded"]=True
    module_path=getattr(module,"__file__",None)
    if module_path is None or Path(module_path).resolve()!=path:
        row["status"]="LOADED_MODULE_PATH_MISMATCH";return row
    compiled=_compiled_codes(compile(raw,str(path),"exec",dont_inherit=True,optimize=sys.flags.optimize))
    loaded=_loaded_codes(module)
    # Generated dataclass methods etc. have no compiled source code object.
    # Every loaded source-defined function must still match its compiled object.
    checks=[(name,code==compiled[name]) for name,code in loaded.items() if name in compiled]
    missing=[name for name,code in loaded.items() if name not in compiled and code.co_filename not in ("<string>","<generated>")]
    row["functions_verified"]=sum(ok for _,ok in checks)
    row["code_identity_verified"]=bool(checks) and all(ok for _,ok in checks) and not missing
    row["status"]="LOADED_CODE_MATCH" if row["code_identity_verified"] else "LOADED_CODE_UNVERIFIED_OR_DIFFERENT"
    return row

def capture(*,stage,repo=None):
    global _disabled_reason
    base={"schema":SCHEMA,"observed_at_utc":datetime.now(UTC).isoformat(),"stage":stage,"pid":os.getpid(),"ppid":os.getppid(),"members":[]}
    if _disabled_reason:return {**base,"status":_disabled_reason}
    try:
        repo=Path(repo) if repo is not None else Path(__file__).resolve().parents[3]
        raw=RUNTIME_RELEASE_PATH.read_bytes();spec=json.loads(raw)
        if spec.get("schema")!="AISP_REVIEWED_RUNTIME_SOURCE_SET_V1":raise ValueError("Runtime spec schema")
        base["runtime_spec_sha256"]=_sha(raw)
        owner_raw=(repo/".runtime/scheduler-owner").read_bytes()
        base["owner_file_sha256"]=_sha(owner_raw)
        token=os.environ.get("ASP_SCHEDULER_INSTANCE","")
        base["owner_matches_process"]=bool(token) and _sha(token.encode("utf-8"))==base["owner_file_sha256"]
        base["members"]=[member_proof(repo,m) for m in spec["members"]]
        base["status"]="OBSERVED"
    except PermissionError:
        _disabled_reason="PROOF_ACCESS_DENIED_TERMINAL_FOR_PROCESS";base["status"]=_disabled_reason
    except Exception as exc:
        base["status"]="PROOF_UNAVAILABLE:"+type(exc).__name__
    return base

def emit(*,stage):
    proof=capture(stage=stage)
    logging.getLogger(__name__).info("AISP_RUNTIME_SOURCE_PROOF_V1 %s",json.dumps(proof,separators=(",",":")))
    return proof
