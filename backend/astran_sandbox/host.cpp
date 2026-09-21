// Astran Luau sandbox host (multi-fisier).
//
//   luau-sandbox --prelude prelude.luau [--timeout ms] [--mem MB] [--entry main] [file | -]
//
// Intrare (stdin sau fisier), unul din formate:
//   1) un bundle de fisiere:   @@FILE <nume> <nr_octeti>\n<continut>\n ...
//   2) cod Luau simplu (tratat ca un singur fisier numit "main")
//
// Fisierul "entry" (implicit "main") ruleaza primul; celelalte pot fi incarcate
// din script cu require("nume"). Toate ruleaza izolat: fara io/os.execute,
// cu limita de timp si de memorie.
//
// Scrie pe stdout cate un obiect JSON pe linie:
//   {"type":"print","msg":"..."}    {"type":"warn","msg":"..."}
//   {"type":"error","msg":"..."}    {"type":"ops","data":{...}}
//
// Exit code: 0 ok, 1 argumente/fisiere gresite, 2 eroare de compilare/intrare,
//            3 eroare interna la rulare.

#include <cctype>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <iterator>
#include <set>
#include <string>
#include <vector>

#include "lua.h"
#include "luacode.h"
#include "lualib.h"

using Clock = std::chrono::steady_clock;

static Clock::time_point g_deadline;
static size_t g_printBytes = 0;

static const size_t kMaxPrintBytes = 256 * 1024;    // total text din print/warn
static const size_t kMaxOpsBytes = 2 * 1024 * 1024; // rezultatul final
static const size_t kMaxSourceBytes = 400 * 1024;   // toate fisierele la un loc
static const size_t kMaxFiles = 32;
static const size_t kMaxNameLen = 64;

struct SourceFile
{
    std::string name;
    std::string source;
};

// ---------- output JSON ----------

static std::string jsonEscape(const std::string& s)
{
    std::string o;
    for (unsigned char c : s)
    {
        switch (c)
        {
        case '"': o += "\\\""; break;
        case '\\': o += "\\\\"; break;
        case '\n': o += "\\n"; break;
        case '\r': o += "\\r"; break;
        case '\t': o += "\\t"; break;
        default:
            if (c < 0x20)
            {
                char b[8];
                std::snprintf(b, sizeof b, "\\u%04x", c);
                o += b;
            }
            else
            {
                o += (char)c;
            }
        }
    }
    return o;
}

static void emit(const char* type, const std::string& msg)
{
    std::printf("{\"type\":\"%s\",\"msg\":\"%s\"}\n", type, jsonEscape(msg).c_str());
    std::fflush(stdout);
}

// ---------- functii expuse scriptului ----------

static int logImpl(lua_State* L, const char* type)
{
    int n = lua_gettop(L);
    std::string out;
    for (int i = 1; i <= n; i++)
    {
        size_t len = 0;
        const char* s = luaL_tolstring(L, i, &len);
        if (i > 1)
            out += '\t';
        out.append(s, len);
        lua_pop(L, 1);
    }

    g_printBytes += out.size();
    if (g_printBytes > kMaxPrintBytes)
        luaL_error(L, "output limit exceeded");

    emit(type, out);
    return 0;
}

static int luaPrint(lua_State* L) { return logImpl(L, "print"); }
static int luaWarn(lua_State* L) { return logImpl(L, "warn"); }

// emit(kind, payload) e dat doar preludiului (nu e global), ca scriptul
// utilizatorului sa nu poata falsifica rezultatul.
static int luaEmit(lua_State* L)
{
    const char* kind = luaL_checkstring(L, 1);
    size_t len = 0;
    const char* payload = luaL_checklstring(L, 2, &len);

    if (std::strcmp(kind, "ops") == 0)
    {
        if (len > kMaxOpsBytes)
            luaL_error(L, "result too large");
        std::fputs("{\"type\":\"ops\",\"data\":", stdout);
        std::fwrite(payload, 1, len, stdout);
        std::fputs("}\n", stdout);
        std::fflush(stdout);
    }
    else if (std::strcmp(kind, "error") == 0)
    {
        emit("error", std::string(payload, len));
    }
    return 0;
}

// ---------- limita de timp ----------

