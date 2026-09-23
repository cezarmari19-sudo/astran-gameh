-- Astran Luau prelude.
-- Rulat de host ca: prelude(userFn, emit, modules)
--   userFn  = scriptul "main" al utilizatorului (deja compilat)
--   emit    = functie a host-ului: emit("ops", json) / emit("error", text)
--   modules = tabel nume -> functie, cu celelalte fisiere (pentru require).
--             fisierul special "@assets" (daca exista) e JSON cu modelele
--             cumparate din Shop, pus la dispozitie prin Assets.load("id")
--
-- Scriptul ruleaza pe un ceas VIRTUAL: task.wait() nu asteapta cu adevarat,
-- doar avanseaza timpul si inregistreaza operatiile (create/set/destroy) cu
-- momentul la care s-au intamplat. Clientul le reda apoi in scena 3D.

local userFn, emit, modules = ...

-- ===== limite =====
local MAX_OPS = 5000       -- operatii inregistrate
local MAX_PARTS = 500      -- obiecte simultan in workspace
local MAX_CREATED = 5000   -- Instance.new total
local MAX_TIME = 60        -- secunde virtuale
local MAX_STEPS = 20000    -- reluari de thread-uri
local MAX_THREADS = 200    -- thread-uri in asteptare
local MAX_COORD = 10000
local MIN_WAIT = 1 / 60

-- ===== functii de baza captate local (scriptul nu le poate schimba) =====
local type, tostring, tonumber, pairs = type, tostring, tonumber, pairs
local setmetatable, getmetatable, error, rawequal, pcall = setmetatable, getmetatable, error, rawequal, pcall
local floor, abs, huge, sqrt = math.floor, math.abs, math.huge, math.sqrt
local sformat, sgsub, sbyte, ssub, smatch, sfind = string.format, string.gsub, string.byte, string.sub, string.match, string.find
local concat, tremove, tpack, tunpack = table.concat, table.remove, table.pack, table.unpack
local cocreate, coresume, coyield, corunning = coroutine.create, coroutine.resume, coroutine.yield, coroutine.running

-- ===== inregistrarea operatiilor =====
local ops = {}
local opCount = 0
local now = 0
local truncated = false

local function record(op)
    if opCount >= MAX_OPS then
        truncated = true
        return
    end
    opCount = opCount + 1
    op.t = now
    ops[opCount] = op
end

-- ===== JSON minimal =====
local ESC = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }

local function jsonString(s)
    s = sgsub(s, '[%c"\\]', function(c)
        return ESC[c] or sformat("\\u%04x", sbyte(c))
    end)
    return '"' .. s .. '"'
end

local function jsonNumber(n)
    if n ~= n or n == huge or n == -huge then
        return "0"
    end
    if n == floor(n) and abs(n) < 1e15 then
        return sformat("%d", n)
    end
    return sformat("%.6g", n)
end

local function jsonValue(v)
    local tv = type(v)
    if tv == "string" then
        return jsonString(v)
    elseif tv == "number" then
        return jsonNumber(v)
    elseif tv == "boolean" then
        return v and "true" or "false"
    end
    return "null"
end

