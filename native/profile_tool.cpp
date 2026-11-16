#include <windows.h>
#include <msctf.h>
#include <fstream>
#include <iostream>
static const CLSID CLSID_ZhIME={0xc8470792,0xf77d,0x4f13,{0x98,0xa4,0xb2,0x6c,0x7c,0x5e,0x61,0x39}};
static const GUID PROFILE_ZhIME={0x50e39d92,0x7271,0x437d,{0x9e,0x5c,0xda,0x51,0xa9,0x8a,0xc1,0xd2}};
int wmain(int argc,wchar_t** argv){
 if(argc!=3)return 2;CoInitializeEx(nullptr,COINIT_APARTMENTTHREADED);
 ITfInputProcessorProfileMgr* pm=nullptr;HRESULT hr=CoCreateInstance(CLSID_TF_InputProcessorProfiles,nullptr,CLSCTX_INPROC_SERVER,IID_ITfInputProcessorProfileMgr,(void**)&pm);if(FAILED(hr))return 3;
 if(std::wstring(argv[1])==L"activate"){
   TF_INPUTPROCESSORPROFILE old{};hr=pm->GetActiveProfile(GUID_TFCAT_TIP_KEYBOARD,&old);if(FAILED(hr))return 4;
   std::ofstream file(argv[2],std::ios::binary);file.write((char*)&old,sizeof(old));file.close();
   hr=pm->ActivateProfile(TF_PROFILETYPE_INPUTPROCESSOR,0x0804,CLSID_ZhIME,PROFILE_ZhIME,nullptr,TF_IPPMF_FORSESSION);
 }else{
   TF_INPUTPROCESSORPROFILE old{};std::ifstream file(argv[2],std::ios::binary);file.read((char*)&old,sizeof(old));if(!file)return 5;
   hr=pm->ActivateProfile(old.dwProfileType,old.langid,old.clsid,old.guidProfile,old.hkl,TF_IPPMF_FORSESSION);
 }
 std::cout<<"profile operation HRESULT="<<std::hex<<hr<<std::endl;pm->Release();CoUninitialize();return FAILED(hr)?6:0;
}
