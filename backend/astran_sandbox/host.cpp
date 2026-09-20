// Astran Luau sandbox host.
//
//   luau-sandbox --prelude prelude.luau [--timeout ms] [--mem MB] [file | -]
//
// Citeste scriptul (Luau, ca in Roblox Studio) din fisier sau stdin si il
// ruleaza izolat: fara io/require, cu limita de timp si de memorie.
// Scrie pe stdout cate un obiect JSON pe linie:
//   {"type":"print","msg":"..."}    {"type":"warn","msg":"..."}
//   {"type":"error","msg":"..."}    {"type":"ops","data":{...}}
//
// Exit code: 0 ok, 1 argumente/fisiere gresite, 2 eroare de compilare,
//            3 eroare interna la rulare.

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>

#include "lua.h"
#include "luacode.h"
#include "lualib.h"

using Clock = std::chrono::steady_clock;

static Clock::time_point g_deadline;
static size_t g_printBytes = 0;

static const size_t kMaxPrintBytes = 256 * 1024;    // total text din print/warn
static const size_t kMaxOpsBytes = 2 * 1024 * 1024; // rezultatul final
static const size_t kMaxSourceBytes = 200 * 1024;   // dimensiunea scriptului

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

// ---------- utilitare ----------

static bool readFile(const std::string& path, std::string& out)
{
    std::ifstream f(path, std::ios::binary);
    if (!f)
        return false;
    out.assign(std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>());
    return true;
}

// Compileaza si incarca un chunk pe stiva lui T. La eroare pune mesajul in err.
static bool loadChunk(lua_State* T, const std::string& src, const char* chunkName, std::string& err)
{
    size_t bcSize = 0;
    char* bytecode = luau_compile(src.data(), src.size(), nullptr, &bcSize);
    int rc = luau_load(T, chunkName, bytecode, bcSize, 0);
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

    for (int i = 1; i < argc; i++)
    {
        std::string a = argv[i];
        if (a == "--timeout" && i + 1 < argc)
            timeoutMs = std::atol(argv[++i]);
        else if (a == "--mem" && i + 1 < argc)
            memMB = (size_t)std::atol(argv[++i]);
        else if (a == "--prelude" && i + 1 < argc)
            preludePath = argv[++i];
        else
            path = a;
    }

    if (timeoutMs <= 0 || memMB == 0 || preludePath.empty())
    {
        std::fprintf(stderr, "usage: luau-sandbox --prelude file [--timeout ms] [--mem MB] [file|-]\n");
        return 1;
    }

    std::string prelude;
    if (!readFile(preludePath, prelude))
    {
        std::fprintf(stderr, "cannot read prelude %s\n", preludePath.c_str());
        return 1;
    }

    std::string source;
    if (path == "-")
    {
        source.assign(std::istreambuf_iterator<char>(std::cin), std::istreambuf_iterator<char>());
    }
    else if (!readFile(path, source))
    {
        std::fprintf(stderr, "cannot read %s\n", path.c_str());
        return 1;
    }

    if (source.size() > kMaxSourceBytes)
    {
        emit("error", "script too large");
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

    std::string err;

    // stiva lui T: [userFn]
    if (!loadChunk(T, source, "=script", err))
    {
        emit("error", err);
        lua_close(L);
        return 2;
    }

    // stiva lui T: [userFn, preludeFn]
    if (!loadChunk(T, prelude, "=prelude", err))
    {
        emit("error", "prelude: " + err);
        lua_close(L);
        return 3;
    }

    // apel: preludeFn(userFn, emit)
    lua_pushvalue(T, -2);
    lua_pushcfunction(T, luaEmit, "emit");

    g_deadline = Clock::now() + std::chrono::milliseconds(timeoutMs);

    int rc = 0;
    if (lua_pcall(T, 2, 0, 0) != 0)
    {
        const char* m = lua_tostring(T, -1);
        emit("error", m ? m : "runtime error");
        rc = 3;
    }

    lua_close(L);
    return rc;
}