local function jsonFlatMap(m)
    local parts = {}
    for k, v in pairs(m) do
        parts[#parts + 1] = jsonString(k) .. ":" .. jsonValue(v)
    end
    return "{" .. concat(parts, ",") .. "}"
end

-- ===== numere =====
local function num(v, default)
    v = tonumber(v)
    if v == nil or v ~= v then
        return default or 0
    end
    if v > MAX_COORD then
        return MAX_COORD
    end
    if v < -MAX_COORD then
        return -MAX_COORD
    end
    return v
end

-- ===== V3 =====
local V3 = {}

V3.__index = function(self, key)
    if key == "Magnitude" then
        return sqrt(self.X * self.X + self.Y * self.Y + self.Z * self.Z)
    end
    return V3[key]
end

function V3.new(x, y, z)
    return setmetatable({ X = num(x), Y = num(y), Z = num(z) }, V3)
end

V3.__add = function(a, b)
    return V3.new(a.X + b.X, a.Y + b.Y, a.Z + b.Z)
end

V3.__sub = function(a, b)
    return V3.new(a.X - b.X, a.Y - b.Y, a.Z - b.Z)
end

V3.__mul = function(a, b)
    if type(a) == "number" then
        return V3.new(a * b.X, a * b.Y, a * b.Z)
    end
    if type(b) == "number" then
        return V3.new(a.X * b, a.Y * b, a.Z * b)
    end
    return V3.new(a.X * b.X, a.Y * b.Y, a.Z * b.Z)
end

V3.__div = function(a, b)
    if type(b) == "number" then
        return V3.new(a.X / b, a.Y / b, a.Z / b)
    end
    return V3.new(a.X / b.X, a.Y / b.Y, a.Z / b.Z)
end

V3.__unm = function(a)
    return V3.new(-a.X, -a.Y, -a.Z)
end

V3.__eq = function(a, b)
    return a.X == b.X and a.Y == b.Y and a.Z == b.Z
end

V3.__tostring = function(v)
    return tostring(v.X) .. ", " .. tostring(v.Y) .. ", " .. tostring(v.Z)
end

function V3.Dot(a, b)
    return a.X * b.X + a.Y * b.Y + a.Z * b.Z
end

function V3.Lerp(a, b, t)
    t = tonumber(t) or 0
    return V3.new(a.X + (b.X - a.X) * t, a.Y + (b.Y - a.Y) * t, a.Z + (b.Z - a.Z) * t)
end

V3.zero = V3.new(0, 0, 0)
V3.one = V3.new(1, 1, 1)

-- ===== C3 =====
local C3 = {}
C3.__index = C3

local function clamp01(v)
    v = tonumber(v) or 0
    if v ~= v or v < 0 then
        return 0
    end
    if v > 1 then
        return 1
    end
    return v
end

function C3.new(r, g, b)
    return setmetatable({ R = clamp01(r), G = clamp01(g), B = clamp01(b) }, C3)
end

function C3.fromRGB(r, g, b)
    return C3.new((tonumber(r) or 0) / 255, (tonumber(g) or 0) / 255, (tonumber(b) or 0) / 255)
end

function C3.fromHex(hex)
    local h = smatch(tostring(hex), "^#?(%x%x%x%x%x%x)$")
    if not h then
        error("invalid hex color", 2)
    end
    return C3.fromRGB(tonumber(ssub(h, 1, 2), 16), tonumber(ssub(h, 3, 4), 16), tonumber(ssub(h, 5, 6), 16))
end

C3.__eq = function(a, b)
    return a.R == b.R and a.G == b.G and a.B == b.B
end

C3.__tostring = function(c)
    return tostring(c.R) .. ", " .. tostring(c.G) .. ", " .. tostring(c.B)
end

local function colorHex(c)
    return sformat("#%02x%02x%02x", floor(c.R * 255 + 0.5), floor(c.G * 255 + 0.5), floor(c.B * 255 + 0.5))
end

-- ===== Part =====
-- Shape (Roblox) -> tipul din scena aplicatiei
local SHAPES = { Block = "cube", Ball = "sphere", Cylinder = "cylinder", Cone = "cone", Pyramid = "pyramid" }

local DATA = setmetatable({}, { __mode = "k" }) -- proxy -> date
local PartMT = {}
local PartMethods = {}
local workspaceObj = nil
local children = {} -- parts din workspace, in ordinea aparitiei
local partCount = 0
local createdCount = 0

local function partCreateOp(d)
    return {
        op = "create",
        id = d.Id,
        type = SHAPES[d.Shape],
        x = d.Position.X,
        y = d.Position.Y,
        z = d.Position.Z,
        color = colorHex(d.Color),
        scale = d.Size.X,
        name = d.Name,
    }
end

local function setParent(self, d, parent)
    if d.Destroyed then
        error("The Parent property of " .. d.Name .. " is locked because it was destroyed", 3)
    end

    if rawequal(parent, workspaceObj) then
        if d.Alive then
            return
        end
        if partCount >= MAX_PARTS then
            error("too many parts in workspace (max " .. MAX_PARTS .. ")", 3)
        end
        d.Alive = true
        partCount = partCount + 1
        children[#children + 1] = self
        record(partCreateOp(d))
    elseif parent == nil then
        if not d.Alive then
            return
        end
        d.Alive = false
        partCount = partCount - 1
        for i = 1, #children do
            if rawequal(children[i], self) then
                tremove(children, i)
                break
            end
        end
        record({ op = "destroy", id = d.Id })
    else
        error("Parent must be workspace or nil", 3)
    end
end

PartMT.__index = function(self, key)
    local d = DATA[self]
    if key == "Parent" then
        if d.Alive then
            return workspaceObj
        end
        return nil
    elseif key == "Name" then
        return d.Name
    elseif key == "Position" then
        return d.Position
    elseif key == "Color" then
        return d.Color
    elseif key == "Size" then
        return d.Size
    elseif key == "Shape" then
        return d.Shape
    elseif key == "ClassName" then
        return "Part"
    end
    local method = PartMethods[key]
    if method == nil then
        error(tostring(key) .. " is not a valid member of Part", 2)
    end
    return method
end

PartMT.__newindex = function(self, key, value)
    local d = DATA[self]
    if key == "Parent" then
        setParent(self, d, value)
    elseif key == "Name" then
        d.Name = tostring(value)
    elseif key == "Position" then
        if getmetatable(value) ~= V3 then
            error("Position must be a Vector3", 2)
        end
        d.Position = value
        if d.Alive then
            record({ op = "set", id = d.Id, x = value.X, y = value.Y, z = value.Z })
        end
    elseif key == "Color" then
        if getmetatable(value) ~= C3 then
            error("Color must be a Color3", 2)
        end
        d.Color = value
        if d.Alive then
            record({ op = "set", id = d.Id, color = colorHex(value) })
        end
    elseif key == "Size" then
        -- aplicatia foloseste o scara uniforma: se foloseste Size.X (sau numarul dat)
        local s
        if type(value) == "number" then
            s = num(value, 1)
        elseif getmetatable(value) == V3 then
            s = value.X
        else
            error("Size must be a Vector3 or a number", 2)
        end
        d.Size = V3.new(s, s, s)
        if d.Alive then
            record({ op = "set", id = d.Id, scale = s })
        end
    elseif key == "Shape" then
        local shapeType = SHAPES[value]
        if not shapeType then
            error("invalid Shape (use Block, Ball, Cylinder, Cone or Pyramid)", 2)
        end
        d.Shape = value
        if d.Alive then
            record({ op = "set", id = d.Id, type = shapeType })
        end
    else
        error(tostring(key) .. " is not a valid member of Part", 2)
    end
end

PartMT.__tostring = function(self)
    return DATA[self].Name
end

local function newPart()
    createdCount = createdCount + 1
    local self = setmetatable({}, PartMT)
    DATA[self] = {
        Id = "s" .. createdCount,
        Name = "Part",
        Position = V3.new(0, 0, 0),
        Color = C3.new(0.64, 0.64, 0.64),
        Size = V3.new(1, 1, 1),
        Shape = "Block",
        Alive = false,
        Destroyed = false,
    }
    return self
end

function PartMethods.Destroy(self)
    local d = DATA[self]
    if d.Alive then
        setParent(self, d, nil)
    end
    d.Destroyed = true
end

function PartMethods.IsA(self, className)
    return className == "Part" or className == "BasePart" or className == "Instance"
end

function PartMethods.Clone(self)
    local d = DATA[self]
    if createdCount >= MAX_CREATED then
        error("too many instances created (max " .. MAX_CREATED .. ")", 2)
    end
    local copy = newPart()
    local cd = DATA[copy]
    cd.Name = d.Name
    cd.Position = d.Position
    cd.Color = d.Color
    cd.Size = d.Size
    cd.Shape = d.Shape
    return copy
end

-- ===== workspace si game =====
local WorkspaceMethods = {}

function WorkspaceMethods.GetChildren(self)
    local out = {}
    for i = 1, #children do
        out[i] = children[i]
    end
    return out
end

function WorkspaceMethods.FindFirstChild(self, name)
    for i = 1, #children do
        if DATA[children[i]].Name == name then
            return children[i]
        end
    end
    return nil
end

workspaceObj = setmetatable({}, {
    __index = function(self, key)
        if key == "Name" or key == "ClassName" then
            return "Workspace"
        end
        return WorkspaceMethods[key]
    end,
    __newindex = function()
        error("workspace is read-only", 2)
    end,
    __tostring = function()
        return "Workspace"
    end,
})

local GameMethods = {}

function GameMethods.GetService(self, name)
    if name == "Workspace" then
        return workspaceObj
    end
    error("service '" .. tostring(name) .. "' is not available", 2)
end

local gameObj = setmetatable({}, {
    __index = function(self, key)
        if key == "Workspace" then
            return workspaceObj
        elseif key == "Name" then
            return "Game"
        end
        return GameMethods[key]
    end,
    __newindex = function()
        error("game is read-only", 2)
    end,
    __tostring = function()
        return "Game"
    end,
})

local InstanceLib = {}

function InstanceLib.new(className)
    if className == "Part" then
        if createdCount >= MAX_CREATED then
            error("too many instances created (max " .. MAX_CREATED .. ")", 2)
        end
        return newPart()
    end
    error("unable to create an Instance of type '" .. tostring(className) .. "' (only Part is supported)", 2)
end

-- ===== scheduler (ceas virtual) =====
local pending = {}
local seq = 0
local steps = 0

local function schedule(co, wake, args)
    if #pending >= MAX_THREADS then
        error("too many scheduled threads (max " .. MAX_THREADS .. ")", 3)
    end
    seq = seq + 1
    pending[#pending + 1] = { co = co, wake = wake, seq = seq, args = args }
end

local function resume(co, args)
    if steps >= MAX_STEPS then
        truncated = true
        return
    end
    steps = steps + 1

    local ok, err
    if args then
        ok, err = coresume(co, tunpack(args, 1, args.n))
    else
        ok, err = coresume(co)
    end
    if not ok then
        emit("error", tostring(err))
    end
end

local taskLib = {}

function taskLib.wait(seconds)
    seconds = tonumber(seconds) or 0
    if seconds ~= seconds or seconds < MIN_WAIT then
        seconds = MIN_WAIT
    end
    schedule(corunning(), now + seconds)
    coyield()
    return seconds
end

function taskLib.spawn(f, ...)
    local co
    if type(f) == "thread" then
        co = f
    elseif type(f) == "function" then
        co = cocreate(f)
    else
        error("task.spawn expects a function or a thread", 2)
    end
    resume(co, tpack(...))
    return co
end

function taskLib.delay(seconds, f, ...)
    seconds = tonumber(seconds) or 0
    if seconds ~= seconds or seconds < 0 then
        seconds = 0
    end
    local co = cocreate(f)
    schedule(co, now + seconds, tpack(...))
    return co
end

function taskLib.defer(f, ...)
    return taskLib.delay(0, f, ...)
end

function taskLib.cancel(co)
    for i = #pending, 1, -1 do
        if rawequal(pending[i].co, co) then
            tremove(pending, i)
        end
    end
end

-- functii globale vechi (Roblox): wait / spawn / delay / time
function wait(seconds)
    return taskLib.wait(seconds)
end

function spawn(f, ...)
    return taskLib.spawn(f, ...)
end

function delay(seconds, f, ...)
    return taskLib.delay(seconds, f, ...)
end

function time()
    return now
end

-- ===== decodor JSON minimal (doar cat trebuie pentru "@assets") =====
-- Suporta obiecte, siruri, numere, true/false/null. Suficient pentru datele
-- trimise de server; nu e un parser JSON general si nu trebuie folosit ca atare.
local function jsonSkipSpace(s, i)
    while i <= #s do
        local c = ssub(s, i, i)
        if c ~= " " and c ~= "\t" and c ~= "\n" and c ~= "\r" then
            break
        end
        i = i + 1
    end
    return i
end

local jsonDecodeValue -- forward

local function jsonDecodeString(s, i)
    -- i puncteaza spre ghilimeaua de deschidere
    i = i + 1
    local out = {}
    while true do
        if i > #s then
            error("invalid JSON (unterminated string)", 0)
        end
        local c = ssub(s, i, i)
        if c == '"' then
            return concat(out), i + 1
        elseif c == "\\" then
            local e = ssub(s, i + 1, i + 1)
            if e == "n" then out[#out + 1] = "\n"
            elseif e == "t" then out[#out + 1] = "\t"
            elseif e == "r" then out[#out + 1] = "\r"
            elseif e == "u" then
                -- unicode escape: pastram doar codul ca text simplu, e suficient pentru id-uri/nume
                out[#out + 1] = "?"
                i = i + 4
            else
                out[#out + 1] = e
            end
            i = i + 2
        else
            out[#out + 1] = c
            i = i + 1
        end
    end
end

local function jsonDecodeNumber(s, i)
    local start = i
    while i <= #s and sfind(ssub(s, i, i), "[%d%.%-%+eE]") do
        i = i + 1
    end
    return tonumber(ssub(s, start, i - 1)), i
end

local function jsonDecodeObject(s, i)
    i = jsonSkipSpace(s, i + 1)
    local out = {}
    if ssub(s, i, i) == "}" then
        return out, i + 1
    end
    while true do
        i = jsonSkipSpace(s, i)
        if ssub(s, i, i) ~= '"' then
            error("invalid JSON (expected key)", 0)
        end
        local key
        key, i = jsonDecodeString(s, i)
        i = jsonSkipSpace(s, i)
        if ssub(s, i, i) ~= ":" then
            error("invalid JSON (expected ':')", 0)
        end
        i = jsonSkipSpace(s, i + 1)
        local value
        value, i = jsonDecodeValue(s, i)
        out[key] = value
        i = jsonSkipSpace(s, i)
        local c = ssub(s, i, i)
        if c == "," then
            i = jsonSkipSpace(s, i + 1)
        elseif c == "}" then
            return out, i + 1
        else
            error("invalid JSON (expected ',' or '}')", 0)
        end
    end
end

local function jsonDecodeArray(s, i)
    i = jsonSkipSpace(s, i + 1)
    local out = {}
    if ssub(s, i, i) == "]" then
        return out, i + 1
    end
    while true do
        local value
        value, i = jsonDecodeValue(s, i)
        out[#out + 1] = value
        i = jsonSkipSpace(s, i)
        local c = ssub(s, i, i)
        if c == "," then
            i = jsonSkipSpace(s, i + 1)
        elseif c == "]" then
            return out, i + 1
        else
            error("invalid JSON (expected ',' or ']')", 0)
        end
    end
end

jsonDecodeValue = function(s, i)
    i = jsonSkipSpace(s, i)
    local c = ssub(s, i, i)
    if c == '"' then
        return jsonDecodeString(s, i)
    elseif c == "{" then
        return jsonDecodeObject(s, i)
    elseif c == "[" then
        return jsonDecodeArray(s, i)
    elseif c == "t" and ssub(s, i, i + 3) == "true" then
        return true, i + 4
    elseif c == "f" and ssub(s, i, i + 4) == "false" then
        return false, i + 5
    elseif c == "n" and ssub(s, i, i + 3) == "null" then
        return nil, i + 4
    elseif sfind(c, "[%d%-]") then
        return jsonDecodeNumber(s, i)
    end
    error("invalid JSON", 0)
end

local function jsonDecode(s)
    local value = jsonDecodeValue(s, 1)
    return value
end

-- ===== Assets.load("id"): modele cumparate din Shop =====
-- Datele vin de la server, prin fisierul special "@assets" (JSON), niciodata
-- generate de scriptul jocului, deci sunt de incredere ca forma (dar tot
-- verificate mai jos, defensiv, in caz ca formatul se schimba pe viitor).
local assetsById = nil -- populat lazy, doar daca scriptul chiar foloseste Assets

local function loadAssetsTable()
    if assetsById ~= nil then
        return assetsById
    end
    assetsById = {}
    local raw = modules["@assets"]
    if raw == nil then
        return assetsById
    end

    local ok, text = pcall(raw)
    if not ok or type(text) ~= "string" then
        return assetsById
    end

    local ok2, decoded = pcall(jsonDecode, text)
    if not ok2 or type(decoded) ~= "table" then
        return assetsById
    end

    for _, entry in pairs(decoded) do
        if type(entry) == "table" and type(entry.id) == "string" and type(entry.object) == "table" then
            local o = entry.object
            if type(o.type) == "string" and SHAPES[o.type == "Block" and "Block" or o.type] ~= nil then
                -- accepta fie numele intern (cube/sphere/...) fie numele Roblox (Block/Ball/...)
            end
            assetsById[entry.id] = entry
        end
    end
    return assetsById
end

local AssetsLib = {}

function AssetsLib.load(id)
    if type(id) ~= "string" then
        error("Assets.load expects the asset id as a string", 2)
    end
    local table_ = loadAssetsTable()
    local entry = table_[id]
    if entry == nil then
        error("asset '" .. id .. "' was not found (buy it from the Shop first, or check the id)", 2)
    end

    local o = entry.object
    local shapeName = o.type
    -- accepta ambele forme: "cube" (intern) sau "Block" (stil Roblox)
    local ROBLOX_NAME = { cube = "Block", sphere = "Ball", cylinder = "Cylinder", cone = "Cone", pyramid = "Pyramid" }
    if SHAPES[shapeName] == nil and ROBLOX_NAME[shapeName] ~= nil then
        shapeName = ROBLOX_NAME[shapeName]
    end
    if SHAPES[shapeName] == nil then
        shapeName = "Block"
    end

    if createdCount >= MAX_CREATED then
        error("too many instances created (max " .. MAX_CREATED .. ")", 2)
    end
    local part = newPart()
    local d = DATA[part]
    d.Name = type(entry.name) == "string" and entry.name or "Part"
    d.Shape = shapeName
    if type(o.color) == "string" then
        local okc, c = pcall(C3.fromHex, o.color)
        if okc then
            d.Color = c
        end
    end
    if type(o.scale) == "number" then
        local s = num(o.scale, 1)
        d.Size = V3.new(s, s, s)
    end
    return part
end

-- ===== require("nume") pentru celelalte fisiere ale jocului =====
local loadedModules = {}
local loadingModules = {}

function require(name)
    if type(name) ~= "string" then
        error("require expects the name of a script, e.g. require(\"utils\")", 2)
    end

    local cached = loadedModules[name]
    if cached ~= nil then
        return cached.value
    end

    local fn = modules[name]
    if fn == nil then
        error("script '" .. name .. "' was not found (require uses the file name, e.g. require(\"utils\"))", 2)
    end
    if loadingModules[name] then
        error("cyclic require: '" .. name .. "' is already loading", 2)
    end

    loadingModules[name] = true
    local ok, result = pcall(fn)
    loadingModules[name] = nil
    if not ok then
        error(result, 0)
    end

    if result == nil then
        result = true
    end
    loadedModules[name] = { value = result }
    return result
end

-- ===== globale vizibile scriptului =====
Instance = InstanceLib
Vector3 = V3
Color3 = C3
task = taskLib
workspace = workspaceObj
game = gameObj
Assets = AssetsLib

-- ===== rulare =====
local function main()
    resume(cocreate(userFn))

    while #pending > 0 do
        if steps >= MAX_STEPS then
            truncated = true
            break
        end

        -- alege thread-ul cu cel mai mic moment de trezire (la egalitate, primul programat)
        local best = 1
        for i = 2, #pending do
            local a, b = pending[i], pending[best]
            if a.wake < b.wake or (a.wake == b.wake and a.seq < b.seq) then
                best = i
            end
        end

        local item = tremove(pending, best)
        if item.wake > MAX_TIME then
            truncated = true
            break
        end

        now = item.wake
        resume(item.co, item.args)
    end
end

local function finish()
    local parts = {}
    for i = 1, opCount do
        parts[i] = jsonFlatMap(ops[i])
    end
    emit(
        "ops",
        '{"ops":[' .. concat(parts, ",") .. '],"duration":' .. jsonNumber(now) .. ',"truncated":' .. (truncated and "true" or "false") .. "}"
    )
end

main()
finish()