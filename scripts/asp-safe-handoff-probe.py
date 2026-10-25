"""Bounded read-only handoff qualification for the original ASP Backend8010 supervisor.
No scheduler is started, executor created, provider called, job fired, or DB row written.
The capture scheduler records the existing registration metadata only.
"""
from __future__ import annotations
import argparse,contextlib,datetime as dt,hashlib,io,json,math,pathlib,re,sys,time,urllib.request,urllib.error
from types import SimpleNamespace

def digest(p): return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
def clock(s):
 v=dt.datetime.fromisoformat(str(s).replace("Z","+00:00"))
 if v.tzinfo is None: raise ValueError("timezone required")
 return v.astimezone(dt.timezone.utc)
def next_fire(spec,now,anchor):
 kind=spec[0];k=spec[1]
 if k.get("jitter"): raise ValueError("jitter cannot certify a deterministic handoff")
 if kind=="date":
  at=clock(k["run_date"]);return at if at>=now-dt.timedelta(seconds=2) else None
 if kind=="interval":
  seconds=sum(float(k.get(n,0))*m for n,m in (("weeks",604800),("days",86400),("hours",3600),("minutes",60),("seconds",1)))
  if seconds<=0: raise ValueError("invalid interval")
  first=clock(k["next_run_time"]) if k.get("next_run_time") else anchor+dt.timedelta(seconds=seconds)
  steps=max(0,math.ceil(((now-dt.timedelta(seconds=2))-first).total_seconds()/seconds))
  return first+dt.timedelta(seconds=steps*seconds)
 if kind=="cron":
  from apscheduler.triggers.cron import CronTrigger
  allowed={x:k[x] for x in ("year","month","day","week","day_of_week","hour","minute","second","start_date","end_date","timezone") if x in k}
  return CronTrigger(**allowed).get_next_fire_time(None,now-dt.timedelta(seconds=2))
 raise ValueError("unsupported trigger: "+str(kind))
_registration_capture={}

def registration_specs(settings,anchor):
 global _registration_capture
 from app.jobs import scheduler as module
 specs=[]
 class Capture:
  def __init__(self,*a,**k): pass
  def add_job(self,func,*a,**k):
   kind=a[0] if a else k.pop("trigger",None)
   specs.append((kind,k));return SimpleNamespace(id=k.get("id"))
 class AnchorClock(dt.datetime):
  @classmethod
  def now(cls,tz=None): return anchor.astimezone(tz) if tz else anchor.replace(tzinfo=None)
 original=(module.AsyncIOScheduler,module.datetime,module.off_event_loop)
 try:
  module.AsyncIOScheduler=Capture;module.datetime=AnchorClock;module.off_event_loop=lambda f,l:f
  diagnostic_output=io.StringIO();diagnostic_errors=io.StringIO()
  try:
   with contextlib.redirect_stdout(diagnostic_output),contextlib.redirect_stderr(diagnostic_errors):
    module.build_scheduler(settings)
  finally:
   stdout=diagnostic_output.getvalue().encode("utf-8");stderr=diagnostic_errors.getvalue().encode("utf-8")
   _registration_capture={"stdout_sha256":hashlib.sha256(stdout).hexdigest(),"stdout_bytes":len(stdout),"stderr_sha256":hashlib.sha256(stderr).hexdigest(),"stderr_bytes":len(stderr),"raw_output_persisted":False}
 finally: module.AsyncIOScheduler,module.datetime,module.off_event_loop=original
 return specs