static void onInterrupt(lua_State* L, int gc)
{
    if (gc >= 0)
        return;
    if (Clock::now() > g_deadline)
        luaL_error(L, "script timed out");
}

// ---------- limita de memorie ----------

struct MemState
{
    size_t used = 0;
    size_t limit = 0;
};

static void* allocator(void* ud, void* ptr, size_t osize, size_t nsize)
{
    MemState* m = static_cast<MemState*>(ud);
    size_t old = ptr ? osize : 0; // cand ptr e null, osize e doar un tag

    if (nsize == 0)
    {
        if (ptr)
        {
            m->used -= old;
            std::free(ptr);
        }
        return nullptr;
    }

    if (m->used - old + nsize > m->limit)
        return nullptr; // Luau va raporta "not enough memory"

    void* p = std::realloc(ptr, nsize);
    if (p)
        m->used = m->used - old + nsize;
    return p;
}

// ---------- intrare: bundle de fisiere ----------

static bool validName(const std::string& n)
{
    if (n.empty() || n.size() > kMaxNameLen)
        return false;
    if (n.front() == '/' || n.back() == '/')
        return false;
    for (size_t i = 0; i < n.size(); i++)
    {
        unsigned char c = (unsigned char)n[i];
        bool ok = std::isalnum(c) != 0 || c == '_' || c == '-' || c == '/';
        if (!ok)
            return false;
        if (c == '/' && i + 1 < n.size() && n[i + 1] == '/')
            return false;
    }
    return true;
}

static bool parseBundle(const std::string& in, std::vector<SourceFile>& files, std::string& err)
{
    size_t pos = 0;
    while (pos < in.size())
    {
        if (in[pos] == '\n')
        {
            pos++;
            continue;
        }

        if (in.compare(pos, 7, "@@FILE ") != 0)
        {
            err = "invalid script bundle";
            return false;
        }

        size_t eol = in.find('\n', pos);
        if (eol == std::string::npos)
        {
            err = "invalid script bundle";
            return false;
        }

        std::string header = in.substr(pos + 7, eol - (pos + 7));
        size_t sp = header.rfind(' ');
        if (sp == std::string::npos)
        {
            err = "invalid script bundle";
            return false;
        }

        std::string name = header.substr(0, sp);
        unsigned long n = std::strtoul(header.c_str() + sp + 1, nullptr, 10);

        pos = eol + 1;
        if (n > in.size() - pos)
        {
            err = "truncated script bundle";
            return false;
        }

        files.push_back({name, in.substr(pos, n)});
        pos += n;
    }
    return true;
}

static bool parseInput(const std::string& in, std::vector<SourceFile>& files, std::string& err)
{
    if (in.compare(0, 7, "@@FILE ") == 0)
    {
        if (!parseBundle(in, files, err))
            return false;
    }
    else
    {
        files.push_back({"main", in});
    }

    if (files.empty())
    {
        err = "no scripts";
        return false;
    }
    if (files.size() > kMaxFiles)
    {
        err = "too many scripts";
        return false;
    }

    std::set<std::string> seen;
    size_t total = 0;
    for (const SourceFile& f : files)
    {
        if (!validName(f.name))
        {
            err = "invalid script name: " + f.name;
            return false;
        }
        if (!seen.insert(f.name).second)
        {
            err = "duplicate script name: " + f.name;
            return false;
        }
        total += f.source.size();
    }
    if (total > kMaxSourceBytes)
    {
        err = "scripts too large";
        return false;
    }
    return true;
}

// ---------- utilitare ----------

static bool readFile(const std::string& path, std::string& out)
{
    std::ifstream f(path, std::ios::binary);
    if (!f)
        return false;
    out.assign(std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>());
    return true;
}

// Compileaza si incarca un chunk pe stiva lui T. La eroare pune mesajul in err
// si nu lasa nimic pe stiva.
static bool loadChunk(lua_State* T, const std::string& src, const std::string& chunkName, std::string& err)
{
    size_t bcSize = 0;
    char* bytecode = luau_compile(src.data(), src.size(), nullptr, &bcSize);
    int rc = luau_load(T, chunkName.c_str(), bytecode, bcSize, 0);
    std::free(bytecode);

    if (rc != 0)
    {
        const char* m = lua_tostring(T, -1);
        err = m ? m : "compile error";
        lua_pop(T, 1);
        return false;
    }
    return true;
}

