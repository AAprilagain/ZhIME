// Rime baseline only: run pinned Windows Rime DLL in an isolated profile.
#include <windows.h>
#include <iostream>
#include <fstream>
#include <string>
#include "rime_api.h"
int wmain(int argc,wchar_t** argv){
 if(argc!=4)return 2;
 SetDllDirectoryW(argv[1]);std::wstring dllpath=std::wstring(argv[1])+L"/rime.dll";
 HMODULE lib=LoadLibraryW(dllpath.c_str());if(!lib)return 3;
 auto get=(RimeApi*(*)())GetProcAddress(lib,"rime_get_api");if(!get)return 4;auto api=get();
 auto utf8=[](const std::wstring& s){int n=WideCharToMultiByte(CP_UTF8,0,s.data(),(int)s.size(),nullptr,0,nullptr,nullptr);std::string x(n,0);WideCharToMultiByte(CP_UTF8,0,s.data(),(int)s.size(),x.data(),n,nullptr,nullptr);return x;};
 std::string shared=utf8(std::wstring(argv[1])+L"/data"),user=utf8(argv[2]);
 RIME_STRUCT(RimeTraits,traits);traits.shared_data_dir=shared.c_str();traits.user_data_dir=user.c_str();traits.app_name="rime.zhime_v2_baseline";traits.min_log_level=2;
 api->setup(&traits);api->initialize(&traits);if(api->start_maintenance(True))api->join_maintenance_thread();
 auto session=api->create_session();std::string line;
 while(std::getline(std::cin,line)){
  auto tab=line.find('\t');if(tab==std::string::npos)return 5;
  std::string schema=utf8(argv[3])+line.substr(0,tab),keys=line.substr(tab+1);
  char current[256]={};api->get_current_schema(session,current,sizeof(current));
  if(schema!=current && !api->select_schema(session,schema.c_str()))return 6;
  api->set_option(session,"ascii_mode",False);api->set_option(session,"simplification",True);
  api->set_property(session,"benchmark_export","0");api->clear_composition(session);DeleteFileW((std::wstring(argv[2])+L"/pool.tsv").c_str());
  for(size_t i=0;i<keys.size();++i){if(i+1==keys.size())api->set_property(session,"benchmark_export","1");api->process_key(session,keys[i],0);}
  RimeCandidateListIterator it{};int count=0;
  if(api->candidate_list_begin(session,&it)){while(count<20&&api->candidate_list_next(&it))++count;api->candidate_list_end(&it);}
  std::ifstream pool(std::wstring(argv[2])+L"/pool.tsv",std::ios::binary);std::string row;bool first=true;
  while(std::getline(pool,row)){if(!first)std::cout<<'\t';std::cout<<row;first=false;}
  std::cout<<std::endl;
 }
 api->destroy_session(session);api->finalize();return 0;
}