def validate_request(request,repo,now):
 if request.get("schema")!="AISP_SINGLE_OWNER_MAINTENANCE_V2": raise ValueError("unreviewed hash-only request")
 if "expected_owner" in request or "loaded_owner_before_fence" in request: raise ValueError("raw owner token forbidden in request")
 if request.get("port")!=8010: raise ValueError("wrong port")
 if now>=clock(request["expires_at_utc"]): raise ValueError("expired maintenance request")
 if clock(request["created_at_utc"])>now: raise ValueError("future request")
 owner_sha=request.get("expected_owner_file_sha256")
 if not isinstance(owner_sha,str) or not re.fullmatch(r"[0-9a-f]{64}",owner_sha) or digest(repo/".runtime/scheduler-owner")!=owner_sha: raise ValueError("owner file hash changed")
 for rel,sha in request["installed_hashes"].items():
  p=(repo/rel).resolve()
  if not p.is_relative_to(repo.resolve()) or digest(p)!=sha: raise ValueError("reviewed installed dependency changed: "+rel)
 if digest(repo/".env")!=request["env_sha256"]: raise ValueError("settings changed")
 return True

def fence_stopped():
 try:
  try:r=urllib.request.urlopen("http://127.0.0.1:8010/readyz",timeout=3)
  except urllib.error.HTTPError as e:r=e
  with r:doc=json.loads(r.read())
  return doc.get("scheduler")=="not_running" and doc.get("database")=="reachable"
 except Exception:return False

def drain_ready(*,running,empty_seconds,fenced,stopped,gap,query_seconds):
 if running or empty_seconds<4 or query_seconds>=1:return False
 return stopped if fenced else gap>=12

def main():
 p=argparse.ArgumentParser();p.add_argument("--repo",required=True);p.add_argument("--request",required=True);args=p.parse_args()
 repo=pathlib.Path(args.repo).resolve();request=json.loads(pathlib.Path(args.request).read_bytes());sys.path.insert(0,str(repo/"server"))
 from app.core.config import get_settings
 from app.db.session import SessionLocal
 from app.models import JobRun,RunStatus
 from sqlalchemy import select
 settings=get_settings();anchor=clock(request["scheduler_started_at_utc"]);specs=registration_specs(settings,anchor)
 proof={"schema":"AISP_HANDOFF_POINT_PROOF_V2","request_sha256":digest(args.request),"registered_jobs":len(specs),"provider_calls":0,"database_writes":0,"schedulers_started":0,"executors_created":0,"original_owner_file_sha256":request["expected_owner_file_sha256"],"registration_diagnostics":dict(_registration_capture),"ready":False}
 fenced=request.get("fenced_scheduler") is True
 deadline=time.monotonic()+min(900 if fenced else 240,float(request.get("maximum_wait_seconds",180)));empty_since=None;attempts=0
 try:
  while time.monotonic()<deadline:
   now=dt.datetime.now(dt.timezone.utc);validate_request(request,repo,now)
   fires=[(s[1].get("id"),next_fire(s,now,anchor)) for s in specs];fires=[(i,v) for i,v in fires if v is not None]
   first=min(fires,key=lambda t:t[1]);gap=(first[1]-now).total_seconds()
   stopped=fence_stopped() if fenced else False
   with SessionLocal() as session:
    if session.get_bind().dialect.name=="postgresql":session.connection(execution_options={"postgresql_readonly":True})
    rows=session.execute(select(JobRun.id,JobRun.job_name,JobRun.started_at).where(JobRun.status==RunStatus.RUNNING).limit(101)).all()
    session.rollback()
   after=dt.datetime.now(dt.timezone.utc);attempts+=1
   running=[{"id":str(r[0]),"job":r[1],"started_at":str(r[2])} for r in rows]
   if running:empty_since=None
   elif empty_since is None:empty_since=time.monotonic()
   proof.update(observed_at_utc=after.isoformat(),running=running,earliest_next_job=first[0],earliest_next_utc=first[1].isoformat(),gap_seconds=(first[1]-after).total_seconds(),attempts=attempts)
   if drain_ready(running=running,empty_seconds=0 if empty_since is None else time.monotonic()-empty_since,fenced=fenced,stopped=stopped,gap=gap,query_seconds=(after-now).total_seconds()):
    validate_request(request,repo,after);proof.update(ready=True,fenced_and_drained=fenced and stopped,continuous_observed_empty_seconds=time.monotonic()-empty_since);break
   time.sleep(min(1,max(0.05,deadline-time.monotonic())))
  if not proof["ready"]:proof["reason"]="ACTIVE_JOBS_DID_NOT_DRAIN" if fenced else "NO_SAFE_EMPTY_HANDOFF_WINDOW_WITHIN_BOUNDED_ATTEMPT"
 except Exception as e:proof.update(reason="HANDOFF_QUALIFICATION_FAILED",error_type=type(e).__name__,errno=getattr(e,"errno",None),winerror=getattr(e,"winerror",None))
 out=pathlib.Path(request["proof_path"])
 if out.exists():raise RuntimeError("immutable proof already exists")
 out.write_text(json.dumps(proof,indent=2),encoding="utf8");print(json.dumps({"ready":proof["ready"],"proof_path":str(out),"proof_sha256":digest(out),"observed_at_utc":proof.get("observed_at_utc"),"earliest_next_utc":proof.get("earliest_next_utc"),"reason":proof.get("reason"),"fenced_and_drained":proof.get("fenced_and_drained",False),"error_type":proof.get("error_type"),"errno":proof.get("errno"),"winerror":proof.get("winerror")}))
 return 0 if proof["ready"] else 2
