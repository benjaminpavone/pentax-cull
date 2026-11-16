-- Applies the ratings of a ratings.json file (Mac viewer or Android app) to the targeted photos of the catalog.
-- Matching: file name + shooting date; falls back to the date alone when it is unique (photo renamed at import).
-- A rating of 0 is a rating cleared in the viewer: the photo goes back to no star (docs/ratings-format.md).
-- Texts: LOC() with English defaults; translations in TranslatedStrings_<lang>.txt.

local LrApplication = import 'LrApplication'
local LrDate = import 'LrDate'
local LrDialogs = import 'LrDialogs'
local LrFunctionContext = import 'LrFunctionContext'
local LrPathUtils = import 'LrPathUtils'
local LrPrefs = import 'LrPrefs'
local LrProgressScope = import 'LrProgressScope'
local LrTasks = import 'LrTasks'

local json = require 'json'

local function readFile(path)
  local f = io.open(path, 'r')
  if not f then return nil end
  local txt = f:read('*a')
  f:close()
  return txt
end

local function basename(key)
  return (key:match('([^/\\]+)$') or key):upper()
end

LrTasks.startAsyncTask(function()
  LrFunctionContext.callWithContext('PentaxCull.apply', function(context)
    LrDialogs.attachErrorDialogToFunctionContext(context)
    local prefs = LrPrefs.prefsForPlugin()

    -- 1. choose ratings.json
    local sel = LrDialogs.runOpenPanel {
      title = LOC '$$$/PentaxCull/ChooseFile=Choose the ratings file (ratings.json)',
      canChooseFiles = true, canChooseDirectories = false, allowsMultipleSelection = false,
      allowedFileTypes = { 'json' },
      initialDirectory = prefs.lastDir,
    }
    if not sel or not sel[1] then return end
    local path = sel[1]
    prefs.lastDir = LrPathUtils.parent(path)

    local txt = readFile(path)
    if not txt then
      return LrDialogs.message('Pentax Cull', LOC('$$$/PentaxCull/CannotRead=Cannot read ^1', path), 'critical')
    end
    local ok, data = pcall(json.decode, txt)
    if not ok or type(data) ~= 'table' then
      return LrDialogs.message('Pentax Cull', LOC('$$$/PentaxCull/BadFile=Unreadable ratings file: ^1', tostring(data)), 'critical')
    end

    -- 2. index the ratings by file name and by date
    local byName, byDate, nRatings = {}, {}, 0
    for key, v in pairs(data) do
      local r = type(v) == 'table' and tonumber(v.rating)
      if r and r >= 0 and r <= 5 and r == math.floor(r) then
        if r > 0 then nRatings = nRatings + 1 end
        local e = { key = key, rating = tonumber(v.rating), datetime = v.datetime }
        local n = basename(key)
        byName[n] = byName[n] or {}
        table.insert(byName[n], e)
        if v.datetime then
          byDate[v.datetime] = byDate[v.datetime] or {}
          table.insert(byDate[v.datetime], e)
        end
      end
    end

    -- 3. targeted photos (the selection, or the whole filmstrip when nothing is selected)
    local catalog = LrApplication.activeCatalog()
    local photos = catalog:getTargetPhotos()

    local plan, stats = {}, { byName = 0, byDate = 0, same = 0, mismatch = 0, notFound = 0 }
    local example
    local progress = LrProgressScope { title = LOC '$$$/PentaxCull/Analysing=Pentax Cull: analysing', functionContext = context }
    for i, photo in ipairs(photos) do
      progress:setPortionComplete(i, #photos)
      local name = (photo:getFormattedMetadata('fileName') or ''):upper()
      local t = photo:getRawMetadata('dateTimeOriginal')
      local dt = t and LrDate.timeToUserFormat(t, '%Y-%m-%dT%H:%M:%S') or nil

      local match
      for _, c in ipairs(byName[name] or {}) do
        if not c.datetime or not dt or c.datetime == dt then match = c; break end
      end
      if match then
        stats.byName = stats.byName + 1
      elseif byName[name] then
        stats.mismatch = stats.mismatch + 1      -- same name but another date: another photo
        example = example or ('%s: Lightroom %s / file %s'):format(name, tostring(dt), tostring(byName[name][1].datetime))
      elseif dt and byDate[dt] and #byDate[dt] == 1 then
        match = byDate[dt][1]                     -- photo renamed at import
        stats.byDate = stats.byDate + 1
      else
        stats.notFound = stats.notFound + 1
      end

      if match then
        local current = photo:getRawMetadata('rating') or 0
        if current == match.rating then
          stats.same = stats.same + 1
        else
          table.insert(plan, { photo = photo, rating = match.rating, current = current })
        end
      end
    end
    progress:done()

    -- 4. confirmation
    local changed, cleared = 0, 0
    for _, p in ipairs(plan) do
      if p.rating == 0 then cleared = cleared + 1 elseif p.current ~= 0 then changed = changed + 1 end
    end
    local report = LOC('$$$/PentaxCull/Report=^1 targeted photo(s), ^2 rating(s) in the file.^n^n'
      .. 'Found by name: ^3^nFound by date (renamed): ^4^nAlready at the right rating: ^5^n'
      .. 'Same name but another date (ignored): ^6^nNo rating in the file: ^7',
      #photos, nRatings, stats.byName, stats.byDate, stats.same, stats.mismatch, stats.notFound)
    if example then
      report = report .. '\n\n' .. LOC('$$$/PentaxCull/DateExample=Example of a different date:^n^1', example)
    end

    if #plan == 0 then
      return LrDialogs.message(LOC '$$$/PentaxCull/NothingToDo=Pentax Cull: nothing to change', report, 'info')
    end

    local answer = LrDialogs.confirm(
      LOC('$$$/PentaxCull/ApplyQ=Apply ^1 rating(s)?', #plan),
      report .. '\n\n' .. LOC('$$$/PentaxCull/Replaced=Including ^1 photo(s) that already had another rating (it will be replaced). Can be undone with Cmd+Z.', changed)
        .. (cleared > 0 and ('\n' .. LOC('$$$/PentaxCull/Cleared=Including ^1 rating(s) cleared in the viewer: back to no star.', cleared)) or ''),
      LOC '$$$/PentaxCull/Apply=Apply', LOC '$$$/PentaxCull/Cancel=Cancel')
    if answer ~= 'ok' then return end

    -- 5. write
    catalog:withWriteAccessDo(LOC '$$$/PentaxCull/Undo=Phone ratings (Pentax Cull)', function()
      for _, p in ipairs(plan) do
        p.photo:setRawMetadata('rating', p.rating > 0 and p.rating or nil)   -- nil = no star
      end
    end)
    LrDialogs.showBezel(LOC('$$$/PentaxCull/Done=^1 rating(s) applied', #plan))
  end)
end)
