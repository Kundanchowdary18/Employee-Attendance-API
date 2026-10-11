"""Employee Attendance & Analytics API. Run: uvicorn app.main:app --port 8000"""
import os, re, calendar
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Literal
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, ConfigDict, Field, EmailStr, field_validator, model_validator
from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError
from bson import json_util

load_dotenv()
UTC=timezone.utc
IST=timezone(timedelta(hours=5, minutes=30))
client=MongoClient(os.getenv('MONGO_URI','mongodb://localhost:27017'), tz_aware=True, serverSelectionTimeoutMS=3000)
db=client[os.getenv('MONGO_DB','attendance_db')]
app=FastAPI(title='Employee Attendance & Analytics API',version='2.0.0')
PRESENCE={'PRESENT','WFH','ON_DUTY'}; STATUSES=PRESENCE|{'ABSENT','LEAVE'}
MIN_MS=100_000_000_000; MAX_MS=4_102_444_800_000

def now_utc(): return datetime.now(UTC).replace(microsecond=0)
def epoch_dt(ms):
    if type(ms) is not int or not MIN_MS <= ms <= MAX_MS: raise ValueError('must be epoch milliseconds in supported range')
    return datetime.fromtimestamp(ms/1000,UTC).replace(microsecond=0)
def dt_ms(dt):
    if dt is None:return None
    if dt.tzinfo is None: dt=dt.replace(tzinfo=UTC)
    return int(dt.timestamp()*1000)
def round2(x): return float(Decimal(str(x)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))
def round4(x): return float(Decimal(str(x)).quantize(Decimal('0.0001'),rounding=ROUND_HALF_UP))
def parse_date(s):
    try:
        d=date.fromisoformat(s)
        if d.isoformat()!=s: raise ValueError()
        return d
    except Exception: raise HTTPException(422,'date must be YYYY-MM-DD')
def parse_month(s):
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',s or ''): raise HTTPException(422,'month must be YYYY-MM')
    y,m=map(int,s.split('-')); return date(y,m,1), date(y,m,calendar.monthrange(y,m)[1])
def time_parts(s): return tuple(map(int,s.split(':')))
def shift_dt(day, hhmm, overnight=False):
    h,m=time_parts(hhmm); return datetime.combine(day,time(h,m),IST).astimezone(UTC)
def attendance_date(ts, emp):
    local=ts.astimezone(IST); d=local.date()
    if emp['shift_end'] <= emp['shift_start'] and local.strftime('%H:%M') < emp['shift_end']: d-=timedelta(days=1)
    return d.isoformat()
def late_minutes(ts, emp):
    local=ts.astimezone(IST); day=date.fromisoformat(attendance_date(ts,emp))
    start=datetime.combine(day,time(*time_parts(emp['shift_start'])),IST)
    elapsed=int((local-start).total_seconds())
    return elapsed//60 if elapsed > 600 else 0
