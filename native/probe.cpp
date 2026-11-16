// Registration-independent lifecycle test of the actual COM DLL and TSF sinks.
#include <windows.h>
#include <msctf.h>
#include <iostream>
int wmain(int argc,wchar_t** argv){
 if(argc<2)return 2;HRESULT hr=CoInitializeEx(nullptr,COINIT_APARTMENTTHREADED);if(FAILED(hr))return 3;
 HMODULE dll=LoadLibraryW(argv[1]);if(!dll)return 4;
 auto get=(HRESULT(__stdcall*)(REFCLSID,REFIID,void**))GetProcAddress(dll,"DllGetClassObject");
 auto unload=(HRESULT(__stdcall*)())GetProcAddress(dll,"DllCanUnloadNow");
 const CLSID cls={0xc8470792,0xf77d,0x4f13,{0x98,0xa4,0xb2,0x6c,0x7c,0x5e,0x61,0x39}};
 IClassFactory* factory=nullptr;hr=get(cls,IID_IClassFactory,(void**)&factory);if(FAILED(hr))return 5;
 ITfTextInputProcessor* service=nullptr;hr=factory->CreateInstance(nullptr,IID_ITfTextInputProcessor,(void**)&service);factory->Release();if(FAILED(hr))return 6;
 if(argc>2&&std::wstring(argv[2])==L"--factory-only"){
   ITfKeyEventSink* sink=nullptr;hr=service->QueryInterface(IID_ITfKeyEventSink,(void**)&sink);if(FAILED(hr))return 10;sink->Release();service->Release();
   if(unload()!=S_OK)return 11;FreeLibrary(dll);CoUninitialize();std::cout<<"COM factory, sink interface and unload passed (no TSF activation claimed)"<<std::endl;return 0;
 }
 ITfThreadMgr* manager=nullptr;hr=CoCreateInstance(CLSID_TF_ThreadMgr,nullptr,CLSCTX_INPROC_SERVER,IID_ITfThreadMgr,(void**)&manager);if(FAILED(hr))return 7;
 TfClientId client=TF_CLIENTID_NULL;hr=manager->Activate(&client);std::cout<<"ThreadMgr Activate hr="<<std::hex<<hr<<" client="<<client<<std::endl;if(FAILED(hr))return 8;
 ITfClientId* ids=nullptr;if(SUCCEEDED(manager->QueryInterface(IID_ITfClientId,(void**)&ids))){TfClientId tip=TF_CLIENTID_NULL;HRESULT mapped=ids->GetClientId(cls,&tip);std::cout<<"TIP client mapping hr="<<std::hex<<mapped<<" client="<<tip<<std::endl;if(SUCCEEDED(mapped))client=tip;ids->Release();}
 hr=service->Activate(manager,client);if(FAILED(hr)){std::cerr<<"Activate failed "<<std::hex<<hr<<std::endl;service->Deactivate();service->Release();manager->Deactivate();manager->Release();FreeLibrary(dll);CoUninitialize();return 9;}
 ITfKeyEventSink* key=nullptr;hr=service->QueryInterface(IID_ITfKeyEventSink,(void**)&key);if(FAILED(hr))return 10;key->Release();
 service->Deactivate();service->Release();manager->Deactivate();manager->Release();
 if(unload()!=S_OK){std::cerr<<"DLL still has outstanding objects"<<std::endl;return 11;}
 FreeLibrary(dll);CoUninitialize();std::cout<<"COM factory, TSF activation/deactivation and unload passed"<<std::endl;return 0;
}
