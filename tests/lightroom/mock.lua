-- Minimal Lightroom SDK stand-in: runs ApplyRatings.lua against fake catalog photos (tests/test_lightroom.py).
-- Usage (from the plugin folder): luajit mock.lua <ratings.json>
local ratings_file = arg[1]
local photos = {
  { name = "A.DNG", dt = "2026-09-18T15:00:00", rating = nil },   -- -> 5
  { name = "B.DNG", dt = "2026-09-18T15:01:00", rating = 4 },     -- cleared -> no star
  { name = "C.DNG", dt = "2026-09-18T15:02:00", rating = nil },   -- cleared, already no star
  { name = "D.DNG", dt = "2026-09-18T15:03:00", rating = 3 },     -- already right
  { name = "E.DNG", dt = "2026-09-18T15:04:00", rating = 2 },     -- invalid rating 9: untouched
}
for i, p in ipairs(photos) do
  p.t = i
  function p:getFormattedMetadata(k) return self.name end
  function p:getRawMetadata(k) if k == 'dateTimeOriginal' then return self.t end return self.rating end
  function p:setRawMetadata(k, v) self.rating = v end
end
function LOC(s, ...)
  local args, text = { ... }, s:match('=(.*)$')
  return (text:gsub('%^(%d)', function(n) return tostring(args[tonumber(n)]) end):gsub('%^n', '\n'))
end
local mods = {
  LrApplication = { activeCatalog = function() return {
    getTargetPhotos = function() return photos end,
    withWriteAccessDo = function(self, name, f) print('UNDO STEP: ' .. name); f() end } end },
  LrDate = { timeToUserFormat = function(t) return photos[t].dt end },
  LrDialogs = {
    attachErrorDialogToFunctionContext = function() end,
    runOpenPanel = function() return { ratings_file } end,
    message = function(a, b) print('MESSAGE: ' .. a .. '\n' .. tostring(b)) end,
    confirm = function(a, b) print('CONFIRM: ' .. a .. '\n' .. b); return 'ok' end,
    showBezel = function(s) print('BEZEL: ' .. s) end },
  LrFunctionContext = { callWithContext = function(_, f) f({}) end },
  LrPathUtils = { parent = function(p) return p:match('(.*)/') end },
  LrPrefs = { prefsForPlugin = function() return {} end },
  LrProgressScope = function() return { setPortionComplete = function() end, done = function() end } end,
  LrTasks = { startAsyncTask = function(f) f() end },
}
function import(n) return mods[n] end
package.path = './?.lua;' .. package.path
dofile('ApplyRatings.lua')
for _, p in ipairs(photos) do print('RESULT ' .. p.name .. ' ' .. tostring(p.rating)) end
