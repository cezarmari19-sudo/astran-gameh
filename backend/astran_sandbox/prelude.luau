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

-- ===== JSON minimal (encode) =====
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

-- ===== V3 (devine Vector3 pentru script) =====
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

-- ===== C3 (devine Color3 pentru script) =====
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