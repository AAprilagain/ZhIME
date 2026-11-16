import json
import sqlite3
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen
from urllib.error import HTTPError
import pytest
from zhime.engine import Engine
from zhime.independent_server import Handler

def test_dictionary_survives_missing_model_and_validates_requests(tmp_path):
    path=tmp_path/'lex.sqlite';db=sqlite3.connect(path);db.execute('CREATE TABLE entries(pinyin TEXT,text TEXT,weight REAL)')
    db.executemany('INSERT INTO entries VALUES(?,?,?)',[('ni hao','你好',100),('ni hao','拟好',10)]);db.commit();db.close()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.engine=Engine(path);server.model=None
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    base='http://127.0.0.1:'+str(server.server_port)
    try:
        assert json.load(urlopen(base+'/health'))['model'] is False
        value=json.load(urlopen(base+'/query?keys=nihao&rank=1&format=json&id=42'))
        assert value['request_id']==42 and value['status']=='fallback:model_unavailable'
        assert value['candidates'][0]['text']=='你好'
        value=json.load(urlopen(base+'/query?keys=nh&format=json'))
        assert value['candidates'][0]['text']=='你好'
        value=json.load(urlopen(base+'/query?keys=nh&format=json&abbreviated=0'))
        assert value['candidates']==[]
        value=json.load(urlopen(base+'/suggest?context=e4bda0e5a5bd&format=json&id=43&session=test'))
        assert value=={'session':'test','request_id':43,'status':'fallback:model_unavailable','suggestions':[]}
        server.model = object()
        server.lock = threading.Lock()
        with server.lock:
            value=json.load(urlopen(base+'/suggest?context=e4bda0e5a5bd&format=json&id=44'))
        assert value['status']=='fallback:model_busy' and value['suggestions']==[]
        with pytest.raises(HTTPError) as error:urlopen(base+'/query?keys=nihao&fuzzy=99')
        assert error.value.code==400
    finally:server.shutdown();server.server_close();worker.join()
