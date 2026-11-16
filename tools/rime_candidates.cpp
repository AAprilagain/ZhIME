// Read one ASCII pinyin query per line, write up to 20 tab-separated Rime candidates.
// Usage: rime_candidates SHARED_DATA_DIR USER_DATA_DIR
#include <iostream>
#include <string>
#include <rime_api.h>
int main(int argc, char** argv) {
  if (argc != 3) return 2;
  RIME_STRUCT(RimeTraits, traits);
  traits.shared_data_dir = argv[1]; traits.user_data_dir = argv[2];
  traits.app_name = "rime.zhime_benchmark";
  auto api = rime_get_api(); api->setup(&traits); api->initialize(&traits);
  if (api->start_maintenance(True)) api->join_maintenance_thread();
  auto session = api->create_session();
  if (!session || !api->select_schema(session, "luna_pinyin")) return 3;
  api->set_option(session, "simplification", True);
  std::string line;
  while (std::getline(std::cin, line)) {
    api->clear_composition(session);
    for (char c : line) api->process_key(session, c, 0);
    RimeCandidateListIterator iter = {};
    bool first = true; int n = 0;
    if (api->candidate_list_begin(session, &iter)) {
      while (n < 20 && api->candidate_list_next(&iter)) {
        if (!first) std::cout << '\t';
        std::cout << iter.candidate.text; first = false; ++n;
      }
      api->candidate_list_end(&iter);
    }
    std::cout << std::endl;
  }
  api->destroy_session(session); api->finalize();
}
