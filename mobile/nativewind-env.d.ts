/// <reference types="nativewind/types" />

// Metro resolves the Tailwind entry stylesheet; TypeScript needs to be told
// a bare `import './global.css'` is a legitimate side-effect import.
declare module '*.css';