def overtime_minutes(out_ts, emp, daystr):
    day=date.fromisoformat(daystr); endday=day+timedelta(days=1) if emp['shift_end']<=emp['shift_start'] else day
    end=shift_dt(endday,emp['shift_end'])
    mins=int((out_ts-end).total_seconds()//60)
    return mins if mins>=30 else 0
def derived(emp, daystr, pin, pout):
    late=late_minutes(pin,emp) if pin else 0
    if pin and pout:
        hours=round2((pout-pin).total_seconds()/3600); ot=overtime_minutes(pout,emp,daystr); half=hours<4.5
    else: hours=None; ot=0; half=False
    return {'late_minutes':late,'work_hours':hours,'overtime_minutes':ot,'half_day':half}
def record_out(d):
    hist=[]
    for h in d.get('history',[]) or []:
        changes={k:{'from':dt_ms(v.get('from')) if k in ('punch_in','punch_out') else v.get('from'),'to':dt_ms(v.get('to')) if k in ('punch_in','punch_out') else v.get('to')} for k,v in h.get('changes',{}).items()}
        hist.append({'at':dt_ms(h['at']),'by':h['by'],'reason':h['reason'],'changes':changes})
    return {'emp_code':d['emp_code'],'date':d['date'],'status':d['status'],'punch_in':dt_ms(d.get('punch_in')),'punch_out':dt_ms(d.get('punch_out')),'work_hours':d.get('work_hours'),'late_minutes':d.get('late_minutes',0),'overtime_minutes':d.get('overtime_minutes',0),'half_day':d.get('half_day',False),'history':hist}
def employee_out(d): return {k:(dt_ms(v) if k=='created_at' else v) for k,v in d.items() if k!='_id'}
def ensure_indexes():
    db.employees.create_index([('emp_code',ASCENDING)],unique=True,name='uq_emp_code')
    db.employees.create_index([('department',ASCENDING),('joined_on',ASCENDING)],name='department_joined')
    db.attendance_logs.create_index([('emp_code',ASCENDING),('date',ASCENDING)],unique=True,name='uq_attendance_emp_date')
    db.attendance_logs.create_index([('date',DESCENDING),('emp_code',ASCENDING)],name='attendance_date_emp')
    db.attendance_logs.create_index([('date',ASCENDING),('status',ASCENDING),('emp_code',ASCENDING)],name='attendance_date_status_emp')
    db.attendance_logs.create_index([('emp_code',ASCENDING),('date',ASCENDING),('late_minutes',ASCENDING)],name='attendance_emp_date_late')
@app.on_event('startup')
def startup():
    # Fail startup on index errors: uniqueness is part of the concurrency contract.
    ensure_indexes()

class EmployeeIn(BaseModel):
    model_config=ConfigDict(extra='ignore')
    emp_code:str=Field(pattern=r'^EMP\d{4,6}$'); name:str=Field(min_length=1,max_length=100); email:str=Field(max_length=120,pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$'); department:str=Field(min_length=1,max_length=50)
    shift_start:str=Field(default='09:30',pattern=r'^([01]\d|2[0-3]):[0-5]\d$'); shift_end:str=Field(default='18:30',pattern=r'^([01]\d|2[0-3]):[0-5]\d$'); joined_on:str
    @field_validator('joined_on')
    @classmethod
    def valid_joined(cls,v):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',v): raise ValueError('joined_on must be YYYY-MM-DD')
        date.fromisoformat(v); return v
    @model_validator(mode='after')
    def shifts_differ(self):
        if self.shift_start==self.shift_end: raise ValueError('shift_start must differ from shift_end')
        return self
class PunchInIn(BaseModel):
    model_config=ConfigDict(extra='ignore')
    emp_code:str; punched_at:Optional[int]=None; status:Literal['PRESENT','WFH','ON_DUTY']='PRESENT'
    @field_validator('punched_at')
    @classmethod
    def valid_ts(cls,v):
        if v is not None: epoch_dt(v)
        return v
class PunchOutIn(BaseModel):
    model_config=ConfigDict(extra='ignore')
    emp_code:str; punched_at:Optional[int]=None
    @field_validator('punched_at')
    @classmethod
    def valid_ts(cls,v):
        if v is not None: epoch_dt(v)
        return v
class RegularizeIn(BaseModel):
    model_config=ConfigDict(extra='ignore')
    status:Optional[Literal['PRESENT','WFH','ON_DUTY','ABSENT','LEAVE']]=None; punch_in:Optional[int]=None; punch_out:Optional[int]=None; reason:str=Field(min_length=5,max_length=200); regularized_by:str=Field(min_length=1,max_length=50)
    @field_validator('punch_in','punch_out')
    @classmethod
    def valid_ts(cls,v):
        if v is not None: epoch_dt(v)
        return v

@app.get('/health')
def health():
    try: client.admin.command('ping'); return {'status':'ok'}
    except Exception: raise HTTPException(503,'MongoDB unavailable')
@app.post('/employees',status_code=201)
def create_employee(body:EmployeeIn):
    doc=body.model_dump(); doc['created_at']=now_utc()
    try: db.employees.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409,'emp_code already exists')
    return employee_out(doc)
@app.get('/employees')
def list_employees(department:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    q={'department':department} if department is not None else {}; total=db.employees.count_documents(q)
    items=[employee_out(x) for x in db.employees.find(q,{'_id':0}).sort('emp_code',ASCENDING).skip((page-1)*page_size).limit(page_size)]
    return {'items':items,'total':total,'page':page,'page_size':page_size}
@app.post('/attendance/punch-in',status_code=201)
def punch_in(body:PunchInIn):
    emp=db.employees.find_one({'emp_code':body.emp_code})
    if not emp: raise HTTPException(404,'unknown employee')
    ts=epoch_dt(body.punched_at) if body.punched_at is not None else now_utc(); day=attendance_date(ts,emp)
    doc={'emp_code':body.emp_code,'date':day,'status':body.status,'punch_in':ts,'punch_out':None,'work_hours':None,'late_minutes':late_minutes(ts,emp),'overtime_minutes':0,'half_day':False,'history':[]}
    try: db.attendance_logs.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409,'already punched in for this date')
    return record_out(doc)
@app.post('/attendance/punch-out')
def punch_out(body:PunchOutIn):
    emp=db.employees.find_one({'emp_code':body.emp_code})
    if not emp: raise HTTPException(404,'unknown employee')
    ts=epoch_dt(body.punched_at) if body.punched_at is not None else now_utc()
    # Most recent open punch at or before the requested instant, including overnight shifts.
    doc=db.attendance_logs.find_one({'emp_code':body.emp_code,'punch_in':{'$lte':ts},'punch_out':None,'status':{'$in':list(PRESENCE)}},sort=[('punch_in',DESCENDING)])
    if not doc:
        closed=db.attendance_logs.find_one({'emp_code':body.emp_code,'punch_in':{'$lte':ts},'punch_out':{'$ne':None},'status':{'$in':list(PRESENCE)}},sort=[('punch_in',DESCENDING)])
        if closed: raise HTTPException(409,'already punched out')
        raise HTTPException(404,'no punch-in found')
    if ts<=doc['punch_in'] or ts-doc['punch_in']>timedelta(hours=24): raise HTTPException(422,'punch-out must be after punch-in and within 24 hours')
    vals=derived(emp,doc['date'],doc['punch_in'],ts)
    updated=db.attendance_logs.find_one_and_update({'_id':doc['_id'],'punch_out':None},{'$set':{'punch_out':ts,**vals}},return_document=ReturnDocument.AFTER)
    if not updated:
        if db.attendance_logs.find_one({'_id':doc['_id'],'punch_out':{'$ne':None}}): raise HTTPException(409,'already punched out')
        raise HTTPException(409,'record changed concurrently')
    return record_out(updated)
@app.get('/attendance')
def list_attendance(emp_code:Optional[str]=None,date_from:Optional[str]=None,date_to:Optional[str]=None,status:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    if date_from: parse_date(date_from)
    if date_to: parse_date(date_to)
    if date_from and date_to and date_from>date_to: raise HTTPException(422,'date_from must be <= date_to')
    q={}
    if emp_code is not None:q['emp_code']=emp_code
    if status is not None:
        if status not in STATUSES: raise HTTPException(422,'invalid status')
        q['status']=status
    if date_from or date_to:
        q['date']={}
        if date_from:q['date']['$gte']=date_from
        if date_to:q['date']['$lte']=date_to
    total=db.attendance_logs.count_documents(q)
    docs=db.attendance_logs.find(q).sort([('date',DESCENDING),('emp_code',ASCENDING)]).skip((page-1)*page_size).limit(page_size)
    return {'items':[record_out(x) for x in docs],'total':total,'page':page,'page_size':page_size}
@app.patch('/attendance/{emp_code}/{date}')
def regularize(emp_code:str,date:str,body:RegularizeIn):
    parse_date(date); emp=db.employees.find_one({'emp_code':emp_code})
    if not emp: raise HTTPException(404,'employee not found')
    old=db.attendance_logs.find_one({'emp_code':emp_code,'date':date})
    if not old: raise HTTPException(404,'attendance record not found')
    status=body.status if body.status is not None else old['status']
    pin=epoch_dt(body.punch_in) if body.punch_in is not None else old.get('punch_in')
    pout=epoch_dt(body.punch_out) if body.punch_out is not None else old.get('punch_out')
    if status in {'ABSENT','LEAVE'}:
        if body.punch_in is not None or body.punch_out is not None: raise HTTPException(422,'ABSENT/LEAVE cannot include punch times')
        pin=pout=None
    elif pin is None: raise HTTPException(422,'presence status requires punch_in')
    if pin and attendance_date(pin,emp)!=date: raise HTTPException(422,'punch_in must belong to attendance date')
    if pout and not pin: raise HTTPException(422,'punch_out requires punch_in')
    if pin and pout and (pout<=pin or pout-pin>timedelta(hours=24)): raise HTTPException(422,'punch_out must be after punch_in and within 24 hours')
    vals=derived(emp,date,pin,pout) if status in PRESENCE else {'late_minutes':0,'work_hours':None,'overtime_minutes':0,'half_day':False}
    new={'status':status,'punch_in':pin,'punch_out':pout,**vals}
    fields=['status','punch_in','punch_out','work_hours','late_minutes','overtime_minutes','half_day']; changes={}
    for k in fields:
        a=old.get(k,False if k=='half_day' else 0 if k in ('late_minutes','overtime_minutes') else None); b=new[k]
        if a!=b: changes[k]={'from':a,'to':b}
    if not changes: raise HTTPException(422,'request does not change record')
    entry={'at':now_utc(),'by':body.regularized_by,'reason':body.reason,'changes':changes}
    history_filter={'$or':[{'history':old.get('history',[])},{'history':{'$exists':False}}]} if 'history' not in old else {'history':old['history']}
    updated=db.attendance_logs.find_one_and_update({'_id':old['_id'],**history_filter}, {'$set':new}, {'$push':{'history':entry}},return_document=ReturnDocument.AFTER)
    if not updated: raise HTTPException(409,'record changed concurrently')
    return record_out(updated)

# Analytics are aggregation-backed. Decimal rounding is applied at the API boundary.
def month_match(month):
    start,end=parse_month(month); return start.isoformat(),end.isoformat()
def present_expr(): return {'$cond':[{'$in':['$status',list(PRESENCE)]},{'$cond':[{'$eq':['$half_day',True]},0.5,1]},0]}
@app.get('/analytics/employees/{emp_code}/monthly')
def employee_monthly(emp_code:str,month:str):
    start,end=parse_month(month); emp=db.employees.find_one({'emp_code':emp_code})
    if not emp: raise HTTPException(404,'unknown employee')
    joined=date.fromisoformat(emp['joined_on']); first=max(start,joined); wd=0; d=first
    while d<=end:
        wd+=d.weekday()<5; d+=timedelta(days=1)
    pipeline=[{'$match':{'emp_code':emp_code,'date':{'$gte':start.isoformat(),'$lte':end.isoformat()}}},{'$group':{'_id':None,'present_days':{'$sum':{'$cond':[{'$and':[{'$in':['$status',list(PRESENCE)]},{'$ne':[{'$dayOfWeek':{'$dateFromString':{'dateString':'$date'}}},1]},{'$ne':[{'$dayOfWeek':{'$dateFromString':{'dateString':'$date'}}},7]}]}, {'$cond':[{'$eq':['$half_day',True]},0.5,1]},0]}},'leave_days':{'$sum':{'$cond':[{'$eq':['$status','LEAVE']},1,0]}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$late_minutes',0]},0]},1,0]}},'total_late_minutes':{'$sum':{'$ifNull':['$late_minutes',0]}},'total_overtime_minutes':{'$sum':{'$ifNull':['$overtime_minutes',0]}}}}]
    r=list(db.attendance_logs.aggregate(pipeline)); x=r[0] if r else {}
    return {'emp_code':emp_code,'month':month,'working_days':wd,'present_days':round2(x.get('present_days',0)),'leave_days':x.get('leave_days',0),'late_count':x.get('late_count',0),'total_late_minutes':x.get('total_late_minutes',0),'total_overtime_minutes':x.get('total_overtime_minutes',0),'attendance_pct':round4(x.get('present_days',0)/wd*100) if wd else None}
@app.get('/analytics/departments/summary')
def department_summary(month:str,department:Optional[str]=None):
    start,end=parse_month(month); eq={'$eq':['$department',department]} if department is not None else None
    # Employee-first pipeline preserves employees without logs in headcount.
    pipeline=[{'$match':{'joined_on':{'$lte':end.isoformat()},**({'department':department} if department is not None else {})}},{'$lookup':{'from':'attendance_logs','let':{'code':'$emp_code'},'pipeline':[{'$match':{'$expr':{'$and':[{'$eq':['$emp_code','$$code']},{'$gte':['$date',start.isoformat()]},{'$lte':['$date',end.isoformat()]}]}}},{'$project':{'status':1,'date':1,'half_day':1,'work_hours':1,'late_minutes':1,'overtime_minutes':1}}],'as':'logs'}},{'$unwind':{'path':'$logs','preserveNullAndEmptyArrays':True}},{'$group':{'_id':'$department','headcount':{'$addToSet':'$emp_code'},'present_days':{'$sum':{'$cond':[{'$and':[{'$in':['$logs.status',list(PRESENCE)]},{'$not':[{'$in':[{'$dayOfWeek':{'$dateFromString':{'dateString':'$logs.date'} }},[1,7]]}]}]},{'$cond':[{'$eq':['$logs.half_day',True]},0.5,1]},0]}},'hours':{'$push':{'$cond':[{'$and':[{'$in':['$logs.status',list(PRESENCE)]},{'$ne':[{'$ifNull':['$logs.work_hours',None]},None]}]},'$logs.work_hours',None]}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$logs.late_minutes',0]},0]},1,0]}},'total_late_minutes':{'$sum':{'$ifNull':['$logs.late_minutes',0]}},'leave_count':{'$sum':{'$cond':[{'$eq':['$logs.status','LEAVE']},1,0]}},'on_duty_count':{'$sum':{'$cond':[{'$eq':['$logs.status','ON_DUTY']},1,0]}}}},{'$project':{'department':'$_id','headcount':{'$size':'$headcount'},'present_days':1,'avg_work_hours':{'$avg':'$hours'},'late_count':1,'total_late_minutes':1,'leave_count':1,'on_duty_count':1,'_id':0}},{'$match':{'headcount':{'$gt':0}}},{'$sort':{'department':1}}]
    items=[]
    for x in db.employees.aggregate(pipeline):
        x['present_days']=round2(x['present_days']); x['avg_work_hours']=round2(x['avg_work_hours']) if x.get('avg_work_hours') is not None else None; items.append(x)
    return {'month':month,'items':items}
@app.get('/analytics/leaderboard/late')
def late_leaderboard(month:str,limit:int=Query(10,ge=1,le=50),department:Optional[str]=None):
    start,end=parse_month(month)
    pipe=[{'$match':{'date':{'$gte':start.isoformat(),'$lte':end.isoformat()},'late_minutes':{'$gt':0}}},{'$group':{'_id':'$emp_code','total_late_minutes':{'$sum':'$late_minutes'},'late_count':{'$sum':1}}},{'$lookup':{'from':'employees','localField':'_id','foreignField':'emp_code','as':'emp'}},{'$unwind':'$emp'}]
    if department is not None: pipe.append({'$match':{'emp.department':department}})
    pipe += [{'$sort':{'total_late_minutes':-1,'_id':1}},{'$setWindowFields':{'sortBy':{'total_late_minutes':-1},'output':{'rank':{'$rank':{}}}}},{'$match':{'rank':{'$lte':limit}}},{'$project':{'_id':0,'emp_code':'$_id','name':'$emp.name','department':'$emp.department','total_late_minutes':1,'late_count':1,'rank':1}},{'$sort':{'total_late_minutes':-1,'emp_code':1}}]
    return {'month':month,'items':list(db.attendance_logs.aggregate(pipe))}
@app.get('/analytics/departments/{department}/trend')
def department_trend(department:str,from_:str=Query(alias='from'),to:str=Query(...)):
    start=parse_date(from_); end=parse_date(to)
    if end<start or (end-start).days+1>92:
        raise HTTPException(422,'invalid date range; maximum 92 days')
    if not db.employees.find_one({'department':department}):
        raise HTTPException(404,'unknown department')

    # Start with a seed date at the database, then densify. This also returns
    # every day when the attendance collection has no matching documents.
    seed=datetime.combine(start,time(),UTC)
    pipeline=[
      {'$documents':[{'date':seed}]},
      {'$densify':{'field':'date','range':{'step':1,'unit':'day','bounds':[seed,datetime.combine(end,time(),UTC)]}}},
      {'$set':{'date_str':{'$dateToString':{'date':'$date','format':'%Y-%m-%d','timezone':'UTC'}}}},
      {'$lookup':{'from':'attendance_logs','let':{'d':'$date_str'},'pipeline':[
          {'$match':{'$expr':{'$eq':['$date','$$d']}}},
          {'$lookup':{'from':'employees','localField':'emp_code','foreignField':'emp_code','as':'employee'}},
          {'$unwind':'$employee'}, {'$match':{'employee.department':department}},
          {'$group':{'_id':None,
            'present_count':{'$sum':{'$cond':[{'$in':['$status',list(PRESENCE)]},{'$cond':[{'$eq':['$half_day',True]},0.5,1]},0]}},
            'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$late_minutes',0]},0]},1,0]}}}}
        ],'as':'daily'}},
      {'$lookup':{'from':'employees','let':{'d':'$date_str'},'pipeline':[
          {'$match':{'$expr':{'$and':[{'$eq':['$department',department]},{'$lte':['$joined_on','$$d']}]}}},
          {'$count':'n'}
        ],'as':'hc'}},
      {'$set':{'present_count':{'$ifNull':[{'$arrayElemAt':['$daily.present_count',0]},0]},
               'late_count':{'$ifNull':[{'$arrayElemAt':['$daily.late_count',0]},0]},
               'headcount':{'$ifNull':[{'$arrayElemAt':['$hc.n',0]},0]},
               'is_working_day':{'$not':[{'$in':[{'$dayOfWeek':'$date'},[1,7]]}]}}},
      {'$set':{'attendance_rate':{'$cond':[{'$and':['$is_working_day',{'$gt':['$headcount',0]}]},{'$divide':['$present_count','$headcount']},None]}}},
      {'$setWindowFields':{'sortBy':{'date':1},'output':{'moving_avg_7d':{'$avg':'$attendance_rate','window':{'documents':[-6,0]}}}}},
      {'$project':{'_id':0,'date':'$date_str','is_working_day':1,'headcount':1,'present_count':1,'late_count':1,'attendance_rate':1,'moving_avg_7d':1}},
      {'$sort':{'date':1}}
    ]
    items=[]
    for x in db.aggregate(pipeline):
        for k in ('attendance_rate','moving_avg_7d'):
            if x.get(k) is not None: x[k]=round4(x[k])
        x['present_count']=round2(x['present_count'])
        items.append(x)
    return {'department':department,'items':items}
@app.get('/admin/explain/{endpoint}')
def explain_endpoint(endpoint:str,emp_code:Optional[str]=None,month:Optional[str]=None,department:Optional[str]=None,limit:int=Query(10,ge=1,le=50),date_from:Optional[str]=None,date_to:Optional[str]=None,status:Optional[str]=None,from_:Optional[str]=Query(None,alias='from'),to:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    """Explain the same principal MongoDB operation used by the named route."""
    allowed={'attendance_list','employee_monthly','department_summary','late_leaderboard','department_trend'}
    if endpoint not in allowed: raise HTTPException(422,'unsupported endpoint')
    if endpoint=='attendance_list':
        if date_from: parse_date(date_from)
        if date_to: parse_date(date_to)
        if date_from and date_to and date_from>date_to: raise HTTPException(422,'date_from must be <= date_to')
        if status is not None and status not in STATUSES: raise HTTPException(422,'invalid status')
        q={}
        if emp_code is not None:q['emp_code']=emp_code
        if status is not None:q['status']=status
        if date_from or date_to:
            q['date']={}
            if date_from:q['date']['$gte']=date_from
            if date_to:q['date']['$lte']=date_to
        cmd={'find':'attendance_logs','filter':q,'sort':{'date':-1,'emp_code':1},'skip':(page-1)*page_size,'limit':page_size}
        raw=db.command('explain',cmd,verbosity='executionStats'); coll='attendance_logs'
    elif endpoint=='department_trend':
        if not department or not from_ or not to: raise HTTPException(422,'department, from and to are required')
        # Same date validation and range constraint as the trend route.
        ds=parse_date(from_); de=parse_date(to)
        if de<ds or (de-ds).days+1>92: raise HTTPException(422,'invalid date range; maximum 92 days')
        seed=datetime.combine(ds,time(),UTC)
        pipe=[
          {'$documents':[{'date':seed}]},
          {'$densify':{'field':'date','range':{'step':1,'unit':'day','bounds':[seed,datetime.combine(de,time(),UTC)]}}},
          {'$set':{'date_str':{'$dateToString':{'date':'$date','format':'%Y-%m-%d','timezone':'UTC'}}}},
          {'$lookup':{'from':'attendance_logs','let':{'d':'$date_str'},'pipeline':[
            {'$match':{'$expr':{'$eq':['$date','$$d']}}},
            {'$lookup':{'from':'employees','localField':'emp_code','foreignField':'emp_code','as':'employee'}},
            {'$unwind':'$employee'},{'$match':{'employee.department':department}},
            {'$group':{'_id':None,'present_count':{'$sum':{'$cond':[{'$in':['$status',list(PRESENCE)]},{'$cond':[{'$eq':['$half_day',True]},0.5,1]},0]}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$late_minutes',0]},0]},1,0]}}}}
          ],'as':'daily'}},
          {'$lookup':{'from':'employees','let':{'d':'$date_str'},'pipeline':[{'$match':{'$expr':{'$and':[{'$eq':['$department',department]},{'$lte':['$joined_on','$$d']}]}}},{'$count':'n'}],'as':'hc'}},
          {'$set':{'present_count':{'$ifNull':[{'$arrayElemAt':['$daily.present_count',0]},0]},'late_count':{'$ifNull':[{'$arrayElemAt':['$daily.late_count',0]},0]},'headcount':{'$ifNull':[{'$arrayElemAt':['$hc.n',0]},0]},'is_working_day':{'$not':[{'$in':[{'$dayOfWeek':'$date'},[1,7]]}]}}},
          {'$set':{'attendance_rate':{'$cond':[{'$and':['$is_working_day',{'$gt':['$headcount',0]}]},{'$divide':['$present_count','$headcount']},None]}}},
          {'$setWindowFields':{'sortBy':{'date':1},'output':{'moving_avg_7d':{'$avg':'$attendance_rate','window':{'documents':[-6,0]}}}}},
          {'$project':{'_id':0,'date':'$date_str','is_working_day':1,'headcount':1,'present_count':1,'late_count':1,'attendance_rate':1,'moving_avg_7d':1}},{'$sort':{'date':1}}
        ]
        raw=db.command('explain',{'aggregate':1,'pipeline':pipe,'cursor':{}},verbosity='executionStats'); coll='attendance_logs'
    else:
        if not month: raise HTTPException(422,'month is required')
        start,end=parse_month(month)
        if endpoint=='employee_monthly':
            if not emp_code: raise HTTPException(422,'emp_code is required')
            pipe=[{'$match':{'emp_code':emp_code,'date':{'$gte':start.isoformat(),'$lte':end.isoformat()}}},{'$group':{'_id':None,'present_days':{'$sum':{'$cond':[{'$and':[{'$in':['$status',list(PRESENCE)]},{'$ne':[{'$dayOfWeek':{'$dateFromString':{'dateString':'$date'}}},1]},{'$ne':[{'$dayOfWeek':{'$dateFromString':{'dateString':'$date'}}},7]}]},{'$cond':[{'$eq':['$half_day',True]},0.5,1]},0]}},'leave_days':{'$sum':{'$cond':[{'$eq':['$status','LEAVE']},1,0]}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$late_minutes',0]},0]},1,0]}},'total_late_minutes':{'$sum':{'$ifNull':['$late_minutes',0]}},'total_overtime_minutes':{'$sum':{'$ifNull':['$overtime_minutes',0]}}}}]
            coll='attendance_logs'
        elif endpoint=='department_summary':
            pipe=[{'$match':{'joined_on':{'$lte':end.isoformat()},**({'department':department} if department is not None else {})}},{'$lookup':{'from':'attendance_logs','let':{'code':'$emp_code'},'pipeline':[{'$match':{'$expr':{'$and':[{'$eq':['$emp_code','$$code']},{'$gte':['$date',start.isoformat()]},{'$lte':['$date',end.isoformat()]}]}}},{'$project':{'status':1,'date':1,'half_day':1,'work_hours':1,'late_minutes':1,'overtime_minutes':1}}],'as':'logs'}},{'$unwind':{'path':'$logs','preserveNullAndEmptyArrays':True}},{'$group':{'_id':'$department','headcount':{'$addToSet':'$emp_code'},'present_days':{'$sum':{'$cond':[{'$and':[{'$in':['$logs.status',list(PRESENCE)]},{'$not':[{'$in':[{'$dayOfWeek':{'$dateFromString':{'dateString':'$logs.date'}}},[1,7]]}]}]},{'$cond':[{'$eq':['$logs.half_day',True]},0.5,1]},0]}},'hours':{'$push':{'$cond':[{'$and':[{'$in':['$logs.status',list(PRESENCE)]},{'$ne':[{'$ifNull':['$logs.work_hours',None]},None]}]},'$logs.work_hours',None]}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$logs.late_minutes',0]},0]},1,0]}},'total_late_minutes':{'$sum':{'$ifNull':['$logs.late_minutes',0]}},'leave_count':{'$sum':{'$cond':[{'$eq':['$logs.status','LEAVE']},1,0]}},'on_duty_count':{'$sum':{'$cond':[{'$eq':['$logs.status','ON_DUTY']},1,0]}}}},{'$project':{'department':'$_id','headcount':{'$size':'$headcount'},'present_days':1,'avg_work_hours':{'$avg':'$hours'},'late_count':1,'total_late_minutes':1,'leave_count':1,'on_duty_count':1,'_id':0}},{'$match':{'headcount':{'$gt':0}}},{'$sort':{'department':1}}]
            coll='employees'
        else:
            pipe=[{'$match':{'date':{'$gte':start.isoformat(),'$lte':end.isoformat()},'late_minutes':{'$gt':0}}},{'$group':{'_id':'$emp_code','total_late_minutes':{'$sum':'$late_minutes'},'late_count':{'$sum':1}}},{'$lookup':{'from':'employees','localField':'_id','foreignField':'emp_code','as':'emp'}},{'$unwind':'$emp'}]
            if department is not None: pipe.append({'$match':{'emp.department':department}})
            pipe += [{'$sort':{'total_late_minutes':-1,'_id':1}},{'$setWindowFields':{'sortBy':{'total_late_minutes':-1},'output':{'rank':{'$rank':{}}}}},{'$match':{'rank':{'$lte':limit}}},{'$project':{'_id':0,'emp_code':'$_id','name':'$emp.name','department':'$emp.department','total_late_minutes':1,'late_count':1,'rank':1}},{'$sort':{'total_late_minutes':-1,'emp_code':1}}]
            coll='attendance_logs'
        raw=db.command('explain',{'aggregate':coll,'pipeline':pipe,'cursor':{}},verbosity='executionStats')
    return {'endpoint':endpoint,'collection':coll,'explain':__import__('json').loads(json_util.dumps(raw))}