int main(int argc, char** argv)
{
    long timeoutMs = 2000;
    size_t memMB = 64;
    std::string path = "-";
    std::string preludePath;
    std::string entry = "main";

    for (int i = 1; i < argc; i++)
    {
        std::string a = argv[i];
        if (a == "--timeout" && i + 1 < argc)
            timeoutMs = std::atol(argv[++i]);
        else if (a == "--mem" && i + 1 < argc)
            memMB = (size_t)std::atol(argv[++i]);
        else if (a == "--prelude" && i + 1 < argc)
            preludePath = argv[++i];
        else if (a == "--entry" && i + 1 < argc)
            entry = argv[++i];
        else
            path = a;
    }

    if (timeoutMs <= 0 || memMB == 0 || preludePath.empty())
    {
        std::fprintf(stderr, "usage: luau-sandbox --prelude file [--timeout ms] [--mem MB] [--entry name] [file|-]\n");
        return 1;
    }

    std::string prelude;
    if (!readFile(preludePath, prelude))
    {
        std::fprintf(stderr, "cannot read prelude %s\n", preludePath.c_str());
        return 1;
    }

    std::string input;
    if (path == "-")
    {
        input.assign(std::istreambuf_iterator<char>(std::cin), std::istreambuf_iterator<char>());
    }
    else if (!readFile(path, input))
    {
        std::fprintf(stderr, "cannot read %s\n", path.c_str());
        return 1;
    }

    std::vector<SourceFile> files;
    std::string err;
    if (!parseInput(input, files, err))
    {
        emit("error", err);
        return 2;
    }

    size_t entryIndex = files.size();
    for (size_t i = 0; i < files.size(); i++)
    {
        if (files[i].name == entry)
            entryIndex = i;
    }
    if (entryIndex == files.size())
    {
        emit("error", "missing entry script '" + entry + "'");
        return 2;
    }

    MemState mem;
    mem.limit = memMB * 1024 * 1024;

    lua_State* L = lua_newstate(allocator, &mem);
    if (!L)
    {
        emit("error", "cannot create VM");
        return 3;
    }

    luaL_openlibs(L); // Luau nu are io/require; os are doar clock/time/date

    // Functii vizibile scriptului. Se inregistreaza INAINTE de luaL_sandbox.
    lua_pushcfunction(L, luaPrint, "print");
    lua_setglobal(L, "print");
    lua_pushcfunction(L, luaWarn, "warn");
    lua_setglobal(L, "warn");

    luaL_sandbox(L); // bibliotecile devin read-only, ca in Roblox

    lua_State* T = lua_newthread(L);
    luaL_sandboxthread(T); // scriptul primeste propriile globale, izolate

    lua_callbacks(L)->interrupt = onInterrupt;

    // stiva lui T: [preludeFn]
    if (!loadChunk(T, prelude, "=prelude", err))
    {
        emit("error", "prelude: " + err);
        lua_close(L);
        return 3;
    }

    bool failed = false;

    // stiva lui T: [preludeFn, entryFn]
    if (!loadChunk(T, files[entryIndex].source, "=" + files[entryIndex].name, err))
    {
        emit("error", err);
        failed = true;
    }

    // stiva lui T: [preludeFn, entryFn, emit, modules]
    lua_pushcfunction(T, luaEmit, "emit");
    lua_createtable(T, 0, (int)files.size());

    // toate celelalte fisiere devin module pentru require("nume")
    for (size_t i = 0; i < files.size(); i++)
    {
        if (i == entryIndex)
            continue;
        if (!loadChunk(T, files[i].source, "=" + files[i].name, err))
        {
            emit("error", err);
            failed = true;
            continue;
        }
        lua_setfield(T, -2, files[i].name.c_str());
    }

    if (failed)
    {
        lua_close(L);
        return 2;
    }

    g_deadline = Clock::now() + std::chrono::milliseconds(timeoutMs);

    int rc = 0;
    if (lua_pcall(T, 3, 0, 0) != 0)
    {
        const char* m = lua_tostring(T, -1);
        emit("error", m ? m : "runtime error");
        rc = 3;
    }

    lua_close(L);
    return rc;
}