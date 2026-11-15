// Doar pentru teste (node --import ./src/sandbox/register-alias.mjs ...): face ca importurile
// "@/src/..." din runtime.ts sa se rezolve ca in Metro/TS (paths: "@/*" -> "./*").
import { register } from "node:module";
register("./alias-loader.mjs", import.meta.url);