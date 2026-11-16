local M = {}
function M.func(key, env)
  if key:repr() == 'Control+Shift+BackSpace' then
    local ctx = env.engine.context
    ctx:set_option('zhime_reset', not ctx:get_option('zhime_reset'))
    return 1
  end
  return 2
end
return M
