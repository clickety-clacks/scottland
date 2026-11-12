-- Independent source inventory for the Omarchy binding audit test. This deliberately does not
-- load Scottland's Lua scanner: it captures the bindings produced by Omarchy's real setup.
local root = os.getenv("OMARCHY_PATH")
if not root or root == "" then error("OMARCHY_PATH is required") end
package.path = root .. "/?.lua;" .. root .. "/?/init.lua;" .. package.path

local order, bindings = {}, {}
local function normalize(keys)
  local mods, key = {}, ""
  for part in string.gmatch(tostring(keys or ""), "[^+]+") do
    local value = part:gsub("^%s+", ""):gsub("%s+$", "")
    local upper = string.upper(value)
    if upper == "SUPER" or upper == "SHIFT" or upper == "CTRL" or upper == "CONTROL" or upper == "ALT" then
      mods[#mods + 1] = (upper == "CONTROL") and "CTRL" or upper
    else
      key = value
    end
  end
  table.sort(mods)
  return table.concat(mods, " "), key
end

local function literal(value)
  local kind = type(value)
  if kind == "string" then return string.format("%q", value) end
  if kind == "number" or kind == "boolean" then return tostring(value) end
  if kind == "table" then
    local parts = {}
    for key, inner in pairs(value) do parts[#parts + 1] = tostring(key) .. "=" .. literal(inner) end
    table.sort(parts)
    return "{" .. table.concat(parts, ",") .. "}"
  end
  return "nil"
end

local function option_signature(options)
  local parts = {}
  for key, value in pairs(options) do
    if key ~= "description" then parts[#parts + 1] = tostring(key) .. "=" .. literal(value) end
  end
  table.sort(parts)
  return table.concat(parts, ",")
end

local function dispatcher(kind, action)
  return { __scottland = true, kind = kind, action = action }
end

local noop
local function noop_function() return noop end
noop = setmetatable({}, { __index = function() return noop end,
                          __call = function() return noop end })

local function dispatcher_proxy(path)
  return setmetatable({ path = path }, {
    __index = function(self, key) return dispatcher_proxy(self.path .. "." .. tostring(key)) end,
    __call = function(self, ...)
      local first = ...
      if self.path == "hl.dsp.exec_cmd" and type(first) == "string" then
        return dispatcher("exec", first)
      end
      local args = {}
      for index = 1, select("#", ...) do args[index] = literal(select(index, ...)) end
      return dispatcher("lua", self.path .. "(" .. table.concat(args, ",") .. ")")
    end,
  })
end

hl = setmetatable({
  dsp = dispatcher_proxy("hl.dsp"),
  bind = function(keys, action, options)
    options = options or {}
    local mods, key = normalize(keys)
    local release = options.release and "1" or "0"
    local id = mods .. "+" .. key .. (release == "1" and ":release" or "")
    local kind, argument, signature = "", "", ""
    if type(action) == "table" and action.__scottland then
      kind, argument = action.kind, action.action
    elseif type(action) == "string" then
      kind, argument = "exec", action
    elseif type(action) == "function" then
      kind = "function"
      local info = debug.getinfo(action, "S")
      signature = info.source .. ":" .. tostring(info.linedefined) .. ":" .. tostring(info.lastlinedefined)
    end
    local row = { mods, key, options.description or "", kind, argument,
                  (options["repeat"] or options.repeating) and "1" or "0",
                  release, options.locked and "1" or "0", options.mouse and "1" or "0",
                  signature, option_signature(options), "0", options.ignore_mods and "1" or "0" }
    if not bindings[id] then order[#order + 1] = id end
    bindings[id] = row
    return noop
  end,
  unbind = function(keys)
    local mods, key = normalize(keys)
    bindings[mods .. "+" .. key] = nil
    bindings[mods .. "+" .. key .. ":release"] = nil
    return noop
  end,
  get_config = function() return nil end,
}, { __index = function() return noop_function end })

_G.omarchy_preinstalled_bindings = true
_G.omarchy_default_bindings = true
require("default.hypr.helpers")
-- Include optional upstream bindings regardless of the packages installed in the CI image.
o.cmd_present = function() return true end
require("default.hypr.omarchy")

local printed = {}
for _, id in ipairs(order) do
  local row = bindings[id]
  if row and not printed[id] then
    printed[id] = true
    for index = 1, #row do row[index] = tostring(row[index]):gsub("[\t\n]", " ") end
    print(table.concat(row, "\t"))
  end
end
