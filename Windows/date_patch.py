"""Localize the English birth-date column only. All other protobuf bytes stay exact."""
import hashlib
import re
from datetime import date
NAME = 'StreamingAssets/DataConfig/tnt_deploy_data44.data'
ORIGINAL_SHA = 'c61fe305cb04f62eda7c2bfb58c91b3ba10e68885767f2ceaab5ab6090ced572'

def fields(blob, start=0, end=None):
    end=len(blob) if end is None else end
    def integer(pos):
        val=shift=0
        while True:
            c=blob[pos];pos+=1;val|=(c&127)<<shift
            if c<128:return val,pos
            shift+=7
            if shift>63:raise ValueError('Invalid protobuf')
    pos=start
    while pos<end:
        tag,pos=integer(pos); wire=tag&7
        if wire==2:
            size,pos=integer(pos);a=pos;pos+=size
            if pos>end:raise ValueError('Invalid length')
            yield tag>>3,a,pos
        elif wire==0:_,pos=integer(pos)
        elif wire in (1,5):pos+=8 if wire==1 else 4
        else:raise ValueError('Unknown wire type')
    if pos!=end:raise ValueError('Invalid protobuf boundary')

def patch_dates(source):
    if hashlib.sha256(source).hexdigest()!=ORIGINAL_SHA:
        raise ValueError('Таблица дат Titan отличается от поддерживаемой версии')
    result=bytearray(source);changes=[]
    for _,start,end in fields(source):
        fs={n:(a,b) for n,a,b in fields(source,start,end)}
        if 8 not in fs:continue
        a,b=fs[8];old=source[a:b].decode('utf-8')
        if old in ('','null'):continue
        if not re.fullmatch(r'\d{2}\.\d{2}\.\d{4}',old):raise ValueError('Unknown English birth format')
        m,d,y=map(int,old.split('.'));dt=date(y,m,d)
        # The independent Chinese column confirms the calendar value.
        x,z=fs[7]
        if source[x:z].decode()!=dt.strftime('%Y.%m.%d'):
            # Shipped Shirley record differs by year between languages; retain EN calendar value.
            if (old,source[x:z].decode())!=('09.28.1989','1991.09.28'):raise ValueError('Birth columns disagree')
        new=dt.strftime('%d.%m.%Y').encode();assert len(new)==b-a
        result[a:b]=new;changes.append((a,b,old,new.decode()))
    if len(changes)!=38:raise ValueError('Unexpected Titan birth-date count')
    reverse=bytearray(result)
    for a,b,old,new in changes:reverse[a:b]=old.encode()
    assert bytes(reverse)==source
    return bytes(result),changes
