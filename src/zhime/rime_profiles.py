"""Prepare isolated A/B profiles with exactly shared decoding/fuzzy settings."""
import argparse
import json
import shutil
from pathlib import Path
from .engine import FUZZY

LUA='''local M = {}
function M.func(input, env)
  if env.engine.context:get_property('benchmark_export') ~= '1' then
    for c in input:iter() do yield(c) end
    return
  end
  local list = {}
  local iter, state, control = input:iter()
  for c in iter, state, control do
    control=c; list[#list+1]=c
    if #list>=20 then break end
  end
  local f=io.open(rime_api.get_user_data_dir() .. '/pool.tsv','wb')
  if f then
    for _,c in ipairs(list) do f:write(c.text, '\\t', tostring(c._end), '\\n') end
    f:close()
  end
  for _,c in ipairs(list) do yield(c) end
  for c in iter,state,control do yield(c) end
end
return M
'''
def prepare(root, shared, abbreviated=False):
    root=Path(root);root.mkdir(parents=True,exist_ok=True);(root/'lua').mkdir(exist_ok=True)
    (root/'lua/benchmark_export.lua').write_text(LUA,encoding='utf-8')
    shutil.copy(shared,root/'zhime_shared.dict.yaml')
    masks=[0] if abbreviated else [0,63,1,2,4,8,16,32]
    schemas=[]
    for arm,dictionary in [('a','luna_pinyin'),('b','zhime_shared')]:
      for mask in masks:
        schema=f'bench_{arm}_{mask}';schemas.append(schema)
        algebra=['derive/^([nl])ve$/$1ue/']
        if abbreviated:
            algebra.extend(['abbrev/^([a-z]).+$/$1/', 'abbrev/^([zcs]h).+$/$1/'])
        for i,(name,(a,b,part)) in enumerate(FUZZY.items()):
            if not mask&(1<<i):continue
            if part=='initial':algebra.extend([f'derive/^{a}(.*)$/{b}$1/',f'derive/^{b}(.*)$/{a}$1/'])
            else:algebra.extend([f'derive/^(.*){a}$/$1{b}/',f'derive/^(.*){b}$/$1{a}/'])
        text=f'''schema:
  schema_id: {schema}
  name: {schema}
  version: '1'
switches:
  - name: ascii_mode
    reset: 0
  - name: simplification
    reset: 1
engine:
  processors: [ascii_composer, speller, selector, navigator, express_editor]
  segmentors: [abc_segmentor, fallback_segmentor]
  translators: [script_translator]
  filters: [simplifier, uniquifier, 'lua_filter@*benchmark_export']
speller:
  alphabet: abcdefghijklmnopqrstuvwxyz
  delimiter: " '"
  algebra: {json.dumps(algebra)}
translator:
  dictionary: {dictionary}
  prism: {schema}
  enable_user_dict: false
  enable_completion: false
  enable_sentence: true
menu:
  page_size: 5
'''
        (root/(schema+'.schema.yaml')).write_text(text,encoding='utf-8')
    (root/'default.custom.yaml').write_text('patch:\n  schema_list:\n'+''.join('    - schema: '+s+'\n' for s in schemas),encoding='utf-8')
    print('prepared',len(schemas),'isolated schemas at',root)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--shared',required=True);a=p.parse_args();prepare(a.root,a.shared)
