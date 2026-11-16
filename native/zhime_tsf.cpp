// Independent x64 Windows TSF input processor. No Rime runtime dependency.
#include <windows.h>
#include <msctf.h>
#include <winhttp.h>
#include <atomic>
#include <thread>
#include <mutex>
#include <condition_variable>
#include <string>
#include <vector>
#include <sstream>
#include <algorithm>
#include <filesystem>

static const CLSID CLSID_ZhIME={0xc8470792,0xf77d,0x4f13,{0x98,0xa4,0xb2,0x6c,0x7c,0x5e,0x61,0x39}};
static const GUID PROFILE_ZhIME={0x50e39d92,0x7271,0x437d,{0x9e,0x5c,0xda,0x51,0xa9,0x8a,0xc1,0xd2}};
static HINSTANCE instance=nullptr;
static std::atomic<long> objects{0};
static const UINT WM_RESULT=WM_APP+17;
static const UINT_PTR FOREGROUND_TIMER=1;
static std::string utf8(const std::wstring& s){int n=WideCharToMultiByte(CP_UTF8,0,s.data(),(int)s.size(),nullptr,0,nullptr,nullptr);std::string out(n,0);if(n)WideCharToMultiByte(CP_UTF8,0,s.data(),(int)s.size(),out.data(),n,nullptr,nullptr);return out;}
static std::wstring wide(const std::string& s){int n=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,s.data(),(int)s.size(),nullptr,0);std::wstring out(n,0);if(n)MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,s.data(),(int)s.size(),out.data(),n);return out;}
static std::wstring hex(const std::wstring& s){static const wchar_t* digits=L"0123456789abcdef";std::wstring out;for(unsigned char c:utf8(s)){out+=digits[c>>4];out+=digits[c&15];}return out;}
template<class T> static void release(T*& p){if(p){p->Release();p=nullptr;}}
struct Entry{std::wstring text;size_t consumed=0;};
struct Result{unsigned long long id=0;std::wstring status;std::vector<Entry> entries;};
struct Query{unsigned long long id=0;std::wstring keys,context;unsigned mask=0;std::wstring session;};
static GUID shortcutGuid(unsigned action){GUID g=CLSID_ZhIME;g.Data1=0xc8470800+action;return g;}
static TF_PRESERVEDKEY shortcutKey(unsigned action){
  TF_PRESERVEDKEY k{};
  if(action==0){k.uVKey=VK_SPACE;k.uModifiers=TF_MOD_CONTROL;}
  else if(action==1){k.uVKey='P';k.uModifiers=TF_MOD_CONTROL|TF_MOD_SHIFT;}
  else if(action==2){k.uVKey=VK_BACK;k.uModifiers=TF_MOD_CONTROL|TF_MOD_SHIFT;}
  else{k.uVKey='1'+action-3;k.uModifiers=TF_MOD_CONTROL|TF_MOD_ALT;}
  return k;
}

static Result* fetch(const Query& q,bool rank,bool suggestion=false){
  auto result=new Result;result->id=q.id;result->status=L"引擎未连接";
  HINTERNET session=WinHttpOpen(L"ZhIME/1",WINHTTP_ACCESS_TYPE_NO_PROXY,WINHTTP_NO_PROXY_NAME,WINHTTP_NO_PROXY_BYPASS,0);
  if(!session)return result;
  if(suggestion)WinHttpSetTimeouts(session,100,100,100,350);
  else WinHttpSetTimeouts(session,500,500,500,rank?1500:1000);
  HINTERNET connection=WinHttpConnect(session,L"127.0.0.1",18770,0);
  std::wstring url=(suggestion?L"/suggest?keys=":L"/query?keys=")+q.keys+L"&fuzzy="+std::to_wstring(q.mask)+L"&id="+std::to_wstring(q.id)+L"&context="+hex(q.context)+L"&rank="+(rank?L"1":L"0")+L"&session="+q.session;
  HINTERNET request=connection?WinHttpOpenRequest(connection,L"GET",url.c_str(),nullptr,WINHTTP_NO_REFERER,WINHTTP_DEFAULT_ACCEPT_TYPES,0):nullptr;
  std::string body;
  if(request && WinHttpSendRequest(request,WINHTTP_NO_ADDITIONAL_HEADERS,0,WINHTTP_NO_REQUEST_DATA,0,0,0)&&WinHttpReceiveResponse(request,nullptr)){
    DWORD code=0,size=sizeof(code);WinHttpQueryHeaders(request,WINHTTP_QUERY_STATUS_CODE|WINHTTP_QUERY_FLAG_NUMBER,WINHTTP_HEADER_NAME_BY_INDEX,&code,&size,WINHTTP_NO_HEADER_INDEX);
    if(code==200){char buf[4096];DWORD count=0;while(body.size()<65536&&WinHttpReadData(request,buf,sizeof(buf),&count)&&count)body.append(buf,count);}
  }else if(rank&&request&&GetLastError()==ERROR_WINHTTP_TIMEOUT)result->status=L"模型超时";
  if(request)WinHttpCloseHandle(request);if(connection)WinHttpCloseHandle(connection);WinHttpCloseHandle(session);
  if(!body.empty()){
    std::istringstream stream(body);std::string line;
    if(!std::getline(stream,line))return result;
    auto header=line.find('\t');
    if(header==std::string::npos||line.substr(0,header)!=std::to_string(q.id))return result;
    result->status=wide(line.substr(header+1));
    if(suggestion&&result->status!=L"suggestion")return result;
    while(std::getline(stream,line)&&result->entries.size()<(suggestion?3u:1024u)){auto tab=line.find('\t');if(tab==std::string::npos)continue;
      try{size_t used=std::stoul(line.substr(tab+1));auto text=wide(line.substr(0,tab));if(!text.empty()&&(suggestion?used==0:used>0&&used<=q.keys.size()))result->entries.push_back({text,used});}catch(...){}
    }
  }
  return result;
}

