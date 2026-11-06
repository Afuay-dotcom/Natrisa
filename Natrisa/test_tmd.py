from datetime import datetime, timezone, timedelta
import tmd_forecast as tf
now=datetime(2026,10,12,2,0,tzinfo=timezone(timedelta(hours=7)))
class R:
    def __init__(s,c,j): s.status_code=c; s._j=j
    def json(s): return s._j
def mk(key): return {key:[{"location":{},"forecasts":[{"time":f"t{i}","data":{"rain":v}} for i,v in enumerate([0,5,40,0.5])]}]}
cap={}
def get(u,params=None,headers=None,timeout=0): cap.update(p=params); return R(200,mk("WeatherForcasts"))
r=tf.get_forecast_rain(1,2,get=get,now=now,token="x")
assert r["rain_mm"]==45.5 and r["max_hour_mm"]==40 and r["level"]=="ฝนหนัก", r
assert cap["p"]["hour"]==2 and cap["p"]["date"]=="2026-10-12" and cap["p"]["fields"]=="rain"
assert tf.get_forecast_rain(1,2,get=lambda *a,**k:R(200,{"weather_forecast":{"locations":mk("x")["x"]}}),now=now,token="x")["hours"]==4
assert tf.get_forecast_rain(1,2,get=lambda *a,**k:R(401,{}),now=now,token="x") is None
def boom(*a,**k): raise RuntimeError("secret")
assert tf.get_forecast_rain(1,2,get=boom,now=now,token="x") is None
assert tf.get_forecast_rain(1,2,token="") is None
assert tf.format_forecast_rain_lines(None)==[]
print(tf.format_forecast_rain_lines(r)[0]); print("ok")
