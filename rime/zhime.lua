-- Socket or local spool transport; failures preserve the original candidate order.
local M = {}
local ok, socket = pcall(require, "socket")
local nonce = tostring({}):gsub('[^%w]', '')
local serial = 0
local function hex(s)
  return (s:gsub('.', function(c) return string.format('%02x', string.byte(c)) end))
end
local function request_spool(line)
  local base = os.getenv('LOCALAPPDATA')
  if not base then return nil, '环境变量' end
  local folder = base .. '\\ZhIME\\spool\\'
  local config = io.open(rime_api.get_user_data_dir() .. '/zhime-spool-path.txt', 'r')
  if config then
    local custom = config:read('*l'); config:close()
    if custom and custom ~= '' then folder = custom .. '/' end
  end
  local ready = io.open(folder .. 'ready', 'r')
  if not ready then return nil, '服务标记不可读' end
  local stamp = tonumber(ready:read('*a')); ready:close()
  if not stamp or math.abs(os.time() - stamp) > 2 then return nil, '服务标记过期' end
  serial = serial + 1
  local name = folder .. nonce .. '_' .. serial
  local f = io.open(name .. '.tmp', 'wb')
  if not f then return nil, '请求文件不可写' end
  f:write(line); f:close()
  if not os.rename(name .. '.tmp', name .. '.req') then os.remove(name .. '.tmp'); return nil, '请求文件无法提交' end
  local deadline = os.clock() + 0.15
  repeat
    local result = io.open(name .. '.res', 'rb')
    if result then
      local answer = result:read('*l'); result:close(); os.remove(name .. '.res'); return answer
    end
  until os.clock() >= deadline
  os.remove(name .. '.req')
  return nil, '请求超时'
end

function M.init(env)
  env.history = ''
  env.reset_connection = env.engine.context.option_update_notifier:connect(function(_, name)
    if name == 'zhime_reset' then env.history = '' end
  end)
  env.connection = env.engine.context.commit_notifier:connect(function(ctx)
    local text = ctx:get_commit_text()
    if text and text ~= '' then
      env.history = env.history .. text
      -- Trim at UTF-8 codepoint boundaries, retaining at most 128 codepoints.
      local chars = {}
      for c in env.history:gmatch('[%z\1-\127\194-\244][\128-\191]*') do chars[#chars + 1] = c end
      env.history = table.concat(chars, '', math.max(1, #chars - 127))
    end
  end)
end

function M.fini(env)
  if env.connection then env.connection:disconnect() end
  if env.reset_connection then env.reset_connection:disconnect() end
end

function M.func(input, env)
  local candidates = {}
  local iter, state, control = input:iter()
  for cand in iter, state, control do
    control = cand
    candidates[#candidates + 1] = cand
    if #candidates == 20 then break end
  end
  local ordered = nil
  local reason = nil
  if #candidates > 1 then
    local parts = {'R1', hex(env.history or '')}
    for _, cand in ipairs(candidates) do parts[#parts + 1] = hex(cand.text) end
    local request = table.concat(parts, '\t') .. '\n'
    local line = nil
    if ok then
    local client = socket.tcp()
    if client then
      client:settimeout(0.15)
      if client:connect('127.0.0.1', 18765) then
        local sent = client:send(request)
        line = sent and client:receive('*l')
      end
      client:close()
    end
    end
    if not line then line, reason = request_spool(request) end
    if line then
      local order, seen = {}, {}
      for n in line:gmatch('%d+') do
        local i = tonumber(n)
        if i < 1 or i > #candidates or seen[i] then order = {}; break end
        seen[i] = true; order[#order + 1] = candidates[i]
      end
      if #order == #candidates then ordered = order end
    end
  end
  for i, cand in ipairs(ordered or candidates) do
    if i == 1 and #candidates > 1 then
      local status = ordered and ' [模型]' or (' [模型:' .. (reason or '响应无效') .. ']')
      local success, marked = pcall(ShadowCandidate, cand, cand.type, cand.text, (cand.comment or '') .. status)
      if success and marked then cand = marked end
    end
    yield(cand)
  end
  for cand in iter, state, control do yield(cand) end
end
return M