class Service;
class Edit:public ITfEditSession{
  std::atomic<ULONG> refs{1};Service* owner;std::wstring text;bool commit,cancel,predict;unsigned long long version;ITfRange* anchor;
public:
  Edit(Service* s,std::wstring t,bool c,bool x,unsigned long long v,bool predict,ITfRange* anchor);
  ~Edit();
  STDMETHODIMP QueryInterface(REFIID iid,void** pp)override{if(!pp)return E_POINTER;*pp=nullptr;if(iid==IID_IUnknown||iid==IID_ITfEditSession){*pp=static_cast<ITfEditSession*>(this);AddRef();return S_OK;}return E_NOINTERFACE;}
  STDMETHODIMP_(ULONG) AddRef()override{return ++refs;}
  STDMETHODIMP_(ULONG) Release()override{ULONG n=--refs;if(!n)delete this;return n;}
  STDMETHODIMP DoEditSession(TfEditCookie cookie)override;
};

class Service:public ITfTextInputProcessor,public ITfKeyEventSink,public ITfThreadMgrEventSink,public ITfCompositionSink{
  std::atomic<ULONG> refs{1};ITfThreadMgr* manager=nullptr;ITfContext* context=nullptr;ITfComposition* composition=nullptr;TfClientId client=TF_CLIENTID_NULL;DWORD threadCookie=TF_INVALID_COOKIE;
  HWND window=nullptr;HFONT font=nullptr;std::wstring keys,history,status=L"词典";size_t caret=0,selected=0;unsigned mask=0;bool english=false,chinesePunct=true,locked=false,shiftOnly=false,quoteOpen=true,singleOpen=true;
  std::atomic<unsigned long long> version{0};std::vector<Entry> entries;
  std::thread dictionaryWorker,rankWorker,suggestionWorker;
  std::mutex mutex;std::condition_variable cv;
  bool stop=false,pendingDictionary=false,pendingRank=false,pendingSuggestion=false;
  Query latest,rankQuery,suggestionQuery;
  bool suggestionsEnabled=true,awaitingSuggestion=false,showingSuggestion=false;
  HANDLE automaticProcess=nullptr;
  ULONGLONG nextAutomaticLaunch=0;
  std::filesystem::path automaticRoot,automaticPython;
  HWND suggestionForeground=nullptr;
  void findAutomaticRuntime(){
    wchar_t module[MAX_PATH];
    if(!GetModuleFileNameW(instance,module,MAX_PATH))return;
    auto root=std::filesystem::path(module).parent_path();
    for(int level=0;level<4&&!root.empty();++level,root=root.parent_path()){
      if(GetFileAttributesW((root/L"automatic-start.enabled").c_str())==INVALID_FILE_ATTRIBUTES)continue;
      auto python=root/L"python"/L"pythonw.exe";
      if(GetFileAttributesW(python.c_str())==INVALID_FILE_ATTRIBUTES)
        python=root/L".venv-cuda"/L"Scripts"/L"pythonw.exe";
      if(GetFileAttributesW(python.c_str())!=INVALID_FILE_ATTRIBUTES){automaticRoot=root;automaticPython=python;}
      break;
    }
  }
  void refreshAutomaticRuntime(){
    if(automaticRoot.empty()||!manager)return;
    HWND foreground=GetForegroundWindow();
    if(!foreground)return;
    DWORD foregroundThread=GetWindowThreadProcessId(foreground,nullptr);
    GUITHREADINFO gui{};
    gui.cbSize=sizeof(gui);
    // WinUI can host the focused editor on a thread separate from its frame.
    if(GetGUIThreadInfo(foregroundThread,&gui)&&gui.hwndFocus)
      foregroundThread=GetWindowThreadProcessId(gui.hwndFocus,nullptr);
    if(foregroundThread!=GetCurrentThreadId())return;
    ITfKeystrokeMgr* keysManager=nullptr;
    CLSID active{};
    if(FAILED(manager->QueryInterface(IID_ITfKeystrokeMgr,(void**)&keysManager)))return;
    HRESULT current=keysManager->GetForeground(&active);
    keysManager->Release();
    if(FAILED(current)||active!=CLSID_ZhIME)return;
    auto lease=automaticRoot/L"runtime"/L"foreground.lease";
    HANDLE file=CreateFileW(lease.c_str(),FILE_WRITE_ATTRIBUTES,
                           FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,nullptr,OPEN_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL,nullptr);
    if(file==INVALID_HANDLE_VALUE)return;
    FILETIME now;GetSystemTimeAsFileTime(&now);SetFileTime(file,nullptr,nullptr,&now);CloseHandle(file);
    if(automaticProcess&&WaitForSingleObject(automaticProcess,0)==WAIT_OBJECT_0){
      CloseHandle(automaticProcess);automaticProcess=nullptr;
    }
    if(!automaticProcess&&GetTickCount64()>=nextAutomaticLaunch){
      nextAutomaticLaunch=GetTickCount64()+2000;
      HANDLE existing=OpenMutexW(SYNCHRONIZE,FALSE,L"Local\\ZhIME.Autoservice.18770");
      if(existing)CloseHandle(existing);
      else{
        std::wstring command=L"\""+automaticPython.wstring()+L"\" -m zhime.autostart";
        STARTUPINFOW start{};start.cb=sizeof(start);PROCESS_INFORMATION process{};
        if(CreateProcessW(automaticPython.c_str(),command.data(),nullptr,nullptr,FALSE,CREATE_NO_WINDOW,nullptr,
                          automaticRoot.c_str(),&start,&process)){
          automaticProcess=process.hProcess;CloseHandle(process.hThread);
        }
      }
    }
    if(keys.empty()||locked)return;
    if(status==L"fallback:model_unavailable"){
      status=L"词典";
      scheduleRank(version);
    }else if(status==L"引擎未连接"&&entries.empty())request(true);
  }
  ITfRange* suggestionAnchor=nullptr;
  bool preservedReady=false,inMenu=false;
  void trace(const char* action){
    wchar_t path[1024];if(!GetEnvironmentVariableW(L"ZHIME_DIAGNOSTIC_LOG",path,1024))return;
    HANDLE h=CreateFileW(path,FILE_APPEND_DATA,FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,OPEN_ALWAYS,FILE_ATTRIBUTE_NORMAL,nullptr);
    if(h!=INVALID_HANDLE_VALUE){std::string line=std::to_string(GetCurrentProcessId())+" "+action+" english="+std::to_string(english)+" fuzzy="+std::to_string(mask)+" punctuation="+std::to_string(chinesePunct)+" keys="+std::to_string(keys.size())+" tick="+std::to_string(GetTickCount64())+" request="+std::to_string(version.load())+"\r\n";DWORD n;WriteFile(h,line.data(),(DWORD)line.size(),&n,nullptr);CloseHandle(h);}
  }
  void candidateLoop(bool rank){
    for(;;){
      Query query;
      {
        std::unique_lock<std::mutex> lock(mutex);
        bool& pending=rank?pendingRank:pendingDictionary;
        cv.wait(lock,[&]{return stop||pending;});
        if(stop)return;
        query=rank?rankQuery:latest;
        pending=false;
      }
      if(query.id!=version)continue;
      auto result=fetch(query,rank);
      std::lock_guard<std::mutex> lock(mutex);
      if(stop||query.id!=version||!PostMessage(window,WM_RESULT,rank?1:0,(LPARAM)result))delete result;
      if(stop)return;
    }
  }
  void scheduleRank(unsigned long long id){
    {
      std::lock_guard<std::mutex> lock(mutex);
      if(stop||id!=latest.id)return;
      rankQuery=latest;
      pendingRank=true;
    }
    cv.notify_all();
  }
  void suggestionLoop(){
    for(;;){
      Query query;
      {std::unique_lock<std::mutex> lock(mutex);cv.wait(lock,[this]{return stop||pendingSuggestion;});
        if(stop)return;query=suggestionQuery;pendingSuggestion=false;}
      auto result=fetch(query,false,true);
      std::lock_guard<std::mutex> lock(mutex);
      if(stop||!PostMessage(window,WM_RESULT,2,(LPARAM)result))delete result;
      if(stop)return;
    }
  }
  void dismissSuggestion(){
    if(!awaitingSuggestion&&!showingSuggestion)return;
    ++version;awaitingSuggestion=showingSuggestion=false;entries.clear();selected=0;
    release(suggestionAnchor);
    {std::lock_guard<std::mutex> lock(mutex);pendingSuggestion=false;}
    if(window)ShowWindow(window,SW_HIDE);
  }
  void suggestAfterCommit(ITfRange* selection){
    if(!suggestionsEnabled||english||history.size()<2)return;
    if(FAILED(selection->Clone(&suggestionAnchor)))return;
    awaitingSuggestion=true;suggestionForeground=GetForegroundWindow();
    Query query{version,L"",history,0,std::to_wstring(GetCurrentProcessId())+L"_"+std::to_wstring((uintptr_t)this)};
    {std::lock_guard<std::mutex> lock(mutex);suggestionQuery=std::move(query);pendingSuggestion=true;}
    cv.notify_all();
  }
  void request(bool reconnect=false){
    dismissSuggestion();
    ++version;locked=false;selected=0;entries.clear();status=reconnect?L"正在连接引擎":L"搜索中";
    if(keys.empty()){ShowWindow(window,SW_HIDE);return;}
    Query q{version,keys,history,mask,std::to_wstring(GetCurrentProcessId())+L"_"+std::to_wstring((uintptr_t)this)};
    {std::lock_guard<std::mutex> lock(mutex);latest=std::move(q);pendingDictionary=true;pendingRank=false;}
    trace("candidate_requested");cv.notify_all();show();
  }
  void show(){if(!window||(keys.empty()&&!showingSuggestion))return;InvalidateRect(window,nullptr,TRUE);ShowWindow(window,SW_SHOWNOACTIVATE);}
  void edit(const std::wstring& text,bool commit=false,bool cancel=false,bool predict=false,ITfRange* anchor=nullptr){
    if(!context){ITfDocumentMgr* doc=nullptr;if(manager&&SUCCEEDED(manager->GetFocus(&doc))&&doc){doc->GetTop(&context);doc->Release();}}
    if(!context)return;
    Edit* job=new Edit(this,text,commit,cancel,version,predict,anchor);HRESULT answer=E_FAIL;
    HRESULT hr=context->RequestEditSession(client,job,TF_ES_SYNC|TF_ES_READWRITE,&answer);
    if(FAILED(hr)||answer==TF_E_SYNCHRONOUS||answer==TS_E_READONLY)context->RequestEditSession(client,job,TF_ES_ASYNC|TF_ES_READWRITE,&answer);
    job->Release();
  }
  void changed(){request();if(keys.empty())edit(L"",true,true);else edit(keys);}
  void remember(const std::wstring& text){history+=text;if(history.size()>128)history.erase(0,history.size()-128);}
  void choose(size_t index,bool predict=true){
    if(index>=entries.size())return;auto item=entries[index];
    bool continuation=showingSuggestion;
    auto anchor=continuation?suggestionAnchor:nullptr;
    if(anchor)anchor->AddRef();
    std::wstring rest=keys.substr(item.consumed);
    dismissSuggestion();++version;
    keys.clear();caret=0;entries.clear();ShowWindow(window,SW_HIDE);
    if(!continuation||anchor)edit(item.text,true,false,predict&&rest.empty(),anchor);
    release(anchor);
    if(!rest.empty()){keys=rest;caret=keys.size();changed();}
  }
  void clear(bool terminate=true){dismissSuggestion();++version;if(terminate&&composition)edit(L"",true,true);keys.clear();caret=selected=0;entries.clear();locked=false;history.clear();if(window)ShowWindow(window,SW_HIDE);}
  static LRESULT CALLBACK wndproc(HWND hwnd,UINT msg,WPARAM wp,LPARAM lp){
    Service* s=(Service*)GetWindowLongPtr(hwnd,GWLP_USERDATA);
    if(msg==WM_NCCREATE){s=(Service*)((CREATESTRUCT*)lp)->lpCreateParams;SetWindowLongPtr(hwnd,GWLP_USERDATA,(LONG_PTR)s);}
    if(!s)return DefWindowProc(hwnd,msg,wp,lp);
    if(msg==WM_TIMER&&wp==FOREGROUND_TIMER){s->refreshAutomaticRuntime();return 0;}
    if(msg==WM_RESULT){auto r=(Result*)lp;
      if(wp==2){
        if(r->id==s->version&&s->awaitingSuggestion){
          s->awaitingSuggestion=false;
          if(s->keys.empty()&&!s->english&&s->suggestionsEnabled&&
             s->suggestionForeground==GetForegroundWindow()&&!r->entries.empty()){
            s->entries=std::move(r->entries);s->selected=0;s->showingSuggestion=true;s->show();s->trace("suggestion_shown");
          }else release(s->suggestionAnchor);
        }
      }else if(r->id==s->version&&!s->keys.empty()&&(!wp||!s->locked)){
        if(!wp||(r->status==L"model"&&!r->entries.empty()))s->entries=std::move(r->entries);
        s->status=r->status==L"model"?L"模型":r->status==L"dictionary"?L"词典":r->status;
        s->show();
        if(!s->entries.empty())s->trace(!wp?"dictionary_applied":r->status==L"model"?"model_applied":"model_fallback");
        if(!wp&&!s->locked&&s->entries.size()>1)s->scheduleRank(r->id);
      }delete r;return 0;
    }
    if(msg==WM_MOUSEACTIVATE)return MA_NOACTIVATE;
    if(msg==WM_LBUTTONDOWN){if(!s->entries.empty())s->locked=true;return 0;}
    if(msg==WM_LBUTTONUP){int row=(HIWORD(lp)-60)/30;if(HIWORD(lp)>=60&&row>=0&&row<5)s->choose((s->selected/5)*5+row);return 0;}
    if(msg==WM_CONTEXTMENU){
      s->dismissSuggestion();
      std::wstring savedKeys=s->keys,savedHistory=s->history;size_t savedCaret=s->caret;HWND foreground=GetForegroundWindow();
      s->inMenu=true;++s->version;s->edit(L"",true,true);s->keys.clear();s->entries.clear();ShowWindow(hwnd,SW_HIDE);
      HMENU menu=CreatePopupMenu();const wchar_t* names[]={L"z / zh",L"c / ch",L"s / sh",L"n / l",L"an / ang",L"en / eng"};
      for(unsigned i=0;i<6;++i)AppendMenu(menu,MF_STRING|((s->mask&(1<<i))?MF_CHECKED:0),100+i,names[i]);
      AppendMenu(menu,MF_SEPARATOR,0,nullptr);AppendMenu(menu,MF_STRING|(s->english?MF_CHECKED:0),201,L"英文模式");AppendMenu(menu,MF_STRING|(s->chinesePunct?MF_CHECKED:0),202,L"中文标点");AppendMenu(menu,MF_STRING,200,L"清空上下文");
      AppendMenu(menu,MF_STRING|(s->suggestionsEnabled?MF_CHECKED:0),203,L"上屏后联想");
      POINT p;GetCursorPos(&p);int id=TrackPopupMenu(menu,TPM_RETURNCMD|TPM_NONOTIFY,p.x,p.y,0,foreground,nullptr);DestroyMenu(menu);s->inMenu=false;
      if(GetForegroundWindow()==foreground){s->keys=savedKeys;s->caret=savedCaret;s->history=savedHistory;}
      if(id>=100&&id<106){s->mask^=1<<(id-100);s->trace("menu_fuzzy");}
      if(id==200){s->history.clear();s->trace("menu_clear_context");}
      if(id==201){if(!s->keys.empty()){++s->version;s->edit(s->keys,true);s->keys.clear();s->entries.clear();ShowWindow(hwnd,SW_HIDE);}s->english=!s->english;s->trace("menu_mode");}
      if(id==202){s->chinesePunct=!s->chinesePunct;s->trace("menu_punctuation");}
      if(id==203){s->suggestionsEnabled=!s->suggestionsEnabled;s->trace("menu_suggestion");}
      if(!s->keys.empty())s->changed();return 0;
    }
    if(msg==WM_PAINT){PAINTSTRUCT ps;HDC dc=BeginPaint(hwnd,&ps);RECT rc;GetClientRect(hwnd,&rc);FillRect(dc,&rc,(HBRUSH)(COLOR_WINDOW+1));auto old=SelectObject(dc,s->font);SetBkMode(dc,TRANSPARENT);
      std::wstring top=s->showingSuggestion?L"联想":s->keys.substr(0,s->caret)+L"|"+s->keys.substr(s->caret);TextOut(dc,10,4,top.c_str(),(int)top.size());
      std::wstring state=s->showingSuggestion?L"数字选词 · Esc 关闭":L"拼音  "+s->status+(s->chinesePunct?L"  中标":L"  英标")+(s->mask?L"  模糊音已启用":L"");TextOut(dc,10,32,state.c_str(),(int)state.size());
      size_t page=s->selected/5;for(size_t row=0;row<5;++row){size_t i=page*5+row;if(i>=s->entries.size())break;RECT line{4,60+(LONG)row*30,506,90+(LONG)row*30};
        if(i==s->selected){FillRect(dc,&line,(HBRUSH)(COLOR_HIGHLIGHT+1));SetTextColor(dc,GetSysColor(COLOR_HIGHLIGHTTEXT));}else SetTextColor(dc,GetSysColor(COLOR_WINDOWTEXT));
        std::wstring text=std::to_wstring(row+1)+L". "+s->entries[i].text;TextOut(dc,10,line.top+2,text.c_str(),(int)text.size());
      }SelectObject(dc,old);EndPaint(hwnd,&ps);return 0;
    }
    return DefWindowProc(hwnd,msg,wp,lp);
  }
  bool eat(WPARAM key){
    if(inMenu)return false;
    bool ctrl=(GetKeyState(VK_CONTROL)&0x8000)!=0,alt=(GetKeyState(VK_MENU)&0x8000)!=0;
    if(ctrl&&key==VK_SPACE)return !preservedReady;
    if(ctrl&&(GetKeyState(VK_SHIFT)&0x8000)&&(key=='P'||key==VK_BACK))return !preservedReady;
    if(ctrl&&alt&&key>='1'&&key<='6')return !preservedReady;
    if(ctrl||alt||(GetKeyState(VK_LWIN)&0x8000)||(GetKeyState(VK_RWIN)&0x8000))return false;
    if(key==VK_SHIFT)return true;
    if(english||(GetKeyState(VK_CAPITAL)&1))return false;
    if(showingSuggestion&&(key==VK_ESCAPE||suggestionNumber(key)))return true;
    if(key>='A'&&key<='Z')return true;
    if(key==VK_OEM_7&&!keys.empty())return true;
    if(!keys.empty()&&(key==VK_SPACE||key==VK_RETURN||key==VK_ESCAPE||key==VK_BACK||key==VK_DELETE||key==VK_LEFT||key==VK_RIGHT||key==VK_HOME||key==VK_END||key==VK_UP||key==VK_DOWN||key==VK_PRIOR||key==VK_NEXT||(key>='1'&&key<='5')||key==VK_OEM_MINUS||key==VK_OEM_PLUS))return true;
    bool punctuation=key==VK_OEM_COMMA||key==VK_OEM_PERIOD||key==VK_OEM_1||key==VK_OEM_2||key==VK_OEM_7||key==VK_OEM_4||key==VK_OEM_6||((GetKeyState(VK_SHIFT)&0x8000)&&(key=='1'||key=='9'||key=='0'));
    return (!keys.empty()||chinesePunct)&&punctuation;
  }
  bool suggestionNumber(WPARAM key)const{
    return !(GetKeyState(VK_SHIFT)&0x8000)&&key>='1'&&size_t(key-'1')<entries.size();
  }
public:
  Service(){++objects;}~Service(){Deactivate();--objects;}
  STDMETHODIMP QueryInterface(REFIID iid,void** pp)override{if(!pp)return E_POINTER;*pp=nullptr;
    if(iid==IID_IUnknown||iid==IID_ITfTextInputProcessor)*pp=static_cast<ITfTextInputProcessor*>(this);
    else if(iid==IID_ITfKeyEventSink)*pp=static_cast<ITfKeyEventSink*>(this);
    else if(iid==IID_ITfThreadMgrEventSink)*pp=static_cast<ITfThreadMgrEventSink*>(this);
    else if(iid==IID_ITfCompositionSink)*pp=static_cast<ITfCompositionSink*>(this);
    else return E_NOINTERFACE;AddRef();return S_OK;}
  STDMETHODIMP_(ULONG) AddRef()override{return ++refs;}
  STDMETHODIMP_(ULONG) Release()override{ULONG n=--refs;if(!n)delete this;return n;}
  STDMETHODIMP Activate(ITfThreadMgr* tm,TfClientId id)override{
    manager=tm;manager->AddRef();client=id;
    WNDCLASS wc{};wc.lpfnWndProc=wndproc;wc.hInstance=instance;wc.lpszClassName=L"ZhIME.Candidates";wc.hCursor=LoadCursor(nullptr,IDC_ARROW);RegisterClass(&wc);
    window=CreateWindowEx(WS_EX_TOPMOST|WS_EX_TOOLWINDOW|WS_EX_NOACTIVATE,wc.lpszClassName,L"ZhIME",WS_POPUP|WS_BORDER,100,100,510,216,nullptr,nullptr,instance,this);
    font=CreateFont(20,0,0,0,FW_NORMAL,FALSE,FALSE,FALSE,DEFAULT_CHARSET,0,0,CLEARTYPE_QUALITY,0,L"Microsoft YaHei");
    ITfKeystrokeMgr* km=nullptr;HRESULT hr=tm->QueryInterface(IID_ITfKeystrokeMgr,(void**)&km);if(SUCCEEDED(hr)){hr=km->AdviseKeyEventSink(id,this,TRUE);preservedReady=SUCCEEDED(hr);if(SUCCEEDED(hr))for(unsigned i=0;i<9;++i){auto k=shortcutKey(i);if(FAILED(km->PreserveKey(id,shortcutGuid(i),&k,L"ZhIME",5)))preservedReady=false;}km->Release();}if(FAILED(hr))return hr;trace(preservedReady?"activate_preserved":"activate_no_preserved");
    ITfSource* src=nullptr;if(SUCCEEDED(tm->QueryInterface(IID_ITfSource,(void**)&src))){src->AdviseSink(IID_ITfThreadMgrEventSink,static_cast<ITfThreadMgrEventSink*>(this),&threadCookie);src->Release();}
    stop=pendingDictionary=pendingRank=pendingSuggestion=false;
    dictionaryWorker=std::thread([this]{candidateLoop(false);});
    rankWorker=std::thread([this]{candidateLoop(true);});
    suggestionWorker=std::thread([this]{suggestionLoop();});
    findAutomaticRuntime();
    if(window&&!automaticRoot.empty())SetTimer(window,FOREGROUND_TIMER,750,nullptr);
    return S_OK;
  }
  STDMETHODIMP Deactivate()override{
    if(!manager)return S_OK;
    if(window)KillTimer(window,FOREGROUND_TIMER);
    if(automaticProcess){CloseHandle(automaticProcess);automaticProcess=nullptr;}
    {std::lock_guard<std::mutex> lock(mutex);stop=true;}cv.notify_all();
    if(dictionaryWorker.joinable())dictionaryWorker.join();
    if(rankWorker.joinable())rankWorker.join();
    if(suggestionWorker.joinable())suggestionWorker.join();
    clear(false);release(composition);release(context);
    ITfKeystrokeMgr* km=nullptr;if(SUCCEEDED(manager->QueryInterface(IID_ITfKeystrokeMgr,(void**)&km))){for(unsigned i=0;i<9;++i){auto k=shortcutKey(i);km->UnpreserveKey(shortcutGuid(i),&k);}km->UnadviseKeyEventSink(client);km->Release();}
    ITfSource* src=nullptr;if(threadCookie!=TF_INVALID_COOKIE&&SUCCEEDED(manager->QueryInterface(IID_ITfSource,(void**)&src))){src->UnadviseSink(threadCookie);src->Release();}threadCookie=TF_INVALID_COOKIE;
    if(window){MSG m;while(PeekMessage(&m,window,WM_RESULT,WM_RESULT,PM_REMOVE))delete (Result*)m.lParam;DestroyWindow(window);window=nullptr;}
    if(font){DeleteObject(font);font=nullptr;}release(manager);return S_OK;
  }
  HRESULT apply(TfEditCookie ec,const std::wstring& text,bool commit,bool cancel,unsigned long long v,bool predict,ITfRange* anchor){
    if(v!=version||!context)return S_FALSE;ITfRange* range=nullptr;
    if(composition)composition->GetRange(&range);
    else if(!cancel){TF_SELECTION sel{};ULONG fetched=0;if(FAILED(context->GetSelection(ec,TF_DEFAULT_SELECTION,1,&sel,&fetched))||!fetched)return E_FAIL;
      if(anchor){
        LONG start=0,end=0;
        if(FAILED(sel.range->CompareStart(ec,anchor,TF_ANCHOR_START,&start))||
           FAILED(sel.range->CompareEnd(ec,anchor,TF_ANCHOR_START,&end))||start||end){sel.range->Release();return S_FALSE;}
      }
      ITfContextComposition* cc=nullptr;if(SUCCEEDED(context->QueryInterface(IID_ITfContextComposition,(void**)&cc))){cc->StartComposition(ec,sel.range,this,&composition);cc->Release();}range=sel.range;
    }
    if(!range)return S_FALSE;
    HRESULT written=range->SetText(ec,0,text.c_str(),(LONG)text.size());
    if(FAILED(written)){range->Release();return written;}
    if(commit&&composition){auto comp=composition;composition=nullptr;comp->EndComposition(ec);comp->Release();}
    ITfRange* selection=nullptr;range->Clone(&selection);selection->Collapse(ec,TF_ANCHOR_START);LONG shifted=0;selection->ShiftEnd(ec,commit?(LONG)text.size():(LONG)std::min(caret,text.size()),&shifted,nullptr);selection->Collapse(ec,TF_ANCHOR_END);
    TF_SELECTION sel{selection,{TF_AE_NONE,FALSE}};context->SetSelection(ec,1,&sel);
    ITfContextView* view=nullptr;if((!commit||predict)&&SUCCEEDED(context->GetActiveView(&view))){RECT rect{};BOOL clipped=FALSE;if(SUCCEEDED(view->GetTextExt(ec,selection,&rect,&clipped)))SetWindowPos(window,HWND_TOPMOST,rect.left,rect.bottom+4,510,216,SWP_NOACTIVATE);view->Release();}
    if(commit&&!cancel){remember(text);if(predict&&v==version)suggestAfterCommit(selection);if(anchor)trace("suggestion_selected");}
    selection->Release();range->Release();return S_OK;
  }
  STDMETHODIMP OnSetFocus(BOOL focused)override{if(!focused&&!inMenu){dismissSuggestion();history.clear();}return S_OK;}
  STDMETHODIMP OnTestKeyDown(ITfContext* ctx,WPARAM key,LPARAM,BOOL* eaten)override{
    if(key!=VK_SHIFT)shiftOnly=false;
    if(awaitingSuggestion||showingSuggestion){
      bool command=showingSuggestion&&ctx==context&&(key==VK_ESCAPE||
        (suggestionNumber(key)&&!(GetKeyState(VK_CONTROL)&0x8000)&&!(GetKeyState(VK_MENU)&0x8000)));
      if(!command)dismissSuggestion();
    }
    *eaten=eat(key);return S_OK;
  }
  STDMETHODIMP OnTestKeyUp(ITfContext*,WPARAM key,LPARAM,BOOL* eaten)override{*eaten=key==VK_SHIFT;return S_OK;}
  STDMETHODIMP OnKeyDown(ITfContext* ctx,WPARAM key,LPARAM,BOOL* eaten)override{
    *eaten=eat(key);if(!*eaten){shiftOnly=false;return S_OK;}
    if(context!=ctx){clear();release(context);context=ctx;context->AddRef();}
    bool ctrl=(GetKeyState(VK_CONTROL)&0x8000)!=0,alt=(GetKeyState(VK_MENU)&0x8000)!=0,shift=(GetKeyState(VK_SHIFT)&0x8000)!=0;
    if(showingSuggestion&&!ctrl&&!alt){
      if(key==VK_ESCAPE){dismissSuggestion();return S_OK;}
      if(suggestionNumber(key)){choose(key-'1');return S_OK;}
    }
    dismissSuggestion();
    if(key==VK_SHIFT){shiftOnly=!ctrl&&!alt;return S_OK;}shiftOnly=false;
    if(ctrl&&key==VK_SPACE){if(!keys.empty()){++version;edit(keys,true);keys.clear();entries.clear();ShowWindow(window,SW_HIDE);}english=!english;trace("keydown_mode");return S_OK;}
    if(ctrl&&shift&&key==VK_BACK){history.clear();request();return S_OK;}
    if(ctrl&&shift&&key=='P'){chinesePunct=!chinesePunct;return S_OK;}
    if(ctrl&&alt&&key>='1'&&key<='6'){mask^=1<<(key-'1');request();return S_OK;}
    if(key>='A'&&key<='Z'){if(keys.size()<256){keys.insert(caret,1,(wchar_t)(key-'A'+'a'));++caret;changed();}return S_OK;}
    if(key==VK_OEM_7&&!shift&&!keys.empty()){keys.insert(caret,1,L'\'');++caret;changed();return S_OK;}
    if(key==VK_ESCAPE){clear();return S_OK;}
    if(key==VK_BACK||key==VK_DELETE){if(key==VK_BACK&&caret){keys.erase(--caret,1);}else if(key==VK_DELETE&&caret<keys.size())keys.erase(caret,1);changed();return S_OK;}
    if(key==VK_LEFT||key==VK_RIGHT||key==VK_HOME||key==VK_END){if(key==VK_LEFT&&caret)--caret;if(key==VK_RIGHT&&caret<keys.size())++caret;if(key==VK_HOME)caret=0;if(key==VK_END)caret=keys.size();edit(keys);show();return S_OK;}
    if(key==VK_UP||key==VK_DOWN||key==VK_PRIOR||key==VK_NEXT||key==VK_OEM_MINUS||key==VK_OEM_PLUS){if(!entries.empty()){locked=true;int delta=key==VK_UP?-1:key==VK_DOWN?1:(key==VK_PRIOR||key==VK_OEM_MINUS)?-5:5;selected=(size_t)std::max(0,std::min((int)entries.size()-1,(int)selected+delta));show();}return S_OK;}
    if(!shift&&key>='1'&&key<='5'){choose((selected/5)*5+key-'1');return S_OK;}
    if(key==VK_SPACE){choose(selected);return S_OK;}
    if(key==VK_RETURN){++version;edit(keys,true);keys.clear();caret=0;entries.clear();ShowWindow(window,SW_HIDE);return S_OK;}
    std::wstring punct;
    if(key==VK_OEM_COMMA)punct=chinesePunct?L"，":(shift?L"<":L",");
    else if(key==VK_OEM_PERIOD)punct=chinesePunct?L"。":(shift?L">":L".");
    else if(key==VK_OEM_1)punct=shift?L"：":L"；";
    else if(key==VK_OEM_2)punct=shift?L"？":L"、";
    else if(key==VK_OEM_4)punct=L"【";
    else if(key==VK_OEM_6)punct=L"】";
    else if(key=='1')punct=L"！";
    else if(key=='9')punct=L"（";
    else if(key=='0')punct=L"）";
    else if(key==VK_OEM_7){if(shift){punct=quoteOpen?L"“":L"”";quoteOpen=!quoteOpen;}else{punct=singleOpen?L"‘":L"’";singleOpen=!singleOpen;}}
    if(!chinesePunct){if(key==VK_OEM_1)punct=shift?L":":L";";else if(key==VK_OEM_2)punct=shift?L"?":L"/";else if(key==VK_OEM_4)punct=shift?L"{":L"[";else if(key==VK_OEM_6)punct=shift?L"}":L"]";else if(key=='1')punct=L"!";else if(key=='9')punct=L"(";else if(key=='0')punct=L")";else if(key==VK_OEM_7)punct=shift?L"\"":L"'";}
    if(!keys.empty()){
      if(!entries.empty()&&entries[selected].consumed==keys.size())choose(selected,false);
      else{++version;edit(keys,true);keys.clear();caret=0;entries.clear();ShowWindow(window,SW_HIDE);}
    }
    ++version;edit(punct,true);return S_OK;
  }
  STDMETHODIMP OnKeyUp(ITfContext*,WPARAM key,LPARAM,BOOL* eaten)override{*eaten=key==VK_SHIFT;if(key==VK_SHIFT&&shiftOnly){if(keys.empty())english=!english;shiftOnly=false;}return S_OK;}
  STDMETHODIMP OnPreservedKey(ITfContext* ctx,REFGUID guid,BOOL* eaten)override{
    *eaten=FALSE;for(unsigned i=0;i<9;++i){if(guid!=shortcutGuid(i))continue;
      if(context!=ctx){clear();release(context);context=ctx;if(context)context->AddRef();}
      if(i==0){if(!keys.empty()){++version;edit(keys,true);keys.clear();entries.clear();ShowWindow(window,SW_HIDE);}english=!english;trace("preserved_mode");}
      else if(i==1)chinesePunct=!chinesePunct;
      else if(i==2){history.clear();request();}
      else{mask^=1<<(i-3);request();}
      *eaten=TRUE;return S_OK;
    }return S_OK;
  }
  STDMETHODIMP OnInitDocumentMgr(ITfDocumentMgr*)override{return S_OK;}
  STDMETHODIMP OnUninitDocumentMgr(ITfDocumentMgr*)override{return S_OK;}
  STDMETHODIMP OnSetFocus(ITfDocumentMgr* focus,ITfDocumentMgr* previous)override{if(!inMenu&&focus!=previous){clear();release(context);}return S_OK;}
  STDMETHODIMP OnPushContext(ITfContext*)override{return S_OK;}
  STDMETHODIMP OnPopContext(ITfContext* ctx)override{if(!inMenu&&ctx==context){clear();release(context);}return S_OK;}
  STDMETHODIMP OnCompositionTerminated(TfEditCookie,ITfComposition* comp)override{if(comp==composition){release(composition);++version;keys.clear();entries.clear();if(window)ShowWindow(window,SW_HIDE);}return S_OK;}
};
Edit::Edit(Service* s,std::wstring t,bool c,bool x,unsigned long long v,bool p,ITfRange* a):owner(s),text(std::move(t)),commit(c),cancel(x),predict(p),version(v),anchor(a){owner->AddRef();if(anchor)anchor->AddRef();++objects;}
Edit::~Edit(){release(anchor);owner->Release();--objects;}
HRESULT Edit::DoEditSession(TfEditCookie ec){return owner->apply(ec,text,commit,cancel,version,predict,anchor);}

