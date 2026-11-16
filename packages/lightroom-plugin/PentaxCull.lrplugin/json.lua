-- Minimal JSON decoder (objects, arrays, strings, numbers, true/false/null).
-- Enough for ratings.json; Lightroom does not provide one.
local json = {}

local function err(s, i, m) error(('invalid JSON (position %d): %s'):format(i, m), 0) end

local function ws(s, i)
  return s:find('[^ \t\r\n]', i) or #s + 1
end

local decode_value

local escapes = { ['"'] = '"', ['\\'] = '\\', ['/'] = '/', b = '\b', f = '\f', n = '\n', r = '\r', t = '\t' }

local function utf8char(cp)
  if cp < 0x80 then return string.char(cp)
  elseif cp < 0x800 then return string.char(0xC0 + math.floor(cp / 0x40), 0x80 + cp % 0x40)
  elseif cp < 0x10000 then
    return string.char(0xE0 + math.floor(cp / 0x1000), 0x80 + math.floor(cp / 0x40) % 0x40, 0x80 + cp % 0x40)
  else
    return string.char(0xF0 + math.floor(cp / 0x40000), 0x80 + math.floor(cp / 0x1000) % 0x40,
      0x80 + math.floor(cp / 0x40) % 0x40, 0x80 + cp % 0x40)
  end
end

local function decode_string(s, i)
  local out, j = {}, i + 1
  while true do
    local c = s:sub(j, j)
    if c == '' then err(s, j, 'unterminated string') end
    if c == '"' then return table.concat(out), j + 1 end
    if c == '\\' then
      local e = s:sub(j + 1, j + 1)
      if e == 'u' then
        local cp = tonumber(s:sub(j + 2, j + 5), 16)
        if not cp then err(s, j, 'invalid \\u escape') end
        j = j + 6
        -- characters beyond U+FFFF come as a UTF-16 surrogate pair: \uD83D\uDCF7 -> U+1F4F7
        local lo = s:sub(j, j + 1) == '\\u' and tonumber(s:sub(j + 2, j + 5), 16)
        if cp >= 0xD800 and cp <= 0xDBFF and lo and lo >= 0xDC00 and lo <= 0xDFFF then
          cp = 0x10000 + (cp - 0xD800) * 0x400 + (lo - 0xDC00); j = j + 6
        end
        out[#out + 1] = utf8char(cp)
      elseif escapes[e] then
        out[#out + 1] = escapes[e]; j = j + 2
      else err(s, j, 'invalid escape') end
    else
      out[#out + 1] = c; j = j + 1
    end
  end
end

function decode_value(s, i)
  i = ws(s, i)
  local c = s:sub(i, i)
  if c == '{' then
    local obj = {}
    i = ws(s, i + 1)
    if s:sub(i, i) == '}' then return obj, i + 1 end
    while true do
      if s:sub(i, i) ~= '"' then err(s, i, 'key expected') end
      local k; k, i = decode_string(s, i)
      i = ws(s, i)
      if s:sub(i, i) ~= ':' then err(s, i, '":" expected') end
      obj[k], i = decode_value(s, i + 1)
      i = ws(s, i)
      local d = s:sub(i, i)
      if d == '}' then return obj, i + 1 end
      if d ~= ',' then err(s, i, '"," or "}" expected') end
      i = ws(s, i + 1)
    end
  elseif c == '[' then
    local arr = {}
    i = ws(s, i + 1)
    if s:sub(i, i) == ']' then return arr, i + 1 end
    while true do
      arr[#arr + 1], i = decode_value(s, i)
      i = ws(s, i)
      local d = s:sub(i, i)
      if d == ']' then return arr, i + 1 end
      if d ~= ',' then err(s, i, '"," or "]" expected') end
      i = i + 1
    end
  elseif c == '"' then
    return decode_string(s, i)
  elseif s:sub(i, i + 3) == 'true' then return true, i + 4
  elseif s:sub(i, i + 4) == 'false' then return false, i + 5
  elseif s:sub(i, i + 3) == 'null' then return nil, i + 4
  else
    local num = s:match('^-?%d+%.?%d*[eE]?[-+]?%d*', i)
    if not num or num == '' then err(s, i, 'unexpected value') end
    return tonumber(num), i + #num
  end
end

function json.decode(s)
  local v, i = decode_value(s, 1)
  return v
end

return json
