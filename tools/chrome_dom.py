"""Inspect only the dedicated, local Chromium acceptance page over loopback CDP."""
import argparse,json,urllib.request
import websocket
p=argparse.ArgumentParser();p.add_argument('action',choices=['focus','read','close','reset']);a=p.parse_args()
pages=json.load(urllib.request.urlopen('http://127.0.0.1:18780/json/list'))
page=next(x for x in pages if x.get('url','').endswith('/runtime/browser-acceptance.html'))
ws=websocket.create_connection(page['webSocketDebuggerUrl'],suppress_origin=True,timeout=5)
if a.action=='close':message={'id':1,'method':'Page.close'}
else:
 expression="document.getElementById('ime').value=''; document.getElementById('ime').focus(); document.activeElement.id" if a.action=='reset' else "document.getElementById('ime').focus(); document.activeElement.id" if a.action=='focus' else "document.getElementById('ime').value"
 message={'id':1,'method':'Runtime.evaluate','params':{'expression':expression,'returnByValue':True}}
ws.send(json.dumps(message))
try:
 while True:
    result=json.loads(ws.recv())
    if result.get('id')==1:
        print(json.dumps(result,ensure_ascii=True));break
finally:ws.close()