def safe_main():
 # Imports/private Settings setup in main may fail before its drain-loop try.
 # Never let their traceback, warning text or exception input values reach logs.
 output=io.StringIO();errors=io.StringIO()
 try:
  with contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors):
   result=main()
 except Exception as error:
  raw=(output.getvalue()+"\0"+errors.getvalue()).encode("utf-8")
  print(json.dumps({"schema":"AISP_SAFE_HANDOFF_COMPONENT_RESULT_V2","ready":False,"fenced_and_drained":False,"reason":"HANDOFF_SETUP_FAILED","error_type":type(error).__name__,"errno":getattr(error,"errno",None),"winerror":getattr(error,"winerror",None),"captured_output_sha256":hashlib.sha256(raw).hexdigest(),"captured_output_bytes":len(raw)}))
  return 2
 raw=(output.getvalue()+"\0"+errors.getvalue()).encode("utf-8")
 try:
  lines=output.getvalue().splitlines()
  if errors.getvalue() or len(lines)!=1:raise ValueError("unknown probe output")
  item=json.loads(lines[0])
  if not isinstance(item,dict) or type(item.get("ready")) is not bool or type(item.get("fenced_and_drained")) is not bool:raise ValueError("unknown probe result")
  allowed={"ACTIVE_JOBS_DID_NOT_DRAIN","NO_SAFE_EMPTY_HANDOFF_WINDOW_WITHIN_BOUNDED_ATTEMPT","HANDOFF_QUALIFICATION_FAILED",None}
  if item.get("reason") not in allowed:raise ValueError("unknown probe reason")
  if item["ready"] and (result!=0 or not item["fenced_and_drained"]):raise ValueError("inconsistent probe result")
  safe={key:item.get(key) for key in ("ready","fenced_and_drained","proof_path","proof_sha256","observed_at_utc","earliest_next_utc","reason","error_type","errno","winerror")}
  safe.update(schema="AISP_SAFE_HANDOFF_COMPONENT_RESULT_V2",captured_output_sha256=hashlib.sha256(raw).hexdigest(),captured_output_bytes=len(raw))
 except Exception:
  print(json.dumps({"schema":"AISP_SAFE_HANDOFF_COMPONENT_RESULT_V2","ready":False,"fenced_and_drained":False,"reason":"UNREVIEWED_PROBE_OUTPUT","captured_output_sha256":hashlib.sha256(raw).hexdigest(),"captured_output_bytes":len(raw)}))
  return 2
 print(json.dumps(safe));return result
if __name__=="__main__":raise SystemExit(safe_main())
