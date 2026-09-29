/// <reference types="vite/client" />

/**
 * 这里只声明 Vite 注入的客户端类型。
 * 刻意不写 `declare module '*.vue'` 通配垫片：vue-tsc 能直接解析 .vue 真实类型，
 * 通配垫片会把 props / emits 全部退化成 any，等于关掉模板类型检查。
 */