class Factory:public IClassFactory{
  std::atomic<ULONG> refs{1};
public:
  Factory(){++objects;}~Factory(){--objects;}
  STDMETHODIMP QueryInterface(REFIID iid,void** pp)override{if(!pp)return E_POINTER;*pp=nullptr;if(iid==IID_IUnknown||iid==IID_IClassFactory){*pp=static_cast<IClassFactory*>(this);AddRef();return S_OK;}return E_NOINTERFACE;}
  STDMETHODIMP_(ULONG) AddRef()override{return ++refs;}STDMETHODIMP_(ULONG) Release()override{ULONG n=--refs;if(!n)delete this;return n;}
  STDMETHODIMP CreateInstance(IUnknown* outer,REFIID iid,void** pp)override{if(outer)return CLASS_E_NOAGGREGATION;auto s=new Service;HRESULT hr=s->QueryInterface(iid,pp);s->Release();return hr;}
  STDMETHODIMP LockServer(BOOL lock)override{objects+=lock?1:-1;return S_OK;}
};
extern "C" BOOL WINAPI DllMain(HINSTANCE h,DWORD reason,LPVOID){if(reason==DLL_PROCESS_ATTACH){instance=h;DisableThreadLibraryCalls(h);}return TRUE;}
extern "C" HRESULT __stdcall DllGetClassObject(REFCLSID cls,REFIID iid,void** pp){if(cls!=CLSID_ZhIME)return CLASS_E_CLASSNOTAVAILABLE;auto f=new Factory;HRESULT hr=f->QueryInterface(iid,pp);f->Release();return hr;}
extern "C" HRESULT __stdcall DllCanUnloadNow(){return objects==0?S_OK:S_FALSE;}
static HRESULT registration(bool install){
  wchar_t guid[64],path[MAX_PATH];StringFromGUID2(CLSID_ZhIME,guid,64);GetModuleFileName(instance,path,MAX_PATH);
  auto note=[&](const char* step,HRESULT result){std::string text=std::string(step)+" "+std::to_string((unsigned long)result)+"\r\n";std::wstring log=std::wstring(path)+L".registration.log";HANDLE h=CreateFileW(log.c_str(),FILE_APPEND_DATA,FILE_SHARE_READ,nullptr,OPEN_ALWAYS,FILE_ATTRIBUTE_NORMAL,nullptr);if(h!=INVALID_HANDLE_VALUE){DWORD n;WriteFile(h,text.data(),(DWORD)text.size(),&n,nullptr);CloseHandle(h);}};
  std::wstring key=L"Software\\Classes\\CLSID\\"+std::wstring(guid);
  if(install){HKEY h;LONG error=RegCreateKeyEx(HKEY_CURRENT_USER,(key+L"\\InprocServer32").c_str(),0,nullptr,0,KEY_WRITE,nullptr,&h,nullptr);if(error!=ERROR_SUCCESS)return HRESULT_FROM_WIN32(error);
    RegSetValueEx(h,nullptr,0,REG_SZ,(BYTE*)path,(DWORD)(wcslen(path)+1)*sizeof(wchar_t));const wchar_t* apartment=L"Apartment";RegSetValueEx(h,L"ThreadingModel",0,REG_SZ,(BYTE*)apartment,20);RegCloseKey(h);
  }
  HRESULT init=CoInitializeEx(nullptr,COINIT_APARTMENTTHREADED);ITfInputProcessorProfiles* profiles=nullptr;HRESULT hr=CoCreateInstance(CLSID_TF_InputProcessorProfiles,nullptr,CLSCTX_INPROC_SERVER,IID_ITfInputProcessorProfiles,(void**)&profiles);
  note("create_profiles",hr);
  if(SUCCEEDED(hr)){
    if(install){hr=profiles->Register(CLSID_ZhIME);note("register",hr);if(SUCCEEDED(hr)){hr=profiles->AddLanguageProfile(CLSID_ZhIME,0x0804,PROFILE_ZhIME,L"ZhIME 自研全拼",(ULONG)wcslen(L"ZhIME 自研全拼"),path,(ULONG)wcslen(path),0);note("add_language",hr);}if(SUCCEEDED(hr))profiles->EnableLanguageProfile(CLSID_ZhIME,0x0804,PROFILE_ZhIME,TRUE);}
    else profiles->Unregister(CLSID_ZhIME);profiles->Release();
  }
  ITfCategoryMgr* categories=nullptr;if(SUCCEEDED(CoCreateInstance(CLSID_TF_CategoryMgr,nullptr,CLSCTX_INPROC_SERVER,IID_ITfCategoryMgr,(void**)&categories))){
    if(install)categories->RegisterCategory(CLSID_ZhIME,GUID_TFCAT_TIP_KEYBOARD,CLSID_ZhIME);else categories->UnregisterCategory(CLSID_ZhIME,GUID_TFCAT_TIP_KEYBOARD,CLSID_ZhIME);categories->Release();
  }
  if(!install)RegDeleteTree(HKEY_CURRENT_USER,key.c_str());if(SUCCEEDED(init))CoUninitialize();return hr;
}
extern "C" HRESULT __stdcall DllRegisterServer(){return registration(true);}
extern "C" HRESULT __stdcall DllUnregisterServer(){return registration(false);}
